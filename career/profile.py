"""Stage 5: assemble the profile, and refuse to let it drift off its evidence.

The profile is the most dangerous artifact in the whole pipeline. It is the
part a person will paste into a CV, and it is the part a language model will
happily improve into flattery. Everything here exists to make that harder.

Three mechanisms:

1. **A skeleton, not a blank page.** `build_skeleton` decides in Python what
   may be claimed at all -- only themes that reached `pattern` status, i.e.
   backed by two or more independent sources. The writing stage fills in
   prose; it does not choose the conclusions.

2. **Mandatory citation.** Every claim in the evidence section must carry card
   ids in brackets, and `validate` checks that each one resolves, belongs to a
   qualifying theme, and is not a self-report. An uncited sentence in that
   section is an error, not a style note.

3. **Named sections that cannot borrow each other's authority.** What the
   artifacts show, what you said about yourself, what appeared only once, and
   what the record simply does not contain are four different epistemic
   states. Collapsing them is how "I once wrote a migration script" becomes
   "seasoned migration architect".

The fourth section is the unusual one. Most profiles never say what they do
not know, which is exactly what makes them useless for deciding what to do
next -- and it is where the interview questions come from.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .cards import Card, Theme, SELF_REPORT_KINDS, TRAIT_WORDS
from .relevance import weighted_length

CITATION = re.compile(r"\[([0-9a-f]{6,12}(?:\s*,\s*[0-9a-f]{6,12})*)\]")
SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)

# Section keys are matched by marker, so the prose heading can be in any
# language as long as it carries the marker.
SECTIONS = {
    "evidence": "{{evidence}}",
    "self_reported": "{{self-reported}}",
    "single_source": "{{single-source}}",
    "gaps": "{{gaps}}",
}


@dataclass
class ThemeEntry:
    label: str
    skills: list[str]
    card_ids: list[str]
    sources: int
    strength: float
    role_signals: list[str]
    difficulty_max: int
    best_quote: str = ""
    time_range: str = ""
    # What the person said about this area when asked. Kept separate from the
    # evidence on purpose, and separately surfaced because "demonstrably good
    # at X, explicitly does not want to keep doing X" is the single most
    # decision-relevant sentence a profile can contain -- and it is invisible
    # to any pipeline that only reads artifacts.
    stance: list[str] = field(default_factory=list)


@dataclass
class Skeleton:
    qualified: list[ThemeEntry] = field(default_factory=list)
    self_reported: list[dict] = field(default_factory=list)
    single_source: list[ThemeEntry] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def build_skeleton(cards: list[Card], themes: list[Theme],
                   open_questions: list[str] | None = None) -> Skeleton:
    """Decide what may be claimed. Written in Python on purpose."""
    by_id = {c.id: c for c in cards}
    skeleton = Skeleton()

    for theme in themes:
        entry = ThemeEntry(
            label=theme.label, skills=theme.skills,
            card_ids=[c.id for c in theme.cards], sources=len(theme.sources),
            strength=theme.strength,
            role_signals=sorted({c.role_signal for c in theme.cards}),
            difficulty_max=max((c.difficulty for c in theme.cards), default=0),
            best_quote=next((e.quote for c in sorted(theme.cards, key=lambda c: -c.confidence)
                             for e in c.evidence if e.quote), "")[:200],
            time_range=next((c.time_range for c in theme.cards if c.time_range), ""),
            stance=[c.claim for c in theme.cards
                    if c.kind in SELF_REPORT_KINDS
                    or any(e.source == "interview" for e in c.evidence)],
        )
        if theme.status == "pattern":
            skeleton.qualified.append(entry)
        elif theme.status == "anecdote":
            skeleton.single_source.append(entry)

    for card in cards:
        if card.kind in SELF_REPORT_KINDS:
            skeleton.self_reported.append({
                "card_id": card.id, "claim": card.claim, "skills": card.skills,
                "corroborated": _corroborated(card, cards),
            })

    skeleton.gaps = list(open_questions or [])
    for card in cards:
        if card.open_question.strip() and card.open_question not in skeleton.gaps:
            skeleton.gaps.append(card.open_question.strip())

    checked = [c for c in cards if c.verified is not None]
    verified = [c for c in cards if c.verified]
    skeleton.stats = {
        "cards": len(cards),
        "quote_check_ran": bool(checked),
        "verified": len(verified),
        "failed": len(checked) - len(verified),
        "patterns": len(skeleton.qualified),
        "anecdotes": len(skeleton.single_source),
        "self_reports": len(skeleton.self_reported),
        "citable_ids": len({cid for e in skeleton.qualified for cid in e.card_ids}),
    }
    _ = by_id
    return skeleton


def _corroborated(card: Card, cards: list[Card]) -> bool:
    """Does anything other than the person's own word support this?"""
    mine = {s.lower() for s in card.skills}
    if not mine:
        return False
    for other in cards:
        if other.id == card.id or other.kind in SELF_REPORT_KINDS:
            continue
        if mine & {s.lower() for s in other.skills}:
            return True
    return False


