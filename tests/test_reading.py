from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from career import triage
from career.cards import Card, CardError, Evidence, cluster, load, tensions, validate, verify_quotes
from career.ingest import Document, scan

DECISION_DOC = """# ADR-007 迁移方案
## 背景
老系统的写放大是瓶颈。
## 决策
我们决定分片，因为垂直扩容的成本在两年后超过分片改造，权衡后放弃了托管方案。
## 结果
P99 从 900ms 降低到 140ms，成本减少 35%。
"""

BORING_DOC = "\n".join(f"FIELD_{i} = 'value_{i}'" for i in range(120))


def doc(path="a/x.md", **kw):
    base = dict(id="i" + str(abs(hash(path)) % 9999), path=path, root="a", ext=".md",
                lang="Markdown", bytes=100, sha256="s", mtime=0.0)
    base.update(kw)
    return Document(**base)


class TestScoring(unittest.TestCase):
    def test_decision_density_beats_volume(self):
        rich = triage.score_document(doc("proj/docs/adr-007.md"), DECISION_DOC)
        dull = triage.score_document(doc("proj/settings.json", ext=".json"), BORING_DOC)
        self.assertGreater(rich.score, dull.score * 2)

    def test_ownership_moves_the_score(self):
        mine = triage.score_document(doc("p/src/a.py", ext=".py", my_line_share=0.95), DECISION_DOC)
        theirs = triage.score_document(
            doc("p/src/a.py", ext=".py", my_line_share=0.0, authored_by_me=False), DECISION_DOC)
        self.assertGreater(mine.score, theirs.score)

    def test_a_test_file_can_never_outrank_a_design_doc(self):
        """Found by running the pipeline on a real repository, not a fixture.

        Test files are dense with "because" and with numbers in assertions.
        With class weight added rather than multiplied, that density let seven
        of them into the read packs while redact.py was cut. A test file's
        reasoning is about verifying code, not about the person's judgement.
        """
        dense_test = "\n".join(
            f"# 因为边界情况，这里应该返回 {i}，否则 p99 会涨到 {i * 100}ms"
            for i in range(40))
        thin_doc = "# ADR-001 选型\n\n我们决定用 A 方案。\n"
        test_score = triage.score_document(
            doc("p/tests/test_core.py", ext=".py"), dense_test).score
        doc_score = triage.score_document(
            doc("p/docs/adr-001-choice.md"), thin_doc).score
        self.assertGreater(doc_score, test_score,
                           "marker density must not overcome artifact class")

    def test_the_leftover_pass_stays_a_fallback(self):
        """On a single-repo corpus the quotas do not bind, and "spend what is
        left" quietly became the main route in -- 13 of 25 documents."""
        scored = [triage.Scored(doc_id=f"d{i}", path=f"p/{i}.py", repo="r",
                                artifact_class="source", score=10 - i * 0.5,
                                est_tokens=1000) for i in range(30)]
        selected = [s for s in triage.select(scored, 20000, excerpt_cap=1000)
                    if s.selected]
        leftover = [s for s in selected
                    if any("leftover" in r for r in s.reasons)]
        self.assertLess(len(leftover), len(selected) / 2,
                        "the fallback must not become the main selection route")

    def test_classification(self):
        cases = {"p/docs/adr-003-x.md": "decision_record", "p/README.md": "readme",
                 "p/tests/test_a.py": "test", "p/config.yaml": "config",
                 "p/incident-2023-04.md": "postmortem"}
        for path, expected in cases.items():
            self.assertEqual(triage.classify(doc(path))[0], expected, path)

    def test_token_estimate_handles_cjk(self):
        self.assertGreater(triage.estimate_tokens("中文" * 100),
                           triage.estimate_tokens("ab" * 100))


class TestExcerpt(unittest.TestCase):
    def test_keeps_decisions_and_drops_filler(self):
        filler = "\n".join(f"noise line {i}" for i in range(400))
        text = DECISION_DOC + "\n" + filler
        out, strategy = triage.excerpt(text, 200)
        self.assertEqual(strategy, "structural")
        self.assertIn("我们决定分片", out)
        self.assertIn("lines elided", out)
        self.assertLess(triage.estimate_tokens(out), triage.estimate_tokens(text) / 2)

    def test_short_text_passes_through_untouched(self):
        out, strategy = triage.excerpt(DECISION_DOC, 5000)
        self.assertEqual(strategy, "full")
        self.assertEqual(out, DECISION_DOC)


