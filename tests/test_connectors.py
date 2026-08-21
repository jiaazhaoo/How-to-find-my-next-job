from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from career import triage
from career.cards import Card, Evidence, cluster, validate
from career.connectors import REGISTRY, read_provenance, write_items
from career.connectors.base import StagedItem, source_type_of
from career.connectors.claude_code import ClaudeCodeConnector
from career.ingest import Document, staged_source_type
from career.redact import excise_topics


def session_file(dirpath: Path, records: list[dict], name="s1.jsonl") -> Path:
    path = dirpath / name
    path.write_text("\n".join(json.dumps(r) for r in records), "utf-8")
    return path


def user(text, **kw):
    return {"type": "user", "timestamp": "2026-01-01T10:00:00Z", "cwd": "/home/me/proj",
            "gitBranch": "main", "message": {"role": "user", "content": text}, **kw}


def tool_result_turn():
    return {"type": "user", "timestamp": "2026-01-01T10:01:00Z",
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "content": "x" * 5000}]}}


def assistant(text, tools=(), thinking=False):
    blocks = []
    if thinking:
        blocks.append({"type": "thinking", "thinking": "internal"})
    if text:
        blocks.append({"type": "text", "text": text})
    blocks += [{"type": "tool_use", "name": t, "input": {}} for t in tools]
    return {"type": "assistant", "timestamp": "2026-01-01T10:02:00Z",
            "message": {"role": "assistant", "content": blocks}}


