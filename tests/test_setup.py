from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from career.cli import main
from career.config import TEMPLATE
from career.discover import (find_export, git_identity, looks_like_notion_export,
                             looks_like_x_archive)
from career.ingest import staged_source_type


class TestDiscovery(unittest.TestCase):
    def test_identity_comes_from_git_not_the_user(self):
        """`authors` was the one field everyone had to retype by hand."""
        repo = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test Person"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
        found = git_identity(repo)
        self.assertIn("Test Person", found)
        self.assertIn("t@example.com", found)

    def test_x_archive_is_recognised_by_shape(self):
        root = Path(tempfile.mkdtemp())
        (root / "data").mkdir()
        (root / "data" / "tweets.js").write_text("window.YTD.tweets.part0 = []", "utf-8")
        self.assertTrue(looks_like_x_archive(root))
        self.assertFalse(looks_like_x_archive(Path(tempfile.mkdtemp())))

    def test_notion_export_is_recognised_by_its_id_suffixes(self):
        root = Path(tempfile.mkdtemp())
        (root / "Some page 1a2b3c4d5e6f7890abcdef1234567890.md").write_text("x", "utf-8")
        self.assertTrue(looks_like_notion_export(root))
        plain = Path(tempfile.mkdtemp())
        (plain / "notes.md").write_text("x", "utf-8")
        self.assertFalse(looks_like_notion_export(plain))

    def test_find_export_searches_download_style_folders(self):
        downloads = Path(tempfile.mkdtemp())
        archive = downloads / "twitter-2026-01-01-abcdef"
        (archive / "data").mkdir(parents=True)
        (archive / "data" / "tweets.js").write_text("window.YTD.tweets.part0 = []", "utf-8")
        self.assertEqual(find_export("x", [str(downloads)]), archive)
        self.assertIsNone(find_export("notion", [str(downloads)]))


class TestAgentStaging(unittest.TestCase):
    """Material an agent fetched over MCP must behave exactly like a
    connector's output -- same header, same classification, same gate."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp())
        self.config = self.ws / "config.json"
        cfg = dict(TEMPLATE)
        cfg.update({"sources": [], "authors": ["Me"], "workspace": str(self.ws / "workspace")})
        self.config.write_text(json.dumps(cfg), "utf-8")

    def run_stage(self, records, *extra):
        src = self.ws / "in.jsonl"
        src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), "utf-8")
        return main(["stage", "--config", str(self.config), "--connector", "notion-mcp",
                     "--source-type", "notes", "--file", str(src), *extra])

    def test_text_is_staged_verbatim(self):
        """Quote verification downstream compares against this exact text."""
        body = "我们决定用双写+对账做灰度，因为一次性切换无法回滚。接口改造涉及数据库索引，p99 目标 150ms。"
        self.assertEqual(self.run_stage([{"native_id": "p1", "title": "Design", "text": body}]), 0)
        staged = list((self.ws / "workspace" / "00_staging" / "notion-mcp").glob("*.md"))
        self.assertEqual(len(staged), 1)
        self.assertIn(body, staged[0].read_text("utf-8"))

    def test_relevance_gate_applies_to_agent_staged_material(self):
        rc = self.run_stage([
            {"native_id": "p1", "title": "Design",
             "text": "我们决定用双写+对账做灰度，因为无法回滚。接口改造涉及数据库索引，p99 目标 150ms。"},
            {"native_id": "p2", "title": "Grocery",
             "text": "番茄鸡蛋牛奶，周末做饭，顺便订了旅行的机票和酒店。"}])
        self.assertEqual(rc, 0)
        staged = list((self.ws / "workspace" / "00_staging" / "notion-mcp").glob("*.md"))
        self.assertEqual([p.stem for p in staged], ["p1"])

    def test_source_type_is_read_from_the_file_header(self):
        """So agent-staged material classifies without a registry entry."""
        self.run_stage([{"native_id": "p1", "title": "D",
                         "text": "架构评审下周三，排期两周，上线前跑性能回归看 p99 和错误率。"}])
        path = next((self.ws / "workspace" / "00_staging" / "notion-mcp").glob("*.md"))
        self.assertEqual(staged_source_type(path), "notes")

    def test_malformed_lines_are_counted_not_fatal(self):
        rc = self.run_stage([{"native_id": "p1",
                              "text": "架构评审下周三，排期两周，上线前跑性能回归看 p99。"},
                             {"missing": "fields"}])
        self.assertEqual(rc, 0)

    def test_nothing_stageable_is_an_error_not_a_silent_success(self):
        self.assertEqual(self.run_stage([{"native_id": "p1", "text": "记得交房租"}]), 1)


if __name__ == "__main__":
    unittest.main()