class TestSelection(unittest.TestCase):
    def _scored(self, n, repo, cls="source", tokens=1000):
        out = []
        for i in range(n):
            s = triage.Scored(doc_id=f"{repo}{i}", path=f"{repo}/f{i}.py", repo=repo,
                              artifact_class=cls, score=10 - i * 0.01, est_tokens=tokens)
            out.append(s)
        return out

    def test_respects_the_budget(self):
        scored = triage.select(self._scored(50, "r1"), budget_tokens=5000, excerpt_cap=1000)
        self.assertLessEqual(sum(s.est_tokens for s in scored if s.selected), 5000)

    def test_one_repo_cannot_eat_the_budget(self):
        scored = self._scored(40, "loud") + self._scored(5, "quiet")
        for s in scored:
            if s.repo == "quiet":
                s.score = 5.0
        selected = [s for s in triage.select(scored, 10000, excerpt_cap=1000) if s.selected]
        repos = {s.repo for s in selected}
        self.assertIn("quiet", repos, "diversity quota should reserve room for the quiet repo")

    def test_one_class_cannot_eat_the_budget(self):
        scored = self._scored(40, "r", cls="config") + self._scored(4, "r", cls="decision_record")
        selected = [s for s in triage.select(scored, 10000, excerpt_cap=1000) if s.selected]
        self.assertIn("decision_record", {s.artifact_class for s in selected})


class TestCards(unittest.TestCase):
    def card(self, **kw):
        base = dict(claim="设计并落地了双写加对账的灰度迁移方案", kind="decision",
                    evidence=[Evidence("a.md", "L3", "我们决定分片，因为垂直扩容的成本")],
                    skills=["migration"], role_signal="owner", confidence=0.8)
        base.update(kw)
        return Card(**base)

    def test_rejects_trait_language(self):
        errs = validate(self.card(claim="他是一个很内向的人，性格谨慎"))
        self.assertTrue(any("personality-trait" in e for e in errs))

    def test_impact_card_needs_a_number(self):
        self.assertTrue(any("number" in e for e in validate(
            self.card(kind="impact", claim="大幅提升了系统的整体性能表现"))))
        self.assertEqual(validate(self.card(kind="impact", claim="把 P99 从 900ms 降到 140ms")), [])

    def test_evidence_is_mandatory(self):
        self.assertTrue(any("no evidence" in e for e in validate(self.card(evidence=[]))))

    def test_fabricated_quote_is_caught(self):
        real = self.card()
        fake = self.card(claim="独立完成了三个数据中心的容灾切换演练",
                         evidence=[Evidence("a.md", "L9", "我一个人完成了三地容灾演练")])
        stats = verify_quotes([real, fake], {"a.md": DECISION_DOC})
        self.assertTrue(real.verified)
        self.assertFalse(fake.verified)
        self.assertEqual(stats["not_found"], 1)

    def test_unknown_source_is_not_silently_accepted(self):
        c = self.card(evidence=[Evidence("ghost.md", "L1", "anything")])
        verify_quotes([c], {"a.md": DECISION_DOC})
        self.assertFalse(c.verified)

    def test_pattern_needs_independent_sources(self):
        one = self.card()
        same = self.card(claim="又一次做了双写迁移的灰度",
                         evidence=[Evidence("a.md", "L5", "权衡后放弃了托管方案")])
        other = self.card(claim="在另一个系统里也做了分片迁移",
                          evidence=[Evidence("b.md", "L2", "分片")])
        self.assertEqual(cluster([one, same])[0].status, "anecdote")
        self.assertEqual(cluster([one, same, other])[0].status, "pattern")

    def test_tensions_surface_role_conflicts(self):
        owner = self.card(role_signal="owner")
        observer = self.card(claim="在另一个项目里旁观了同类迁移", role_signal="observer",
                             evidence=[Evidence("b.md", "L1", "旁观")])
        found = tensions([owner, observer])
        self.assertTrue(any("role varies" in t["claim"] for t in found))

    def test_strict_load_rejects_a_bad_file(self):
        p = Path(tempfile.mkdtemp()) / "cards.jsonl"
        p.write_text('{"claim":"短","kind":"nope","evidence":[]}\n', "utf-8")
        with self.assertRaises(CardError):
            load(p)
        self.assertEqual(load(p, strict=False), [])


class TestIngest(unittest.TestCase):
    def test_excludes_vendored_generated_and_binary(self):
        root = Path(tempfile.mkdtemp())
        (root / "node_modules" / "x").mkdir(parents=True)
        (root / "node_modules" / "x" / "index.js").write_text("x" * 200, "utf-8")
        (root / "app.min.js").write_text("y" * 200, "utf-8")
        (root / "package-lock.json").write_text("{}" * 200, "utf-8")
        (root / "blob.bin").write_bytes(b"\x00" * 5000)
        (root / "notes.md").write_text("# real notes\n" + "content " * 40, "utf-8")
        docs, stats = scan([root], authors=["nobody"])
        self.assertEqual([Path(d.path).name for d in docs], ["notes.md"])
        self.assertGreaterEqual(stats["excluded"], 4)

    def test_identical_files_are_deduplicated(self):
        root = Path(tempfile.mkdtemp())
        body = "# same\n" + "text " * 40
        (root / "a.md").write_text(body, "utf-8")
        (root / "copy.md").write_text(body, "utf-8")
        docs, stats = scan([root], authors=["nobody"])
        self.assertEqual(len(docs), 1)
        self.assertEqual(stats["duplicates"], 1)


if __name__ == "__main__":
    unittest.main()
