"""Local Claude Code session logs -> staged transcripts.

The highest-value source in the whole pipeline, for a reason nothing else
supplies: a repository records what shipped, a session log records what you
*tried*. Abandoned approaches, the questions you had to ask, the places you
argued back -- none of that survives into a commit.

Two properties make these logs unusually good evidence:

* They are already joined to the work. Every record carries `cwd` and
  `gitBranch`, so a session sits next to the commits it produced.
* They are unprompted. Nobody assigned you the question you asked at 11pm on
  a Sunday, which makes the topic distribution a revealed preference rather
  than a self-report.

The extraction problem is that a 315-record session contains roughly 5 human
turns. Tool results, thinking blocks and queue operations are the other 98%,
and they are noise for this purpose. So this adapter keeps the human turns in
full, trims assistant prose to context-sized stubs, and reduces tool traffic
to a one-line tally.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .base import Connector, StagedItem

DEFAULT_ROOTS = ["~/.claude/projects"]

# Where the person overrides, corrects or redirects the model. Dense pushback
# is the strongest available signal that they know the domain -- you cannot
# argue with an assistant about something you do not understand.
PUSHBACK = re.compile(
    r"(?:不对|不是这样|其实|错了|应该是|重来|我更想|别这样|不要这样|反了|漏了|少了|"
    r"我觉得不是|你搞错|再想想)"
    r"|\b(?:actually|that's wrong|not quite|no,|nope|instead|rather than|revert|undo|"
    r"you missed|you're wrong|re-?read)\b", re.I)

# The person explaining something *to* the model, at length.
TEACHING = re.compile(
    r"(?:因为|原因是|背景是|我们当时|历史原因|注意|前提是|约束是)"
    r"|\b(?:context:|background:|the reason|note that|constraint|because we)\b", re.I)

ASSISTANT_STUB = 600      # assistant prose is context, not evidence
MIN_HUMAN_WEIGHT = 200    # below this it is "fix this typo"
CJK_WEIGHT = 2.5          # a Chinese character carries far more than a latin one


def weighted_length(text: str) -> int:
    """Length in rough information units, so one threshold works for both
    languages: 200 units is ~200 English characters or ~80 Chinese ones."""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cjk * CJK_WEIGHT + (len(text) - cjk))


def _text_blocks(content) -> tuple[list[str], list[str], bool]:
    """Return (text_parts, tool_names, has_tool_result)."""
    if isinstance(content, str):
        return ([content] if content.strip() else []), [], False
    texts, tools, tool_result = [], [], False
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and block.get("text", "").strip():
                texts.append(block["text"])
            elif kind == "tool_use":
                tools.append(block.get("name", "?"))
            elif kind == "tool_result":
                tool_result = True
    return texts, tools, tool_result


def _iso_minutes(ts: str) -> str:
    return ts[:16].replace("T", " ") if ts else ""


class ClaudeCodeConnector(Connector):
    name = "claude-code"
    source_type = "chat"
    description = "Local Claude Code session logs (~/.claude/projects/**/*.jsonl)"

    def available(self) -> tuple[bool, str]:
        roots = [Path(r).expanduser() for r in DEFAULT_ROOTS]
        found = [r for r in roots if r.exists()]
        if not found:
            return False, f"no session directory at {', '.join(str(r) for r in roots)}"
        n = sum(len(list(r.rglob("*.jsonl"))) for r in found)
        return n > 0, f"{n} session file(s) under {found[0]}"

    def fetch(self, config: dict, limit: int | None = None) -> list[StagedItem]:
        roots = [Path(r).expanduser() for r in config.get("roots", DEFAULT_ROOTS)]
        min_weight = int(config.get("min_human_chars", MIN_HUMAN_WEIGHT))
        files: list[Path] = []
        for root in roots:
            if root.exists():
                files.extend(sorted(root.rglob("*.jsonl")))

        items: list[StagedItem] = []
        for path in files:
            item = self._parse_session(path, min_weight)
            if item:
                items.append(item)
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items[:limit] if limit else items

    # ------------------------------------------------------------------
    def _parse_session(self, path: Path, min_weight: int) -> StagedItem | None:
        try:
            records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            return None

        turns: list[dict] = []
        tool_tally: dict[str, int] = {}
        cwd = branch = ""
        timestamps: list[str] = []
        thinking_blocks = 0
        pending_tools: list[str] = []

        for rec in records:
            rtype = rec.get("type")
            if rtype not in ("user", "assistant"):
                continue
            # Sidechain traffic is a subagent talking to itself, not the person.
            if rec.get("isSidechain"):
                continue
            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            cwd = cwd or rec.get("cwd", "")
            branch = branch or rec.get("gitBranch", "")
            ts = rec.get("timestamp", "")
            if ts:
                timestamps.append(ts)

            texts, tools, tool_result = _text_blocks(message.get("content"))
            for t in tools:
                tool_tally[t] = tool_tally.get(t, 0) + 1
                pending_tools.append(t)
            if isinstance(message.get("content"), list):
                thinking_blocks += sum(1 for b in message["content"]
                                       if isinstance(b, dict) and b.get("type") == "thinking")

            if rtype == "user":
                # A `user` record whose content is a tool_result is machine
                # output wearing the user's name. Only real prose counts.
                if tool_result or not texts:
                    continue
                turns.append({"role": "you", "ts": ts, "text": "\n".join(texts)})
            elif texts:
                turns.append({"role": "assistant", "ts": ts, "text": "\n".join(texts),
                              "tools": pending_tools[:]})
                pending_tools = []

        human = [t for t in turns if t["role"] == "you"]
        human_chars = sum(len(t["text"]) for t in human)
        human_weight = sum(weighted_length(t["text"]) for t in human)
        if not human or human_weight < min_weight:
            return None

        joined = "\n".join(t["text"] for t in human)
        meta = {
            "repo": Path(cwd).name if cwd else "",
            "cwd": cwd,
            "branch": branch,
            "human_turns": len(human),
            "human_chars": human_chars,
            "human_weight": human_weight,
            "assistant_turns": len(turns) - len(human),
            "tool_calls": sum(tool_tally.values()),
            "tools": dict(sorted(tool_tally.items(), key=lambda kv: -kv[1])[:8]),
            "thinking_blocks": thinking_blocks,
            "pushback": len(PUSHBACK.findall(joined)),
            "teaching": len(TEACHING.findall(joined)),
            "source_type": self.source_type,
        }
        first, last = (min(timestamps), max(timestamps)) if timestamps else ("", "")
        meta["duration_minutes"] = _minutes_between(first, last)

        return StagedItem(
            native_id=path.stem,
            title=f"Claude Code session · {meta['repo'] or 'unknown repo'} · {first[:10]}",
            text=self._render(turns, meta),
            created_at=first, updated_at=last, meta=meta,
        )

    @staticmethod
    def _render(turns: list[dict], meta: dict) -> str:
        head = [
            "<!-- source_type=chat connector=claude-code",
            f"     repo={meta['repo']} branch={meta['branch']}",
            f"     human_turns={meta['human_turns']} human_chars={meta['human_chars']} "
            f"human_weight={meta['human_weight']}",
            f"     pushback={meta['pushback']} teaching={meta['teaching']} "
            f"tool_calls={meta['tool_calls']} duration_min={meta['duration_minutes']}",
            "     Human turns are verbatim. Assistant turns are stubs for context only --",
            "     they are the model's words, never evidence about the person. -->",
            "",
        ]
        body = []
        for t in turns:
            if t["role"] == "you":
                body.append(f"\n## you · {_iso_minutes(t['ts'])}\n\n{t['text'].strip()}")
            else:
                text = t["text"].strip()
                if len(text) > ASSISTANT_STUB:
                    text = text[:ASSISTANT_STUB].rstrip() + f" … [+{len(text) - ASSISTANT_STUB} chars]"
                tools = t.get("tools") or []
                tally: dict[str, int] = {}
                for name in tools:
                    tally[name] = tally.get(name, 0) + 1
                trace = ("  <!-- tools: " +
                         ", ".join(f"{k}x{v}" for k, v in tally.items()) + " -->") if tally else ""
                body.append(f"\n## assistant{trace}\n\n{text}")
        return "\n".join(head + body) + "\n"


def _minutes_between(first: str, last: str) -> int:
    try:
        a = datetime.fromisoformat(first.replace("Z", "+00:00"))
        b = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return max(0, int((b - a).total_seconds() // 60))
    except (ValueError, AttributeError):
        return 0
