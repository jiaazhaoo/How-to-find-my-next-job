from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from career_evidence import triage
from career_evidence.connectors.codex import CodexConnector
from career_evidence.connectors.notion_export import NotionExportConnector, clean_title
from career_evidence.connectors.x_archive import XArchiveConnector
from career_evidence.ingest import Document
from career_evidence.relevance import weighted_length, work_score

LONG_CN = ("这个迁移脚本要幂等，之前回滚过一次。其实我更想先把对账任务上线，因为灰度期间只能人工核对，"
           "出问题也没法定位。容差不能拍脑袋定，应该按 p99 时钟偏移来算。")
PUSHBACK_CN = "不对，你把重试和幂等搞混了。重试是调用方的事，幂等是被调用方的保证，这两个不能互相替代。"


class TestCodexConnector(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "2026" / "01" / "02").mkdir(parents=True)

    def write(self, records, name="rollout-x.jsonl"):
        path = self.root / "2026" / "01" / "02" / name
        path.write_text("\n".join(json.dumps(r) for r in records), "utf-8")
        return path

    def fetch(self, records, **conf):
        self.write(records)
        return CodexConnector().fetch({"roots": [str(self.root)], "min_human_chars": 150, **conf})

    @staticmethod
    def meta_line(**kw):
        return {"timestamp": "2026-01-02T09:00:00Z", "type": "session_meta",
                "payload": {"id": "abc", "cwd": "/home/me/payments-svc",
                            "git": {"branch": "feat/reconcile"}, **kw}}

    @staticmethod
    def user_item(text):
        return {"timestamp": "2026-01-02T09:01:00Z", "type": "response_item",
                "payload": {"type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": text}]}}

    @staticmethod
    def user_event(text):
        return {"timestamp": "2026-01-02T09:01:00Z", "type": "event_msg",
                "payload": {"type": "user_message", "message": text}}

    def test_duplicate_prompt_is_counted_once(self):
        """Codex emits each prompt twice -- as an event and as model input.
        Left undeduplicated, every 'your words' metric doubles."""
        items = self.fetch([self.meta_line(), self.user_event(LONG_CN), self.user_item(LONG_CN)])
        self.assertEqual(items[0].meta["human_turns"], 1)
        self.assertEqual(items[0].text.count(LONG_CN[:20]), 1)

    def test_environment_preamble_is_not_your_words(self):
        items = self.fetch([
            self.meta_line(),
            self.user_item("<environment_context>cwd=/home/me</environment_context>"),
            self.user_event(LONG_CN)])
        self.assertEqual(items[0].meta["human_turns"], 1)
        self.assertNotIn("environment_context", items[0].text)

    def test_tool_output_and_reasoning_never_reach_the_transcript(self):
        items = self.fetch([
            self.meta_line(), self.user_event(LONG_CN),
            {"timestamp": "2026-01-02T09:01:05Z", "type": "response_item",
             "payload": {"type": "reasoning",
                         "summary": [{"type": "summary_text", "text": "INTERNAL"}]}},
            {"timestamp": "2026-01-02T09:01:06Z", "type": "response_item",
             "payload": {"type": "function_call", "name": "shell", "arguments": "{}"}},
            {"timestamp": "2026-01-02T09:01:07Z", "type": "response_item",
             "payload": {"type": "function_call_output", "output": "SECRETOUT" * 400}}])
        text = items[0].text
        self.assertNotIn("INTERNAL", text)
        self.assertNotIn("SECRETOUT", text)
        self.assertEqual(items[0].meta["tools"], {"shell": 1})

    def test_unparsed_counter_only_counts_genuine_unknowns(self):
        """It exists to make a format change visible, so deliberate skips and
        the many event_msg progress events must not inflate it."""
        items = self.fetch([
            self.meta_line(), self.user_event(LONG_CN),
            {"timestamp": "t", "type": "response_item", "payload": {"type": "reasoning"}},
            {"timestamp": "t", "type": "event_msg", "payload": {"type": "token_count", "n": 5}},
            {"timestamp": "t", "type": "turn_context", "payload": {"model": "x"}},
            {"timestamp": "t", "type": "response_item", "payload": {"type": "brand_new_shape"}}])
        self.assertEqual(items[0].meta["unparsed_records"], 1)

    def test_session_metadata_is_recovered(self):
        items = self.fetch([self.meta_line(), self.user_event(LONG_CN + PUSHBACK_CN)])
        self.assertEqual(items[0].meta["repo"], "payments-svc")
        self.assertEqual(items[0].meta["branch"], "feat/reconcile")
        self.assertGreaterEqual(items[0].meta["pushback"], 1)

    def test_thin_sessions_are_skipped(self):
        self.assertEqual(self.fetch([self.meta_line(), self.user_event("改一下")]), [])


