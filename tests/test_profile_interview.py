from __future__ import annotations

import unittest

from career_evidence import interview, profile
from career_evidence.cards import Card, Evidence, cluster


def card(claim, kind, source, skills, role="owner", diff=3, conf=0.8, **kw):
    return Card(claim=claim, kind=kind,
                evidence=[Evidence(source, "L1", "q" * 25)],
                skills=skills, role_signal=role, difficulty=diff, confidence=conf, **kw)


class TestQuestionGeneration(unittest.TestCase):
    def kinds(self, cards, themes=None, **kw):
        return {c.kind for c in interview.generate(cards, themes or [], **kw)}

    def test_proven_but_unwanted_is_the_first_class_question(self):
        """The one thing artifacts structurally cannot answer."""
        cards = [card("主导了支付迁移的双写方案", "decision", "adr.md", ["migration"], diff=4),
                 card("实现了对账任务", "capability", "code.py", ["migration"])]
        self.assertIn("capability_without_interest", self.kinds(cards))

    def test_it_does_not_fire_once_interest_is_on_record(self):
        cards = [card("主导了支付迁移的双写方案", "decision", "adr.md", ["migration"], diff=4),
                 card("实现了对账任务", "capability", "code.py", ["migration"]),
                 card("反复主动研究迁移工具链", "interest", "chat.md", ["migration"])]
        self.assertNotIn("capability_without_interest", self.kinds(cards))

    def test_wanting_without_doing(self):
        self.assertIn("interest_without_capability",
                      self.kinds([card("反复关注 Rust", "interest", "chat.md", ["rust"])]))

    def test_a_number_you_asserted_with_nothing_behind_it(self):
        cards = [card("自述把 ETL 从 6 小时优化到 40 分钟", "self_concept", "x.md", ["etl"])]
        self.assertIn("unverified_claim", self.kinds(cards))

    def test_the_same_claim_stops_being_asked_once_corroborated(self):
        cards = [card("自述把 ETL 从 6 小时优化到 40 分钟", "self_concept", "x.md", ["etl"]),
                 card("重写了 ETL 的批量 upsert 路径", "capability", "etl.py", ["etl"])]
        self.assertNotIn("unverified_claim", self.kinds(cards))

    def test_self_description_with_no_trace(self):
        cards = [card("自述不擅长向上沟通", "self_concept", "chat.md", ["communication"])]
        self.assertIn("self_report_gap", self.kinds(cards))

    def test_inconsistent_ownership(self):
        cards = [card("主导了迁移方案", "decision", "adr.md", ["migration"], role="owner"),
                 card("参与了另一次迁移评审", "capability", "doc.md", ["migration"],
                      role="observer")]
        self.assertIn("ownership_dispute", self.kinds(cards))

    def test_a_strength_that_stopped(self):
        cards = [card("写过行情解析器", "capability", "a.md", ["cpp"], diff=5, time_range="2019"),
                 card("优化了尾延迟到 90us", "impact", "b.md", ["cpp"], diff=4, time_range="2020")]
        self.assertIn("dormant_strength", self.kinds(cards, now_year=2026))
        self.assertNotIn("dormant_strength", self.kinds(cards, now_year=2021))


class TestRanking(unittest.TestCase):
    def candidates(self, kinds_subjects):
        return [interview.Candidate(kind=k, subject=s, observation="o", resolves="r",
                                    angle=interview.KIND_ANGLE.get(k, "advice"),
                                    weight=interview.KIND_WEIGHT.get(k, 1.0))
                for k, s in kinds_subjects]

    def test_the_same_kind_is_not_asked_twice(self):
        """Two `capability_without_interest` questions about two skills read as
        the same question asked twice."""
        chosen = interview.rank(self.candidates([
            ("capability_without_interest", "migration"),
            ("capability_without_interest", "data-consistency"),
            ("interest_without_capability", "rust")]))
        self.assertEqual(sum(1 for c in chosen if c.kind == "capability_without_interest"), 1)

    def test_one_question_per_subject(self):
        chosen = interview.rank(self.candidates([
            ("capability_without_interest", "migration"),
            ("ownership_dispute", "migration"),
            ("interest_without_capability", "rust")]))
        self.assertEqual(sum(1 for c in chosen if c.subject == "migration"), 1)

    def test_limit_is_respected(self):
        many = self.candidates([(k, f"s{i}") for i, k in enumerate(interview.KIND_WEIGHT)])
        self.assertLessEqual(len(interview.rank(many, limit=3)), 3)


class TestAnswerFeedback(unittest.TestCase):
    def test_answers_become_cards_from_an_independent_source(self):
        cards = interview.answers_to_cards([
            {"question_id": "q1", "kind": "interest", "claim": "不想再做迁移",
             "answer": "迁移我做得动，但不想再当主线了。", "skills": ["migration"]}])
        self.assertEqual(cards[0].evidence[0].source, "interview")
        self.assertIn("不想再当主线", cards[0].evidence[0].quote)

    def test_empty_answers_are_dropped(self):
        self.assertEqual(interview.answers_to_cards([{"answer": "  "}]), [])


