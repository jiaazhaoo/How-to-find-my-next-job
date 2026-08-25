from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from career_evidence.config import TEMPLATE, Config
from career_evidence.driver import HUMAN, next_step, progress


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


class TestConfigScope(unittest.TestCase):
    """The corpus spans every repository, so it belongs to the user, not to
    whichever folder happens to be open."""

    def test_a_project_local_config_wins_when_present(self):
        from career_evidence.config import LOCAL_CONFIG_PATH, USER_CONFIG_PATH, resolve_config_path

        cwd = os.getcwd()
        root = Path(tempfile.mkdtemp())
        try:
            os.chdir(root)
            self.assertEqual(resolve_config_path(), USER_CONFIG_PATH)
            (root / "config").mkdir()
            (root / "config" / "sources.json").write_text("{}", "utf-8")
            self.assertEqual(resolve_config_path(), LOCAL_CONFIG_PATH)
        finally:
            os.chdir(cwd)

    def test_explicit_config_beats_both(self):
        from career_evidence.config import resolve_config_path

        self.assertEqual(resolve_config_path("/x/y.json"), Path("/x/y.json"))

    def test_workspace_follows_the_config_not_the_shell(self):
        """Otherwise running from elsewhere looks for its own output in the
        wrong place and quietly starts over."""
        root = Path(tempfile.mkdtemp())
        config = root / "sources.json"
        data = json.loads(json.dumps(TEMPLATE))
        data.update({"authors": ["Me"], "workspace": "workspace"})
        config.write_text(json.dumps(data), "utf-8")

        cwd = os.getcwd()
        try:
            os.chdir(tempfile.mkdtemp())
            self.assertEqual(Config.load(config).ws, root / "workspace")
        finally:
            os.chdir(cwd)

    def test_a_config_directory_does_not_swallow_the_workspace(self):
        root = Path(tempfile.mkdtemp())
        (root / "config").mkdir()
        config = root / "config" / "sources.json"
        data = json.loads(json.dumps(TEMPLATE))
        data.update({"authors": ["Me"], "workspace": "workspace"})
        config.write_text(json.dumps(data), "utf-8")
        self.assertEqual(Config.load(config).ws, root / "workspace")


class TestPackaging(unittest.TestCase):
    """The rename broke the install twice, in ways nothing here would catch:
    a package list still naming the old directory, and an entry point still
    named `career`. Both are one-line declarations no test looked at."""

    def setUp(self):
        self.pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml")
        self.text = self.pyproject.read_text("utf-8")

    def test_every_declared_package_exists(self):
        root = self.pyproject.parent
        declared = re.findall(r'"([a-z_][a-z_.]*)"', 
                              re.search(r"packages\s*=\s*\[(.*?)\]", self.text,
                                        re.S).group(1))
        self.assertTrue(declared)
        for name in declared:
            path = root / Path(*name.split("."))
            self.assertTrue((path / "__init__.py").exists(), f"{name} 不存在于 {path}")

    def test_the_entry_point_matches_the_package(self):
        entry = re.search(r'\[project\.scripts\]\s*\n\s*"?([\w-]+)"?\s*=\s*"([\w.]+):(\w+)"',
                          self.text)
        self.assertIsNotNone(entry, "找不到 console script 声明")
        _, module, func = entry.groups()
        mod = __import__(module.rsplit(":", 1)[0], fromlist=[func])
        self.assertTrue(callable(getattr(mod, func)))

    def test_the_driver_resolves_itself_in_priority_order(self):
        """Every deployment failure here came from assuming the previous step
        worked, so the invocation it prints must degrade rather than break."""
        import os
        import shutil
        from career_evidence import driver

        root = Path(__file__).resolve().parent.parent
        real_which, real_env = shutil.which, os.environ.get("CLAUDE_PLUGIN_ROOT")
        try:
            # 1. installed as a plugin: the launcher ships alongside the code
            os.environ["CLAUDE_PLUGIN_ROOT"] = str(root)
            shutil.which = lambda name: "/usr/local/bin/career-evidence"
            self.assertIn("scripts/career-evidence", driver.cli())

            # 2. no plugin, but on PATH
            del os.environ["CLAUDE_PLUGIN_ROOT"]
            self.assertEqual(driver.cli(), "career-evidence")

            # 3. neither: a plain checkout still has the launcher
            shutil.which = lambda name: None
            self.assertIn("scripts/career-evidence", driver.cli())
        finally:
            shutil.which = real_which
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            if real_env is not None:
                os.environ["CLAUDE_PLUGIN_ROOT"] = real_env

    def test_the_launcher_runs_with_nothing_installed(self):
        import subprocess

        launcher = Path(__file__).resolve().parent.parent / "scripts" / "career-evidence"
        self.assertTrue(launcher.exists())
        result = subprocess.run(["python3", str(launcher), "--help"],
                                capture_output=True, text=True, cwd="/", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("career-evidence", result.stdout)

    def test_the_plugin_manifests_are_valid(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
        self.assertEqual(manifest["name"], "career-evidence")
        self.assertIn("name", market["owner"])
        self.assertTrue(market["plugins"])
        for plugin in market["plugins"]:
            self.assertTrue((root / plugin["source"]).is_dir())
        # skills must sit where a plugin expects them
        self.assertTrue((root / "skills" / "career-evidence" / "SKILL.md").exists())


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