class TestNotionExport(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.sub = self.root / "Engineering 1a2b3c4d5e6f7890abcdef1234567890"
        self.sub.mkdir()

    def fetch(self, **conf):
        c = NotionExportConnector()
        items = c.fetch({"path": str(self.root), **conf})
        return c, items

    def test_notion_ids_are_stripped_from_title_and_identifier(self):
        """Left in, the hex ids are long digit runs that trip the payment-card
        rule and every filename becomes [[PAYMENT_CARD_nn]]."""
        (self.sub / "Weekly sync notes 1111222233334444555566667777888a.md").write_text(
            "# Weekly sync\n\n架构评审下周三，排期按两周算，上线前跑性能回归看 p99。", "utf-8")
        _, items = self.fetch()
        self.assertEqual(clean_title("Weekly sync notes 1111222233334444555566667777888a.md"),
                         "Weekly sync notes")
        self.assertNotIn("1111222233334444", items[0].native_id)
        self.assertIn("Weekly sync notes", items[0].title)

    def test_database_exports_become_readable_tables(self):
        (self.sub / "Tracker 5555666677778888999900001111222b.csv").write_text(
            "Name,Status,Notes\n迁移,Done,双写对账灰度上线接口改造\n索引重建,Doing,数据库性能优化\n", "utf-8")
        _, items = self.fetch(min_relevance=0.0)
        self.assertIn("| Name | Status | Notes |", items[0].text)

    def test_non_work_pages_are_filtered_and_counted(self):
        (self.root / "Grocery list aaaabbbbccccddddeeeeffff00001111.md").write_text(
            "# Grocery\n\n番茄鸡蛋牛奶，周末做饭。顺便订了旅行的机票和酒店。", "utf-8")
        (self.sub / "Design doc 9f8e7d6c5b4a39281706f5e4d3c2b1a0.md").write_text(
            "# Design\n\n我们决定用双写+对账做灰度，因为一次性切换无法回滚。接口改造涉及数据库索引。", "utf-8")
        connector, items = self.fetch()
        self.assertEqual(len(items), 1)
        self.assertIn("Design doc", items[0].title)
        self.assertEqual(connector.last_skipped, 1)
        self.assertTrue(connector.last_histogram)

    def test_missing_path_is_reported_not_crashed(self):
        ok, note = NotionExportConnector().available({})
        self.assertFalse(ok)
        self.assertIn("path", note)


class TestXArchive(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()

    def write(self, tweets, account_id="ME"):
        (self.root / "data" / "account.js").write_text(
            "window.YTD.account.part0 = " +
            json.dumps([{"account": {"accountId": account_id}}]), "utf-8")
        (self.root / "data" / "tweets.js").write_text(
            "window.YTD.tweets.part0 = " + json.dumps(tweets), "utf-8")

    @staticmethod
    def tweet(i, text, reply_to=None, reply_user=None):
        t = {"id_str": str(i), "full_text": text, "favorite_count": "3", "retweet_count": "1",
             "created_at": f"Wed Jan 0{i} 10:00:00 +0000 2026"}
        if reply_to:
            t["in_reply_to_status_id_str"] = reply_to
        if reply_user:
            t["in_reply_to_user_id_str"] = reply_user
        return {"tweet": t}

    def fetch(self, **conf):
        c = XArchiveConnector()
        return c, c.fetch({"path": str(self.root), **conf})

    def test_retweets_and_replies_to_others_are_dropped(self):
        self.write([self.tweet(1, "RT @someone: 完全同意这个观点，架构上确实应该这样做，接口设计要收敛"),
                    self.tweet(2, "哈哈是的", reply_to="999", reply_user="OTHER"),
                    self.tweet(3, "重构了 ETL pipeline，从 6 小时降到 40 分钟，核心是把逐行处理换成批量 upsert，"
                                  "顺便去掉了 schema 里的隐式类型转换。")])
        connector, items = self.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(connector.last_counts["retweets"], 1)
        self.assertEqual(connector.last_counts["replies_to_others"], 1)

    def test_self_replies_are_reassembled_into_one_thread(self):
        """A twelve-post argument must not be scored as twelve trivial posts."""
        self.write([self.tweet(1, "关于支付系统迁移的经验：一次性切换看起来干净，但没有回滚路径。"),
                    self.tweet(2, "对账任务应该在灰度之前上线，人工核对的代价被低估了。",
                               reply_to="1", reply_user="ME"),
                    self.tweet(3, "容差不要拍脑袋，按 p99 时钟偏移算。", reply_to="2", reply_user="ME")])
        connector, items = self.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].meta["posts"], 3)
        self.assertEqual(connector.last_counts["threads"], 1)
        for fragment in ("一次性切换", "对账任务", "时钟偏移"):
            self.assertIn(fragment, items[0].text)

    def test_short_chinese_posts_are_not_mistaken_for_one_liners(self):
        """The cutoff is weighted: raw len() silently discards Chinese posts."""
        substantial = ("重构了 ETL pipeline，六小时降到四十分钟，把逐行处理换成批量 upsert，"
                       "并去掉了 schema 的隐式类型转换。")
        self.assertLess(len(substantial), 80)
        self.assertGreaterEqual(weighted_length(substantial), 80)
        self.write([self.tweet(1, substantial), self.tweet(2, "今天天气不错")])
        _, items = self.fetch()
        self.assertEqual(len(items), 1)

    def test_js_wrapper_is_stripped(self):
        self.write([self.tweet(1, "架构决策记录：我们放弃了托管方案，因为成本模型在三年后反转，"
                                  "自研的接口改造代价可控。")])
        _, items = self.fetch()
        self.assertTrue(items)


