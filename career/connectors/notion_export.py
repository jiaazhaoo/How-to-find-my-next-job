"""Notion workspace export (unzipped) -> staged pages.

Notion is where people keep everything, which is exactly the problem: the same
workspace holds design docs, meeting notes, a reading list and a grocery run.
So this connector does two jobs beyond reading files -- it undoes Notion's
export cosmetics (32-hex id suffixes on every filename and title, CSV database
dumps), and it applies `career.relevance` so the grocery run does not consume
read-pack budget.

Point `path` at the unzipped export. Nothing talks to Notion's API: an export
is a directory of markdown, which is precisely the shape the pipeline already
consumes, and it keeps the redaction gate the only way in.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ..relevance import work_score
from .base import Connector, StagedItem

# "Design Doc 1a2b3c4d5e6f7890abcdef1234567890.md"
NOTION_ID = re.compile(r"\s+[0-9a-f]{32}(?=$|\.)")
MAX_CSV_ROWS = 60


def clean_title(name: str) -> str:
    return NOTION_ID.sub("", Path(name).stem).strip()


def _csv_to_markdown(path: Path) -> str:
    """Database exports are usually the project trackers -- worth keeping."""
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return ""
    header, body = rows[0], rows[1:MAX_CSV_ROWS + 1]
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(c.replace("\n", " ")[:120] for c in r) + " |" for r in body]
    if len(rows) - 1 > MAX_CSV_ROWS:
        out.append(f"\n<!-- {len(rows) - 1 - MAX_CSV_ROWS} more rows omitted -->")
    return "\n".join(out)


class NotionExportConnector(Connector):
    name = "notion-export"
    source_type = "notes"
    description = "Unzipped Notion workspace export (set `path` in config)"

    def __init__(self) -> None:
        self.last_skipped = 0
        self.last_histogram: dict[str, int] = {}

    def _root(self, config: dict) -> Path | None:
        raw = config.get("path")
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.exists() else None

    def available(self, config: dict | None = None) -> tuple[bool, str]:
        path = self._root(config or {})
        if not path:
            return False, ("set connectors['notion-export'].path to your unzipped export "
                           "(Notion → Settings → Export content)")
        n = len(list(path.rglob("*.md"))) + len(list(path.rglob("*.csv")))
        return n > 0, f"{n} page(s) under {path}"

    def fetch(self, config: dict, limit: int | None = None) -> list[StagedItem]:
        root = self._root(config)
        if not root:
            return []
        threshold = float(config.get("min_relevance", 0.35))
        files = sorted([*root.rglob("*.md"), *root.rglob("*.csv")])

        items, scores, skipped = [], [], 0
        for path in files:
            try:
                text = (_csv_to_markdown(path) if path.suffix == ".csv"
                        else path.read_text("utf-8", errors="replace"))
            except OSError:
                continue
            if not text.strip():
                continue
            score, signals = work_score(text)
            scores.append(score)
            if score < threshold:
                skipped += 1
                continue
            rel = path.relative_to(root)
            crumbs = " / ".join(clean_title(p) for p in rel.parts[:-1] if clean_title(p))
            title = clean_title(path.name)
            # Strip Notion's 32-hex ids from the identifier too, not just the
            # display title: left in, they are long digit runs that trip the
            # payment-card rule and turn every filename into [[PAYMENT_CARD_nn]].
            slug = "__".join(clean_title(part) for part in rel.parts)
            items.append(StagedItem(
                native_id=slug[:100] or str(rel)[:100],
                title=f"Notion · {crumbs + ' / ' if crumbs else ''}{title}",
                text=f"<!-- source_type=notes connector=notion-export "
                     f"work_relevance={score} path={crumbs or '(root)'} -->\n\n{text}",
                meta={"source_type": self.source_type, "work_relevance": score,
                      "signals": signals, "breadcrumbs": crumbs, "kind": path.suffix.lstrip(".")},
            ))
        self.last_skipped = skipped
        from ..relevance import histogram
        self.last_histogram = histogram(scores)
        items.sort(key=lambda i: -float(i.meta["work_relevance"]))
        return items[:limit] if limit else items
