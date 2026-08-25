from __future__ import annotations

import unittest

from career_evidence.cards import Card, Evidence
from career_evidence.reliability import analyse, band


def card(claim, source, quote, skills, diff=3, conf=0.8, kind="capability"):
    return Card(claim=claim, kind=kind, evidence=[Evidence(source, "L1", quote)],
                skills=skills, role_signal="owner", difficulty=diff, confidence=conf)


BASE = [
    card("主导了支付网关的双写迁移方案", "adr.md", "我们决定用双写 + 对账做灰度", ["migration"], diff=4),
    card("实现了对账任务处理时钟偏移", "code.py", "老网关的时钟有偏移，放宽 30s 容差",
         ["migration", "data-consistency"]),
    card("写过 C++ 实时行情解析器", "legacy.md", "核心是零拷贝环形缓冲", ["cpp"], diff=5),
]


class TestSanity(unittest.TestCase):
    def test_identical_runs_agree_completely(self):
        """If this is not 100%, the metric is measuring its own noise."""
        r = analyse([BASE, BASE, BASE])
        self.assertEqual(r.evidence["mean"], 1.0)
        self.assertEqual(r.claims["stable_fraction"], 1.0)
        self.assertEqual(r.themes["mean"], 1.0)
        self.assertEqual(r.questions["mean"], 1.0)

    def test_disjoint_runs_agree_on_nothing(self):
        other = [card("重构了前端构建流程", "web.ts", "把 webpack 换成 vite", ["frontend"])]
        r = analyse([BASE, other])
        self.assertEqual(r.evidence["mean"], 0.0)
        self.assertEqual(r.claims["stable_fraction"], 0.0)

    def test_one_run_is_refused(self):
        r = analyse([BASE])
        self.assertTrue(r.notes)
        self.assertEqual(r.claims, {})


class TestMatchingIsSoft(unittest.TestCase):
    def test_a_rephrased_claim_counts_as_the_same_claim(self):
        """Card ids hash the wording, so exact matching would score zero."""
        reworded = [card("在支付网关迁移中主导了双写方案的设计", "adr.md",
                         "我们决定用双写 + 对账做灰度", ["migration"], diff=4)]
        r = analyse([[BASE[0]], reworded])
        self.assertEqual(r.claims["stable_fraction"], 1.0)

    def test_themes_match_despite_one_extra_skill_label(self):
        """A theme whose member cards differ slightly is still that theme."""
        run_a = [card("主导了迁移方案", "adr.md", "q1", ["migration", "data-consistency"]),
                 card("实现了对账任务", "code.py", "q2", ["migration", "data-consistency"])]
        run_b = [card("主导了迁移方案", "adr.md", "q1",
                      ["migration", "data-consistency", "performance"]),
                 card("实现了对账任务", "code.py", "q2", ["migration", "data-consistency"])]
        r = analyse([run_a, run_b])
        self.assertEqual(r.themes["mean"], 1.0)


class TestWhatItDetects(unittest.TestCase):
    def test_rating_disagreement_is_surfaced(self):
        """difficulty has no anchors and drives threshold logic."""
        # Shift downward: BASE contains a 5, so shifting up would clamp and
        # understate the disagreement the test is trying to detect.
        shifted = [card(c.claim, c.evidence[0].source, c.evidence[0].quote, c.skills,
                        diff=max(1, c.difficulty - 2)) for c in BASE]
        r = analyse([BASE, shifted])
        self.assertGreaterEqual(r.ratings["difficulty_spread_mean"], 1.5)
        self.assertGreaterEqual(r.ratings["difficulty_disagreements_ge_2"], 1)
        self.assertTrue(any("difficulty" in n for n in r.notes))

    def test_uneven_extraction_depth_is_flagged(self):
        r = analyse([BASE, BASE[:1]])
        self.assertTrue(any("卡片数差异" in n for n in r.notes))

    def test_a_claim_found_only_once_is_counted(self):
        extra = BASE + [card("顺手修了个构建脚本", "build.sh", "把缓存路径改对了", ["build"])]
        r = analyse([BASE, extra])
        self.assertEqual(r.claims["in_one_run_only"], 1)

    def test_partial_overlap_lands_between_the_extremes(self):
        r = analyse([BASE, BASE[:2]])
        self.assertGreater(r.evidence["mean"], 0.0)
        self.assertLess(r.evidence["mean"], 1.0)


class TestBands(unittest.TestCase):
    def test_bands_are_ordered(self):
        self.assertEqual(band(0.95), "stable")
        self.assertEqual(band(0.70), "usable")
        self.assertEqual(band(0.50), "shaky")
        self.assertEqual(band(0.10), "unreliable")


if __name__ == "__main__":
    unittest.main()
