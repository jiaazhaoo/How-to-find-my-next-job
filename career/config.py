"""Configuration and workspace layout."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Your career corpus spans every repository you have ever worked in, so it is
# yours, not any one project's. Requiring you to stand inside a particular
# folder to analyse all the others was backwards. The data lives in one place
# under HOME; a project-local config still wins when there is one, which is
# what makes developing this tool on itself possible.
USER_HOME_DIR = Path("~/.career").expanduser()
LOCAL_CONFIG_PATH = Path("config/sources.json")
USER_CONFIG_PATH = USER_HOME_DIR / "sources.json"
DEFAULT_CONFIG_PATH = LOCAL_CONFIG_PATH        # kept for callers passing paths
DEFAULT_WORKSPACE = Path("workspace")


def resolve_config_path(explicit: str | None = None) -> Path:
    """--config > a project-local config that exists > the one under HOME."""
    if explicit:
        return Path(explicit).expanduser()
    if LOCAL_CONFIG_PATH.exists():
        return LOCAL_CONFIG_PATH
    return USER_CONFIG_PATH

TEMPLATE = {
    "sources": ["~/code/my-project", "~/Documents/reports"],
    "authors": ["Your Name", "you@example.com"],
    "since": None,
    "budget_tokens": 120000,
    "pack_tokens": 25000,
    "excerpt_cap": 3000,
    "keep_pii_reversible": True,
    "pseudonymize_git_identities": True,
    "sensitive_terms": [
        {"term": "Acme Corp", "label": "ORG"},
        {"term": "北极星计划", "label": "PROJECT"},
    ],
    "allowlist": ["example.com", "localhost"],
    "per_source_share": 0.35,
    "connectors": {
        "claude-code": {"enabled": True, "min_human_chars": 200},
        "codex": {"enabled": True, "min_human_chars": 200},
        "notion-export": {"enabled": True, "path": None, "min_relevance": 0.35},
        "x-archive": {"enabled": True, "path": None, "min_relevance": 0.35},
        "web": {"enabled": True, "urls": [], "min_relevance": 0.35, "limit": 200}
    },
    "topic_policy": {
        "enabled_for": ["chat", "notes", "public"],
        "drop_ratio": 0.5,
        "extra_topics": {}
    },
}


@dataclass
class Config:
    sources: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    since: str | None = None
    budget_tokens: int = 120_000
    pack_tokens: int = 25_000
    excerpt_cap: int = 3_000
    keep_pii_reversible: bool = True
    pseudonymize_git_identities: bool = True
    sensitive_terms: list[dict] = field(default_factory=list)
    allowlist: list[str] = field(default_factory=list)
    per_source_share: float = 0.35
    connectors: dict = field(default_factory=dict)
    topic_policy: dict = field(default_factory=dict)
    workspace: str = str(DEFAULT_WORKSPACE)

    _path: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} 不存在。\n"
                f"  · 第一次用：career init\n"
                f"  · 已经建过：从项目目录里运行（配置和 workspace 都是相对路径），"
                f"或者用 --config 指定绝对路径。\n"
                f"  当前目录：{Path.cwd()}")
        data = json.loads(path.read_text("utf-8"))
        known = {f for f in cls.__dataclass_fields__ if not f.startswith("_")}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        cfg = cls(**data)
        cfg._path = path.resolve()
        return cfg

    @property
    def roots(self) -> list[Path]:
        return [Path(s).expanduser() for s in self.sources]

    # workspace paths -------------------------------------------------
    @property
    def ws(self) -> Path:
        """Relative workspaces resolve against the config file, not the shell.

        Otherwise `career run` from a different directory would look for its
        own output somewhere else and quietly start over.
        """
        raw = Path(self.workspace).expanduser()
        if raw.is_absolute() or self._path is None:
            return raw
        # Hand-written relative paths resolve against the config file. `init`
        # writes absolute ones precisely so this case does not come up.
        base = self._path.parent
        if base.name == "config":
            base = base.parent
        return base / raw

    @property
    def staging_root(self) -> Path:
        from .connectors.base import STAGING_DIRNAME
        return self.ws / STAGING_DIRNAME

    @property
    def scan_roots(self) -> list[Path]:
        """Disk sources plus whatever connectors have already staged.

        Staged material is just files by the time `scan` sees it -- that is
        the entire point of the connector contract.
        """
        staged = sorted(d for d in self.staging_root.glob("*") if d.is_dir()) \
            if self.staging_root.exists() else []
        return self.roots + staged

    def topics(self) -> dict | None:
        from .redact import DEFAULT_TOPICS
        extra = (self.topic_policy or {}).get("extra_topics") or {}
        return {**DEFAULT_TOPICS, **extra} if extra else None

    def topic_applies_to(self, source_type: str) -> bool:
        enabled = (self.topic_policy or {}).get("enabled_for", ["chat"])
        return source_type in enabled

    @property
    def vault_path(self) -> Path:
        return self.ws / "vault" / "aliases.json"

    @property
    def manifest_path(self) -> Path:
        return self.ws / "01_manifest.jsonl"

    @property
    def shortlist_path(self) -> Path:
        return self.ws / "02_shortlist.jsonl"

    @property
    def corpus_path(self) -> Path:
        return self.ws / "03_corpus.json"

    @property
    def packs_dir(self) -> Path:
        return self.ws / "04_packs"

    @property
    def cards_path(self) -> Path:
        return self.ws / "05_cards.jsonl"

    @property
    def themes_path(self) -> Path:
        return self.ws / "06_themes.json"

    @property
    def questions_path(self) -> Path:
        return self.ws / "07_questions.json"

    @property
    def answers_path(self) -> Path:
        return self.ws / "07_answers.jsonl"

    @property
    def skeleton_path(self) -> Path:
        return self.ws / "08_skeleton.json"

    @property
    def profile_path(self) -> Path:
        return self.ws / "09_profile.md"

    @property
    def redaction_report_path(self) -> Path:
        return self.ws / "redaction-report.json"


def write_template(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(TEMPLATE, ensure_ascii=False, indent=2), "utf-8")
    return path
