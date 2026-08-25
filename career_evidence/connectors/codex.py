"""OpenAI Codex CLI rollout logs -> staged transcripts.

Same evidence as Claude Code sessions, different envelope. Worth doing purely
for coverage: if you split your work across two assistants and only one is
imported, the corpus is biased toward whatever you happened to use in that
tool, and the resulting portrait inherits the bias.

Codex writes one JSONL per session under
``~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl``. Each line
is a ``RolloutLine``: ``{timestamp, ordinal, type, payload}``, where `type` is
`session_meta`, `response_item`, `event_msg`, `turn_context`, `compacted` and
so on.

The format has churned across releases, so this parser is deliberately
shape-tolerant rather than schema-exact: it looks for any of the known
envelopes, falls back to a bare Responses-API item, and *counts what it could
not read* so an unrecognised future format shows up as a number in the report
instead of a silently empty import.

Two Codex-specific traps:

* The environment preamble arrives as a `user` message on every session. It is
  the harness talking, not the person, and counting it would make every
  session look substantial. Filtered by `chatlog.is_scaffold`.
* The same prompt usually appears twice -- once as an `event_msg/user_message`
  and once as the `response_item` fed to the model. Undeduplicated, every
  metric that counts your words doubles.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import Connector, StagedItem
from .chatlog import MIN_HUMAN_WEIGHT, Turn, build_item, is_scaffold

DEFAULT_ROOTS = ["~/.codex/sessions"]

TEXT_BLOCK_TYPES = {"input_text", "output_text", "text", "summary_text"}
TOOL_ITEM_TYPES = {"function_call", "local_shell_call", "custom_tool_call", "shell_call",
                   "web_search_call", "apply_patch_call"}
SKIP_ITEM_TYPES = {"function_call_output", "local_shell_call_output", "custom_tool_call_output",
                   "reasoning", "shell_call_output"}

# Line-level envelopes that legitimately carry no turn.
NON_TURN_LINES = {"session_meta", "turn_context", "compacted", "world_state",
                  "security_risk_score", "inter_agent_communication",
                  "inter_agent_communication_metadata", "event_msg"}

SKIP = "skip"   # recognised, deliberately not a turn


def _blocks_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in TEXT_BLOCK_TYPES:
            parts.append(block.get("text") or "")
    return "\n".join(p for p in parts if p.strip())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class CodexConnector(Connector):
    name = "codex"
    source_type = "chat"
    description = "OpenAI Codex CLI rollout logs (~/.codex/sessions/**/rollout-*.jsonl)"

    def available(self, config: dict | None = None) -> tuple[bool, str]:
        roots = [Path(r).expanduser()
                 for r in (config or {}).get("roots", DEFAULT_ROOTS)]
        found = [r for r in roots if r.exists()]
        if not found:
            return False, f"no rollout directory at {', '.join(str(r) for r in roots)}"
        n = sum(len(list(r.rglob("*.jsonl"))) for r in found)
        return n > 0, f"{n} rollout file(s) under {found[0]}"

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

    # ------------------------------------------------------------------
    def _parse_session(self, path: Path, min_weight: int) -> StagedItem | None:
        try:
            lines = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            return None

        turns: list[Turn] = []
        tool_tally: dict[str, int] = {}
        pending_tools: list[str] = []
        seen_human: set[str] = set()
        cwd = branch = ""
        timestamps: list[str] = []
        unparsed = 0

        for line in lines:
            ts = line.get("timestamp") or ""
            kind = line.get("type")
            payload = line.get("payload") if isinstance(line.get("payload"), dict) else None

            if kind == "session_meta" or (payload and "cwd" in payload and kind != "response_item"):
                meta = payload or line
                cwd = cwd or meta.get("cwd", "") or ""
                git = meta.get("git") if isinstance(meta.get("git"), dict) else {}
                branch = branch or (git.get("branch") or "")
                if ts:
                    timestamps.append(ts)
                continue

            # Known envelopes, then the bare item as a fallback.
            item = payload if payload is not None else line
            role, text, tool = self._interpret(item)
            if tool:
                tool_tally[tool] = tool_tally.get(tool, 0) + 1
                pending_tools.append(tool)
                continue
            if role is None:
                # Only genuinely unrecognised shapes count. The point of this
                # number is to make a future format change visible instead of
                # returning a silently empty import, so counting deliberate
                # skips would defeat it -- and `event_msg` carries dozens of
                # progress events we never intend to read.
                if kind not in NON_TURN_LINES:
                    unparsed += 1
                continue
            if role == SKIP:
                continue
            if ts:
                timestamps.append(ts)

            if role == "you":
                if not text.strip() or is_scaffold(text):
                    continue
                key = _norm(text)[:400]
                if key in seen_human:
                    continue          # event_msg / response_item duplicate
                seen_human.add(key)
                turns.append(Turn(role="you", ts=ts, text=text))
            else:
                if not text.strip():
                    continue
                turns.append(Turn(role="assistant", ts=ts, text=text, tools=pending_tools[:]))
                pending_tools = []

        return build_item(native_id=path.stem, tool="codex", turns=turns,
                          tool_tally=tool_tally, cwd=cwd, branch=branch,
                          timestamps=timestamps, min_weight=min_weight, unparsed=unparsed)

    @staticmethod
    def _interpret(item: dict) -> tuple[str | None, str, str | None]:
        """Map one payload onto (role, text, tool_name). Role None = not a turn."""
        if not isinstance(item, dict):
            return None, "", None
        itype = item.get("type")

        if itype in SKIP_ITEM_TYPES:
            return SKIP, "", None
        if itype in TOOL_ITEM_TYPES:
            name = item.get("name") or ("shell" if "shell" in (itype or "") else itype)
            return None, "", name

        # event_msg payloads
        if itype == "user_message":
            return "you", item.get("message") or "", None
        if itype in ("agent_message", "agent_message_delta"):
            return "assistant", item.get("message") or "", None

        # response_item / bare Responses API item
        if itype == "message" or "role" in item:
            role = item.get("role")
            text = _blocks_to_text(item.get("content"))
            if role == "user":
                return "you", text, None
            if role == "assistant":
                return "assistant", text, None
            if role in ("system", "developer"):
                return SKIP, "", None
        return None, "", None
