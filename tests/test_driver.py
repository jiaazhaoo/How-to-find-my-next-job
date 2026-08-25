from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from career.config import TEMPLATE, Config
from career.driver import HUMAN, next_step, progress


class TestNextStep(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.config_path = self.root / "sources.json"

    def cfg(self, **overrides) -> Config:
        data = json.loads(json.dumps(TEMPLATE))
        data.update({"sources": [str(self.root / "repo")], "authors": ["Me"],
                     "workspace": str(self.root / "workspace"),
                     "sensitive_terms": [{"term": "Acme Bank", "label": "ORG"}]})
        data.update(overrides)
        self.config_path.write_text(json.dumps(data), "utf-8")
        return Config.load(self.config_path)

    def touch(self, rel: str, body: str = "x") -> None:
        path = Path(self.cfg().workspace) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, "utf-8")

    def test_no_config_starts_at_init(self):
        cfg = self.cfg()
        self.config_path.unlink()
        self.assertEqual(next_step(cfg, self.config_path).key, "init")

    def test_missing_authors_is_a_human_gate(self):
        step = next_step(self.cfg(authors=[]), self.config_path)
        self.assertEqual(step.key, "authors")
        self.assertEqual(step.kind, HUMAN)

    def test_template_sample_terms_do_not_satisfy_the_gate(self):
        """A gate a placeholder can walk through is not a gate. The template
        ships with example terms and they passed the original check."""
        step = next_step(self.cfg(sensitive_terms=TEMPLATE["sensitive_terms"]),
                         self.config_path)
        self.assertEqual(step.key, "terms")

    def test_no_terms_at_all_also_stops(self):
        self.assertEqual(next_step(self.cfg(sensitive_terms=[]), self.config_path).key,
                         "terms")

    def test_real_terms_let_it_through(self):
        self.assertNotEqual(next_step(self.cfg(), self.config_path).key, "terms")

    def test_the_review_gate_cannot_be_skipped(self):
        self.touch("00_staging/claude-code/s.md")
        self.touch("01_manifest.jsonl", '{"id":"a"}')
        self.touch("04_packs/pack-01.md", "pack")
        self.assertEqual(next_step(self.cfg(), self.config_path).key, "review")

    def test_deep_read_comes_after_review(self):
        self.touch("00_staging/claude-code/s.md")
        self.touch("01_manifest.jsonl", '{"id":"a"}')
        self.touch("04_packs/pack-01.md", "pack")
        self.touch(".reviewed")
        step = next_step(self.cfg(), self.config_path)
        self.assertEqual(step.key, "deep-read")
        self.assertEqual(step.kind, HUMAN)

    def test_no_chat_logs_is_not_a_dead_end(self):
        """A missing Codex means "you do not use Codex", not a failure."""
        step = next_step(self.cfg(connectors={}), self.config_path)
        self.assertIn(step.key, ("connect", "scan"))
        self.touch(".nochat")
        self.assertNotEqual(next_step(self.cfg(connectors={}), self.config_path).key,
                            "connect")

    def test_the_order_is_stable_all_the_way_down(self):
        cfg = self.cfg()
        seen = []
        artefacts = [("00_staging/claude-code/s.md", "x"), ("01_manifest.jsonl", '{"id":"a"}'),
                     ("04_packs/pack-01.md", "p"), (".reviewed", "x"),
                     ("05_cards.jsonl", '{"claim":"c"}'), ("06_themes.json", "{}"),
                     ("07_questions.json", "{}"), ("07_answers.jsonl", '{"a":1}'),
                     ("08_skeleton.json", "{}"), ("09_profile.md", "text")]
        for rel, body in artefacts:
            seen.append(next_step(cfg, self.config_path).key)
            self.touch(rel, body)
        seen.append(next_step(cfg, self.config_path).key)
        self.assertEqual(seen, ["connect", "scan", "prep", "review", "deep-read", "themes",
                                "questions", "interview", "skeleton", "write", "check"])


class TestProgress(unittest.TestCase):
    def test_checklist_reflects_what_exists(self):
        root = Path(tempfile.mkdtemp())
        config_path = root / "c.json"
        data = json.loads(json.dumps(TEMPLATE))
        data.update({"authors": ["Me"], "sources": ["/tmp"],
                     "workspace": str(root / "ws")})
        config_path.write_text(json.dumps(data), "utf-8")
        cfg = Config.load(config_path)
        rows = dict(progress(cfg, config_path))
        self.assertEqual(rows["配置"], "ok")
        self.assertEqual(rows["画像"], "-")


if __name__ == "__main__":
    unittest.main()