class TestIndependence(unittest.TestCase):
    def test_saying_it_twice_does_not_make_it_a_pattern(self):
        """A claim made on a timeline and repeated in the interview is two
        sources by the count and one witness in fact."""
        said = card("自述把 ETL 从 6 小时优化到 40 分钟", "self_concept", "x-post.md", ["etl"])
        repeated = Card(claim="ETL 从 6 小时到 40 分钟，有压测报告", kind="impact",
                        evidence=[Evidence("interview", "q3", "那个 ETL 有 PR 和压测报告")],
                        skills=["etl"], role_signal="owner", confidence=0.8)
        self.assertEqual(cluster([said, repeated])[0].status, "self-report")

    def test_testimony_still_strengthens_a_real_artifact(self):
        artifact = card("重写了 ETL 的批量 upsert 路径", "capability", "etl.py", ["etl"])
        testimony = Card(claim="这类事我做过三次", kind="capability",
                         evidence=[Evidence("interview", "q6", "我做过三次")],
                         skills=["etl"], role_signal="owner", confidence=0.85)
        self.assertEqual(cluster([artifact, testimony])[0].status, "pattern")


SKELETON_CARDS = [
    card("主导了支付迁移的双写方案", "decision", "adr.md", ["migration"], diff=4),
    card("实现了对账任务", "capability", "code.py", ["migration"]),
    card("自述不擅长向上沟通", "self_concept", "chat.md", ["communication"],
         role="unknown", conf=0.7),
    card("写过行情解析器", "capability", "legacy.md", ["cpp"], diff=5,
         open_question="那三次分别是什么？"),
]


def doc(evidence: str, gaps: str = "- 有一段时间没有任何材料覆盖，需要本人补充。") -> str:
    return (f"## E\n{{{{evidence}}}}\n{evidence}\n\n"
            f"## S\n{{{{self-reported}}}}\n略。\n\n"
            f"## O\n{{{{single-source}}}}\n略。\n\n"
            f"## G\n{{{{gaps}}}}\n{gaps}\n")


class TestProfileValidation(unittest.TestCase):
    def setUp(self):
        self.cards = SKELETON_CARDS
        self.themes = cluster(self.cards)
        self.skeleton = profile.build_skeleton(self.cards, self.themes)
        self.pattern_ids = [c for e in self.skeleton.qualified for c in e.card_ids]
        self.self_id = next(c.id for c in self.cards if c.kind == "self_concept")

    def errors(self, text):
        return [p for p in profile.validate(text, self.cards, self.skeleton)
                if p.severity == "error"]

    def test_only_patterns_qualify(self):
        self.assertTrue(self.skeleton.qualified)
        for entry in self.skeleton.qualified:
            self.assertGreaterEqual(entry.sources, 2)

    def test_gaps_carry_the_open_questions(self):
        self.assertIn("那三次分别是什么？", self.skeleton.gaps)

    def test_a_compliant_document_passes(self):
        text = doc(f"主导了支付网关的双写迁移方案，并完成了对账任务 "
                   f"[{self.pattern_ids[0]}, {self.pattern_ids[1]}]。")
        self.assertEqual(self.errors(text), [])

    def test_wrapped_lines_are_not_false_positives(self):
        """Markdown wraps; a citation on the next line still cites."""
        text = doc(f"主导了支付网关的双写迁移方案，覆盖了对账与灰度切换的完整链路\n"
                   f"[{self.pattern_ids[0]}, {self.pattern_ids[1]}]。")
        self.assertEqual(self.errors(text), [])

    def test_uncited_claims_are_errors(self):
        errs = self.errors(doc("此人经验丰富，适合各类复杂系统的架构工作。"))
        self.assertTrue(any("无引用" in e.message for e in errs))

    def test_self_reports_cannot_be_cited_as_evidence(self):
        errs = self.errors(doc(f"不擅长向上沟通，更愿意先写文档再讨论 [{self.self_id}]。"))
        self.assertTrue(any("自述卡片" in e.message for e in errs))

    def test_unknown_card_ids_are_caught(self):
        errs = self.errors(doc("主导了支付网关的双写迁移方案与对账链路 [deadbeef01]。"))
        self.assertTrue(any("不存在" in e.message for e in errs))

    def test_trait_language_is_rejected(self):
        errs = self.errors(doc(f"性格细致，主导了支付网关的双写迁移方案 "
                               f"[{self.pattern_ids[0]}]。"))
        self.assertTrue(any("人格特质" in e.message for e in errs))

    def test_the_gaps_section_may_not_be_empty(self):
        errs = self.errors(doc(f"主导了支付网关的双写迁移方案与对账链路 "
                               f"[{self.pattern_ids[0]}]。", gaps=""))
        self.assertTrue(any(e.where == "gaps" for e in errs))

    def test_missing_sections_are_errors(self):
        errs = self.errors("## E\n{{evidence}}\n略略略略略略略略略略略略。\n")
        self.assertTrue(any("缺少" in e.message for e in errs))


if __name__ == "__main__":
    unittest.main()
