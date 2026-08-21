"""Configuration and workspace layout."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config/sources.json")
DEFAULT_WORKSPACE = Path("workspace")

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
    workspace: str = str(DEFAULT_WORKSPACE)

    @classmethod
    def load(cls, path: Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Run `python -m career init` first.")
        data = json.loads(path.read_text("utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**data)

    @property
    def roots(self) -> list[Path]:
        return [Path(s).expanduser() for s in self.sources]

    # workspace paths -------------------------------------------------
    @property
    def ws(self) -> Path:
        return Path(self.workspace)

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
    def redaction_report_path(self) -> Path:
        return self.ws / "redaction-report.json"


def write_template(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(TEMPLATE, ensure_ascii=False, indent=2), "utf-8")
    return path