class TestClaudeCodeConnector(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.c = ClaudeCodeConnector()

    def parse(self, records, **conf):
        session_file(self.dir, records)
        return self.c.fetch({"roots": [str(self.dir)], **conf})

    def test_human_turns_are_kept_verbatim_and_tool_noise_dropped(self):
        prompt = "把这个迁移脚本改成幂等的，之前回滚过一次，" + "细节很多。" * 30
        items = self.parse([user(prompt), tool_result_turn(),
                            assistant("好的，我先看一下。", tools=["Bash", "Edit"], thinking=True)])
        self.assertEqual(len(items), 1)
        text = items[0].text
        self.assertIn(prompt, text)
        self.assertNotIn("x" * 100, text, "tool_result payloads must not reach the transcript")
        self.assertNotIn("internal", text, "thinking blocks are the model's, not evidence")
        self.assertIn("Bashx1", text, "tool traffic survives only as a tally")
        self.assertEqual(items[0].meta["human_turns"], 1)

    def test_sidechain_traffic_is_excluded(self):
        long_prompt = "我需要你解释一下这里的取舍，" * 20
        items = self.parse([user(long_prompt),
                            user("subagent chatter " * 40, isSidechain=True)])
        self.assertEqual(items[0].meta["human_turns"], 1)
        self.assertNotIn("subagent chatter", items[0].text)

    def test_trivial_sessions_are_skipped(self):
        self.assertEqual(self.parse([user("fix typo"), assistant("done")]), [])

    def test_assistant_prose_is_stubbed(self):
        items = self.parse([user("请解释一下这个设计的取舍，" * 20), assistant("A" * 4000)])
        self.assertIn("chars]", items[0].text)
        self.assertLess(items[0].text.count("A"), 4000)

    def test_pushback_and_repo_metadata(self):
        items = self.parse([user("不对，这里应该是幂等的，你搞错了方向，" * 10)])
        meta = items[0].meta
        self.assertGreaterEqual(meta["pushback"], 2)
        self.assertEqual(meta["repo"], "proj")
        self.assertEqual(meta["branch"], "main")


class TestStaging(unittest.TestCase):
    def test_write_items_produces_files_and_provenance(self):
        ws = Path(tempfile.mkdtemp())
        items = [StagedItem(native_id="a/b:1", title="T", text="body", created_at="2026-01-01")]
        out, n = write_items(ws, "demo", items)
        self.assertEqual(n, 1)
        written = list(out.glob("*.md"))
        self.assertEqual(len(written), 1)
        self.assertIn("body", written[0].read_text("utf-8"))
        prov = read_provenance(ws, "demo")
        self.assertEqual(prov[0]["native_id"], "a/b:1")
        self.assertNotIn("text", prov[0], "provenance must not duplicate the content")

    def test_source_type_is_recovered_from_the_path(self):
        self.assertEqual(source_type_of(Path("/w/workspace/00_staging/claude-code/x.md")),
                         "claude-code")
        self.assertEqual(staged_source_type(Path("/w/workspace/00_staging/claude-code/x.md")),
                         "chat")
        self.assertEqual(staged_source_type(Path("/repo/src/a.py")), "file")

    def test_registry_exposes_availability(self):
        for name, connector in REGISTRY.items():
            ok, note = connector.available()
            self.assertIsInstance(ok, bool, name)
            self.assertTrue(note, name)


CHAT = """<!-- source_type=chat connector=claude-code
     repo=proj branch=main
     human_turns=3 human_chars=900
     pushback=6 teaching=2 tool_calls=10 duration_min=45 -->

## you · 2026-01-01 10:00

不对，双写的容差不能拍脑袋定，因为老网关时钟有偏移，得按 p99 偏移量来。

## assistant

明白，我改一下。

## you · 2026-01-01 10:20

其实还有一点：对账任务要先上线，否则灰度期间只能人工核对。
"""


class TestChatTriage(unittest.TestCase):
    def doc(self):
        return Document(id="c1", path="w/00_staging/claude-code/a.md", root="w", ext=".md",
                        lang="Markdown", bytes=1, sha256="s", mtime=0.0, source_type="chat")

    def test_only_human_turns_are_scored(self):
        mine = triage.human_turns(CHAT)
        self.assertIn("不对，双写的容差", mine)
        self.assertNotIn("明白，我改一下", mine)

    def test_pushback_raises_the_score(self):
        quiet = CHAT.replace("pushback=6", "pushback=0")
        self.assertGreater(triage.score_chat(self.doc(), CHAT).score,
                           triage.score_chat(self.doc(), quiet).score)

    def test_chat_is_classified_and_typed(self):
        s = triage.score_chat(self.doc(), CHAT)
        self.assertEqual(s.artifact_class, "chat_session")
        self.assertEqual(s.source_type, "chat")

    def test_excerpt_never_truncates_a_human_turn(self):
        out, strategy = triage.excerpt(CHAT, 90)
        self.assertEqual(strategy, "chat-human-only")
        self.assertNotIn("明白，我改一下", out)
        self.assertIn("不对，双写的容差不能拍脑袋定", out)

    def test_excerpt_keeps_the_metrics_header(self):
        """The reading contract tells the model to check human_turns and
        pushback before writing an `owner` card -- so they must survive."""
        out, _ = triage.excerpt(CHAT, 90)
        for field in ("pushback=6", "human_turns=3", "repo=proj"):
            self.assertIn(field, out, field)

    def test_chat_cannot_consume_the_whole_budget(self):
        def mk(n, st, cls, score):
            return [triage.Scored(doc_id=f"{st}{i}", path=f"{st}/{i}", repo=st,
                                  artifact_class=cls, score=score - i * 0.01,
                                  est_tokens=1000, source_type=st) for i in range(n)]
        scored = mk(60, "chat", "chat_session", 9.0) + mk(6, "file", "decision_record", 5.0)
        selected = [s for s in triage.select(scored, 20000, excerpt_cap=1000) if s.selected]
        self.assertEqual(len([s for s in selected if s.source_type == "file"]), 6)


class TestTopicPolicy(unittest.TestCase):
    def test_excision_is_turn_level(self):
        chat = ("## you\n\n改成幂等的\n\n## you\n\n我最近失眠，去医院开了点药\n\n"
                "## you\n\n继续说对账容差\n")
        out = excise_topics(chat)
        self.assertEqual(out.excised_blocks, 1)
        self.assertFalse(out.dropped)
        self.assertNotIn("失眠", out.text)
        self.assertIn("改成幂等的", out.text)
        self.assertIn("topic policy", out.text, "removal must be visible to the model")

    def test_document_is_dropped_when_mostly_sensitive(self):
        chat = "## you\n\n我的年薪和房贷\n\n## you\n\n律师说竞业限制\n\n## you\n\n还行\n"
        out = excise_topics(chat)
        self.assertTrue(out.dropped)
        self.assertIn("topics:", out.drop_reason)

    def test_custom_topics_replace_defaults(self):
        out = excise_topics("## you\n\nProject Zephyr internals\n", {"codename": r"Zephyr"})
        self.assertEqual(out.excised_blocks, 1)


class TestSelfConcept(unittest.TestCase):
    def card(self, source):
        return Card(claim="自述性格偏内向，更愿意先写文档再开会", kind="self_concept",
                    evidence=[Evidence(source, "turn 3", "我一般不太喜欢开会，更愿意先写个文档")],
                    skills=["communication"], confidence=0.7)

    def test_trait_language_allowed_only_when_quoting_self(self):
        self.assertEqual(validate(self.card("chat1")), [])
        inferred = Card(claim="他性格内向，因此更适合独立作业的后端岗位", kind="capability",
                        evidence=[Evidence("a.md", "L1", "x" * 20)])
        self.assertTrue(any("personality-trait" in e for e in validate(inferred)))

    def test_repetition_of_self_description_is_not_corroboration(self):
        themes = cluster([self.card("chat1"), self.card("chat2"), self.card("chat3")])
        self.assertEqual(themes[0].status, "self-report")
        self.assertNotEqual(themes[0].status, "pattern")


if __name__ == "__main__":
    unittest.main()
