"""Shared machinery for AI session transcripts.

Claude Code and Codex record the same underlying thing -- a person thinking
out loud at an assistant -- in different envelopes. Everything downstream
(scoring, excerpting, the self-concept rule) keys off the rendered transcript,
so the two adapters must agree on that rendering exactly. They do by parsing
their own format into `Turn`s and handing them to `build_item` here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..relevance import weighted_length
from .base import StagedItem

ASSISTANT_STUB = 600      # assistant prose is context, not evidence
MIN_HUMAN_WEIGHT = 200    # below this it is "fix this typo"

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

# Harness scaffolding that arrives wearing the user's role. Codex in particular
# replays the environment preamble as a user message on every session, and
# counting it as "your words" would make every session look substantial.
SCAFFOLD = re.compile(
    r"^\s*<(?:environment_context|user_instructions|INSTRUCTIONS|system|"
    r"editor_context|plan_mode|ide_context)\b"
    r"|^\s*#\s*(?:AGENTS\.md|CLAUDE\.md)\b"
    r"|^\s*<\?xml", re.I)


def is_scaffold(text: str) -> bool:
    return bool(SCAFFOLD.match(text.strip()[:200]))


def minutes_between(first: str, last: str) -> int:
    try:
        a = datetime.fromisoformat(first.replace("Z", "+00:00"))
        b = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return max(0, int((b - a).total_seconds() // 60))
    except (ValueError, AttributeError):
        return 0


def iso_minutes(ts: str) -> str:
    return ts[:16].replace("T", " ") if ts else ""


@dataclass
class Turn:
    role: str            # "you" | "assistant"
    text: str
    ts: str = ""
    tools: list[str] = field(default_factory=list)


def build_item(*, native_id: str, tool: str, turns: list[Turn], tool_tally: dict[str, int],
               cwd: str, branch: str, timestamps: list[str], min_weight: int,
               source_type: str = "chat", extra_meta: dict | None = None,
               unparsed: int = 0) -> StagedItem | None:
    """Assemble a staged transcript, or None if the person barely spoke."""
    human = [t for t in turns if t.role == "you"]
    human_chars = sum(len(t.text) for t in human)
    human_weight = sum(weighted_length(t.text) for t in human)
    if not human or human_weight < min_weight:
        return None

    joined = "\n".join(t.text for t in human)
    first, last = (min(timestamps), max(timestamps)) if timestamps else ("", "")
    meta = {
        "tool": tool,
        "repo": Path(cwd).name if cwd else "",
        "cwd": cwd,
        "branch": branch,
        "human_turns": len(human),
        "human_chars": human_chars,
        "human_weight": human_weight,
        "assistant_turns": len(turns) - len(human),
        "tool_calls": sum(tool_tally.values()),
        "tools": dict(sorted(tool_tally.items(), key=lambda kv: -kv[1])[:8]),
        "pushback": len(PUSHBACK.findall(joined)),
        "teaching": len(TEACHING.findall(joined)),
        "duration_minutes": minutes_between(first, last),
        "unparsed_records": unparsed,
        "source_type": source_type,
    }
    meta.update(extra_meta or {})
    return StagedItem(
        native_id=native_id,
        title=f"{tool} session · {meta['repo'] or 'unknown repo'} · {first[:10]}",
        text=render(turns, meta), created_at=first, updated_at=last, meta=meta,
    )


def render(turns: list[Turn], meta: dict) -> str:
    head = [
        f"<!-- source_type={meta.get('source_type', 'chat')} connector={meta.get('tool')}",
        f"     repo={meta['repo']} branch={meta['branch']}",
        f"     human_turns={meta['human_turns']} human_chars={meta['human_chars']} "
        f"human_weight={meta['human_weight']}",
        f"     pushback={meta['pushback']} teaching={meta['teaching']} "
        f"tool_calls={meta['tool_calls']} duration_min={meta['duration_minutes']}",
        "     Human turns are verbatim. Assistant turns are stubs for context only --",
        "     they are the model's words, never evidence about the person. -->",
        "",
    ]
    if meta.get("unparsed_records"):
        head.insert(-1, f"     <!-- {meta['unparsed_records']} record(s) in an unrecognised "
                        f"shape were skipped -->")
    body = []
    for t in turns:
        if t.role == "you":
            body.append(f"\n## you · {iso_minutes(t.ts)}\n\n{t.text.strip()}")
        else:
            text = t.text.strip()
            if len(text) > ASSISTANT_STUB:
                text = text[:ASSISTANT_STUB].rstrip() + f" … [+{len(text) - ASSISTANT_STUB} chars]"
            tally: dict[str, int] = {}
            for name in t.tools:
                tally[name] = tally.get(name, 0) + 1
            trace = ("  <!-- tools: " + ", ".join(f"{k}x{v}" for k, v in tally.items())
                     + " -->") if tally else ""
            body.append(f"\n## assistant{trace}\n\n{text}")
    return "\n".join(head + body) + "\n"
