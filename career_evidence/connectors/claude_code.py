"""Local Claude Code session logs -> staged transcripts.

A repository records what shipped; a session log records what was *tried*.
Abandoned approaches, the questions someone had to ask, the places they argued
back -- none of that survives into a commit.

Two properties make these logs unusually good evidence:

* They are already joined to the work. Every record carries `cwd` and
  `gitBranch`, so a session sits next to the commits it produced.
* They are unprompted. Nobody assigned you the question you asked at 11pm on
  a Sunday, which makes the topic distribution a revealed preference rather
  than a self-report.

The extraction problem is that a 315-record session contains roughly 5 human
turns. Tool results, thinking blocks and queue operations are the other 98%.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Connector, StagedItem
from .chatlog import MIN_HUMAN_WEIGHT, Turn, build_item, is_scaffold

DEFAULT_ROOTS = ["~/.claude/projects"]


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


class ClaudeCodeConnector(Connector):
    name = "claude-code"
    source_type = "chat"
    description = "Local Claude Code session logs (~/.claude/projects/**/*.jsonl)"

    def available(self, config: dict | None = None) -> tuple[bool, str]:
        roots = [Path(r).expanduser()
                 for r in (config or {}).get("roots", DEFAULT_ROOTS)]
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

        items = [i for i in (self._parse_session(p, min_weight) for p in files) if i]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items[:limit] if limit else items

    def _parse_session(self, path: Path, min_weight: int) -> StagedItem | None:
        try:
            records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            return None

        turns: list[Turn] = []
        tool_tally: dict[str, int] = {}
        cwd = branch = ""
        timestamps: list[str] = []
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

            if rtype == "user":
                # A `user` record whose content is a tool_result is machine
                # output wearing the user's name. Only real prose counts.
                if tool_result or not texts:
                    continue
                body = "\n".join(texts)
                if is_scaffold(body):
                    continue
                turns.append(Turn(role="you", ts=ts, text=body))
            elif texts:
                turns.append(Turn(role="assistant", ts=ts, text="\n".join(texts),
                                  tools=pending_tools[:]))
                pending_tools = []

        return build_item(native_id=path.stem, tool="claude-code", turns=turns,
                          tool_tally=tool_tally, cwd=cwd, branch=branch,
                          timestamps=timestamps, min_weight=min_weight)
