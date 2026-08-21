"""X (Twitter) archive export -> staged posts and threads.

Timelines are a genuinely different signal from everything else in the corpus:
chat logs and repositories record thinking done for yourself, a timeline
records what you chose to say *to an audience*. That makes it the best
available evidence for positioning and sustained interest -- and the worst for
capability, since nothing there is verified by anything.

Three filters do most of the work:

* **Retweets are not writing.** Dropped outright.
* **Replies to other people are conversation fragments**, unreadable without
  the other half. Dropped.
* **Self-replies are threads**, and threads are where anything substantial
  gets written. They are reassembled into one item, which also stops a
  twelve-post argument from being scored as twelve trivial ones.

Point `path` at the unzipped archive (the folder containing `data/`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..relevance import histogram, weighted_length, work_score
from .base import Connector, StagedItem

JS_PREFIX = re.compile(r"^\s*window\.YTD\.[A-Za-z0-9_]+\.part\d+\s*=\s*", re.M)
RT = re.compile(r"^RT @\w+:")
# A coarse floor only: "今天天气不错" scores ~15, while a real one-sentence
# opinion clears 80 comfortably. The relevance gate does the actual filtering,
# so this can afford to be lenient -- set too high, it silently eats genuine
# short posts, and Chinese ones first.
MIN_STANDALONE_WEIGHT = 80


def _load_js(path: Path):
    """Archive files are JSON wearing a `window.YTD.x.part0 =` hat."""
    raw = path.read_text("utf-8", errors="replace")
    return json.loads(JS_PREFIX.sub("", raw, count=1).strip().rstrip(";"))


def _iso(created_at: str) -> str:
    try:
        return datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").isoformat()
    except ValueError:
        return ""


class XArchiveConnector(Connector):
    name = "x-archive"
    source_type = "public"
    description = "Unzipped X/Twitter data archive (set `path` in config)"

    def __init__(self) -> None:
        self.last_skipped = 0
        self.last_histogram: dict[str, int] = {}
        self.last_counts: dict[str, int] = {}

    def _root(self, config: dict) -> Path | None:
        raw = config.get("path")
        if not raw:
            return None
        path = Path(raw).expanduser()
        if (path / "data").exists():
            return path
        return path if path.exists() and list(path.glob("tweet*.js")) else None

    def _tweet_files(self, root: Path) -> list[Path]:
        base = root / "data" if (root / "data").exists() else root
        return sorted(p for p in base.glob("tweet*.js") if "headers" not in p.name)

    def available(self, config: dict | None = None) -> tuple[bool, str]:
        root = self._root(config or {})
        if not root:
            return False, ("set connectors['x-archive'].path to your unzipped archive "
                           "(X → Settings → Download an archive of your data)")
        files = self._tweet_files(root)
        return bool(files), f"{len(files)} tweet file(s) under {root}"

    def _own_id(self, root: Path) -> str:
        base = root / "data" if (root / "data").exists() else root
        for name in ("account.js", "profile.js"):
            path = base / name
            if not path.exists():
                continue
            try:
                data = _load_js(path)
                for entry in data:
                    for value in entry.values():
                        if isinstance(value, dict) and value.get("accountId"):
                            return str(value["accountId"])
            except (json.JSONDecodeError, OSError, AttributeError):
                continue
        return ""

    def fetch(self, config: dict, limit: int | None = None) -> list[StagedItem]:
        root = self._root(config)
        if not root:
            return []
        threshold = float(config.get("min_relevance", 0.35))
        own = self._own_id(root)

        tweets: dict[str, dict] = {}
        counts = {"total": 0, "retweets": 0, "replies_to_others": 0}
        for path in self._tweet_files(root):
            try:
                raw = _load_js(path)
            except (json.JSONDecodeError, OSError):
                continue
            for entry in raw:
                t = entry.get("tweet", entry) if isinstance(entry, dict) else None
                if not isinstance(t, dict) or not t.get("id_str"):
                    continue
                counts["total"] += 1
                text = t.get("full_text") or t.get("text") or ""
                if RT.match(text):
                    counts["retweets"] += 1
                    continue
                reply_to_user = t.get("in_reply_to_user_id_str")
                if reply_to_user and own and reply_to_user != own:
                    counts["replies_to_others"] += 1
                    continue
                tweets[t["id_str"]] = t

        threads = self._assemble_threads(tweets)
        counts["threads"] = sum(1 for g in threads if len(g) > 1)
        counts["standalone"] = sum(1 for g in threads if len(g) == 1)

        items, scores, skipped = [], [], 0
        for group in threads:
            text = "\n\n".join((t.get("full_text") or t.get("text") or "").strip()
                               for t in group)
            if len(group) == 1 and weighted_length(text) < MIN_STANDALONE_WEIGHT:
                skipped += 1
                continue
            score, signals = work_score(text)
            scores.append(score)
            if score < threshold:
                skipped += 1
                continue
            first = group[0]
            created = _iso(first.get("created_at", ""))
            likes = sum(int(t.get("favorite_count") or 0) for t in group)
            reposts = sum(int(t.get("retweet_count") or 0) for t in group)
            head = (f"<!-- source_type=public connector=x-archive "
                    f"posts={len(group)} likes={likes} reposts={reposts} "
                    f"work_relevance={score} -->")
            items.append(StagedItem(
                native_id=first["id_str"],
                title=f"X {'thread' if len(group) > 1 else 'post'} · {created[:10]}",
                text=f"{head}\n\n{text}\n",
                created_at=created,
                updated_at=_iso(group[-1].get("created_at", "")) or created,
                meta={"source_type": self.source_type, "posts": len(group), "likes": likes,
                      "reposts": reposts, "work_relevance": score, "signals": signals},
            ))

        self.last_skipped = skipped
        self.last_histogram = histogram(scores)
        self.last_counts = counts
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items[:limit] if limit else items

    @staticmethod
    def _assemble_threads(tweets: dict[str, dict]) -> list[list[dict]]:
        """Chain self-replies into threads, oldest post first."""
        children: dict[str, list[str]] = {}
        roots: list[str] = []
        for tid, t in tweets.items():
            parent = t.get("in_reply_to_status_id_str")
            if parent and parent in tweets:
                children.setdefault(parent, []).append(tid)
            else:
                roots.append(tid)

        def walk(tid: str) -> list[dict]:
            chain = [tweets[tid]]
            for child in sorted(children.get(tid, []),
                                key=lambda c: tweets[c].get("created_at", "")):
                chain.extend(walk(child))
            return chain

        return [walk(r) for r in sorted(roots, key=lambda r: tweets[r].get("created_at", ""))]