class TestRelevance(unittest.TestCase):
    def test_work_material_outscores_lifestyle(self):
        work, _ = work_score("我们决定用双写+对账做灰度，因为一次性切换无法回滚。接口改造涉及数据库索引，p99 目标 150ms。")
        life, _ = work_score("番茄炒蛋菜谱：鸡蛋三个。周末做饭，顺便买了旅行的机票。")
        self.assertGreaterEqual(work, 0.7)
        self.assertLess(life, 0.3)
        self.assertGreater(work - life, 0.4, "the separation is what matters, not the level")

    def test_link_dumps_and_stubs_score_low(self):
        dump, signals = work_score("- https://a.com/x\n- https://b.com/y\n- https://c.com/z")
        self.assertLess(dump, 0.4)
        self.assertIn("link_dump", signals)
        stub, _ = work_score("记得交房租")
        self.assertLess(stub, 0.4)

    def test_english_and_chinese_both_register(self):
        en, _ = work_score("Refactored the ingest pipeline; the migration needed a schema "
                           "change plus a backfill. Latency regression traced to a missing index.")
        self.assertGreater(en, 0.6)


class TestImportedClassification(unittest.TestCase):
    def doc(self, source_type):
        return Document(id="x", path=f"w/00_staging/s/a.md", root="w", ext=".md",
                        lang="Markdown", bytes=1, sha256="s", mtime=0.0,
                        source_type=source_type)

    def test_source_type_decides_the_class(self):
        self.assertEqual(triage.classify(self.doc("notes"))[0], "work_note")
        self.assertEqual(triage.classify(self.doc("public"))[0], "public_post")
        self.assertEqual(triage.classify(self.doc("chat"))[0], "chat_session")

    def test_relevance_from_the_connector_moves_the_score(self):
        body = "\n\n我们决定用双写做灰度，因为无法回滚。"
        high = triage.score_document(
            self.doc("notes"), "<!-- source_type=notes work_relevance=0.95 -->" + body)
        low = triage.score_document(
            self.doc("notes"), "<!-- source_type=notes work_relevance=0.40 -->" + body)
        self.assertGreater(high.score, low.score)

    def test_thread_length_counts_but_engagement_does_not(self):
        """Likes measure the audience, not the author."""
        body = "\n\n关于迁移的经验：一次性切换没有回滚路径。"
        long_thread = triage.score_document(
            self.doc("public"), "<!-- source_type=public posts=5 likes=0 -->" + body)
        popular = triage.score_document(
            self.doc("public"), "<!-- source_type=public posts=1 likes=9000 -->" + body)
        self.assertGreater(long_thread.score, popular.score)
        self.assertFalse(any("likes" in r for r in popular.reasons))


if __name__ == "__main__":
    unittest.main()


class TestTermSuggestion(unittest.TestCase):
    """Asking "what are your client names?" is a recall task, the hardest kind.
    The material usually contains the answer; the tool should propose and let
    the user recognise."""

    def suggest(self, text, authors=("Me",), repos=()):
        import tempfile
        from career_evidence.terms import suggest
        path = Path(tempfile.mkdtemp()) / "doc.md"
        path.write_text(text, "utf-8")
        return suggest([path], list(authors), list(repos))

    def test_it_finds_names_by_the_word_that_follows_them(self):
        found = self.suggest("北极星项目由星辰科技负责，客户：远洋银行。"
                             "对接 Acme Corp 与 Zephyr Technologies。")
        terms = {c.term for c in found.candidates}
        for expected in ("北极星", "星辰", "Acme", "Zephyr"):
            self.assertIn(expected, terms)

    def test_prose_does_not_become_candidates(self):
        """A candidate list full of noise is worse than no list."""
        found = self.suggest("我们决定把这个项目整个迁移，所以关于那个系统的方案，"
                             "每个平台都要评估一遍，这是同一家公司的两个计划。")
        self.assertEqual([c.term for c in found.candidates], [])

    def test_internal_hosts_are_flagged(self):
        found = self.suggest("连接 pay-db.internal:5432 做对账。")
        self.assertIn("pay-db.internal", {c.term for c in found.candidates})

    def test_a_personal_project_needs_no_question(self):
        """One committer, no corporate mail, no internal hosts: 'there is no
        client here' is a finding, not a guess."""
        found = self.suggest("重构了解析器，把逐行处理换成批量写入，延迟降了一半。")
        self.assertTrue(found.looks_personal)
        self.assertEqual(found.candidates, [])

    def test_public_mail_is_not_a_corporate_signal(self):
        found = self.suggest("联系 me@gmail.com 或 you@qq.com")
        self.assertTrue(found.looks_personal)

    def test_overlapping_names_collapse(self):
        found = self.suggest("客户：远洋银行，远洋银行的对账系统。")
        terms = [c.term for c in found.candidates]
        self.assertNotIn("远洋", terms)
        self.assertIn("远洋银行", terms)
