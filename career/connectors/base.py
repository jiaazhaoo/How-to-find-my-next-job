"""Connectors materialise a source into local files. They never read for the model.

The rule this package exists to enforce: **a connector's only job is to write
plain files into the staging directory**. Analysis then happens exactly where
it already happened -- `scan` -> `redact` -> `triage` -> packs.

Why so strict: the redaction gate is only a gate if there is one way in. A
connector that streamed a Notion page or a chat log straight into a prompt
would be a second route to the model that bypasses `career.redact` entirely,
and the whole fail-closed guarantee would be theatre. Staging also makes runs
reproducible (re-run `prep` without re-fetching) and matches reality: most of
these platforms only offer bulk export anyway.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

STAGING_DIRNAME = "00_staging"
PROVENANCE_FILE = "_provenance.jsonl"


@dataclass
class StagedItem:
    """One unit of imported material: a chat session, a post, an issue thread."""

    native_id: str
    title: str
    text: str
    created_at: str = ""
    updated_at: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def filename(self) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", self.native_id).strip("-")[:80]
        return f"{safe or 'item'}.md"


class Connector:
    """Base class. Subclasses implement `available()` and `fetch()`."""

    name = "base"
    source_type = "generic"
    description = ""

    def available(self) -> tuple[bool, str]:
        """(usable?, one-line explanation shown by `career connectors`)."""
        return False, "not implemented"

    def fetch(self, config: dict, limit: int | None = None) -> list[StagedItem]:
        raise NotImplementedError


def staging_dir(workspace: Path, connector: str) -> Path:
    return Path(workspace) / STAGING_DIRNAME / connector


def source_type_of(path: Path) -> str:
    """Recover the connector name from a staged file's path.

    `scan` uses this so downstream quotas can tell a chat log from a repo file
    without threading extra state through every stage.
    """
    parts = Path(path).parts
    if STAGING_DIRNAME in parts:
        i = parts.index(STAGING_DIRNAME)
        if i + 1 < len(parts):
            return parts[i + 1]
    return "file"


def write_items(workspace: Path, connector: str, items: list[StagedItem],
                clean: bool = True) -> tuple[Path, int]:
    """Write items as markdown plus a provenance sidecar. Returns (dir, count)."""
    out = staging_dir(workspace, connector)
    out.mkdir(parents=True, exist_ok=True)
    if clean:
        for old in out.glob("*.md"):
            old.unlink()
        (out / PROVENANCE_FILE).unlink(missing_ok=True)

    with (out / PROVENANCE_FILE).open("w", encoding="utf-8") as prov:
        for item in items:
            path = out / item.filename
            header = [f"# {item.title}"]
            if item.created_at:
                header.append(f"<!-- created={item.created_at} updated={item.updated_at} -->")
            path.write_text("\n".join(header) + "\n\n" + item.text, "utf-8")
            record = asdict(item)
            record.pop("text")
            record["file"] = path.name
            record["connector"] = connector
            prov.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out, len(items)


def read_provenance(workspace: Path, connector: str) -> list[dict]:
    path = staging_dir(workspace, connector) / PROVENANCE_FILE
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