# -- validation -------------------------------------------------------------
@dataclass
class Problem:
    severity: str      # error | warning
    where: str
    message: str


def _split_sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, marker in SECTIONS.items():
        start = text.find(marker)
        if start < 0:
            continue
        rest = text[start + len(marker):]
        nxt = min([rest.find(m) for m in SECTIONS.values() if rest.find(m) >= 0] or [len(rest)])
        out[key] = rest[:nxt]
    return out


# Weighted, not raw: "擅长把复杂问题讲清楚。" is a complete assertion at 11
# characters and would slip under any threshold tuned on English.
MIN_CLAIM_WEIGHT = 24


SENTENCE_END = re.compile(r"(?<=[。！？!?])\s*|(?<=[.])\s+(?=[A-Z\u4e00-\u9fff])")
BULLET = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")


def _claim_units(block: str) -> list[str]:
    """Sentences that assert something.

    Deliberately not per line: markdown wraps, and a claim whose citation
    happens to land on the next line is still cited. Checking lines produced
    false errors on any normally-wrapped document, which would have made the
    validator something people turn off.
    """
    units: list[str] = []
    buffer = ""

    def flush():
        nonlocal buffer
        text = buffer.strip()
        buffer = ""
        if not text:
            return
        for sentence in SENTENCE_END.split(text):
            sentence = (sentence or "").strip()
            if not sentence:
                continue
            if weighted_length(re.sub(r"[\s\-*0-9.]", "", sentence)) < MIN_CLAIM_WEIGHT:
                continue
            units.append(sentence)

    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "|", "---", "<!--")):
            flush()
            continue
        if BULLET.match(raw):
            flush()                       # each bullet is its own claim
        buffer = f"{buffer} {line}".strip() if buffer else line
    flush()
    return units


def validate(text: str, cards: list[Card], skeleton: Skeleton) -> list[Problem]:
    by_id = {c.id: c for c in cards}
    citable = {cid for e in skeleton.qualified for cid in e.card_ids}
    problems: list[Problem] = []

    sections = _split_sections(text)

    # Already forbidden at the card layer; enforcing it here too closes the
    # loop, since the profile is where trait language would actually land.
    for key, block in sections.items():
        for line in _claim_units(block):
            hit = TRAIT_WORDS.search(line)
            if hit:
                problems.append(Problem(
                    "error", key,
                    f"人格特质用语「{hit.group(0)}」：{line[:40]}"))

    for key in SECTIONS:
        if key not in sections:
            problems.append(Problem("error", key, f"缺少 {SECTIONS[key]} 段落"))

    for line in _claim_units(sections.get("evidence", "")):
        found = CITATION.findall(line)
        if not found:
            problems.append(Problem("error", "evidence", f"无引用的断言：{line[:60]}"))
            continue
        for group in found:
            for cid in re.split(r"\s*,\s*", group):
                card = by_id.get(cid)
                if card is None:
                    problems.append(Problem("error", "evidence", f"引用了不存在的卡片 {cid}"))
                    continue
                if card.kind in SELF_REPORT_KINDS:
                    problems.append(Problem(
                        "error", "evidence",
                        f"{cid} 是自述卡片，不能当作证据使用（应放在自述段落）"))
                elif cid not in citable:
                    problems.append(Problem(
                        "error", "evidence",
                        f"{cid} 不属于任何 pattern 主题（单一来源，不能进证据段）"))
                elif card.verified is False:
                    problems.append(Problem(
                        "warning", "evidence", f"{cid} 的引用未通过校验"))

    for key in ("self_reported", "single_source"):
        for line in _claim_units(sections.get(key, "")):
            if not CITATION.search(line):
                problems.append(Problem("warning", key, f"建议加引用：{line[:60]}"))

    if not _claim_units(sections.get("gaps", "")):
        problems.append(Problem(
            "error", "gaps",
            "空白段落不能为空——记录读不出来的东西必须写明，否则画像会被当成完整的"))
    return problems


def dump_skeleton(skeleton: Skeleton, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(skeleton.to_dict(), ensure_ascii=False, indent=2), "utf-8")
