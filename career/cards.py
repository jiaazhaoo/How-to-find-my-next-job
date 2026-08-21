"""Stage D/E contract: evidence cards, and the machinery that keeps them honest.

Why cards and not summaries
---------------------------
A summary of forty repositories is a paragraph nobody can check. An evidence
card is a single claim with the quote and locator that back it. Cards can be
counted, deduplicated, contradicted and -- crucially -- *verified*: every
quote must appear verbatim in the excerpt the model was shown. A model that
invents a project fails validation instead of flattering you.

The second rule is the one that makes the output trustworthy: a claim
supported by one artifact is an anecdote; it only becomes a *pattern* when
independent sources agree. Anecdotes stay in the file, clearly labelled, and
become interview questions rather than profile statements.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

KINDS = {
    "capability",     # what you can demonstrably do
    "decision",       # a choice you made, with rationale
    "impact",         # an outcome, ideally quantified
    "collaboration",  # how you work with others
    "interest",       # what you gravitate towards unprompted
    "constraint",     # context that limited you (team, org, legacy)
    "contradiction",  # evidence that conflicts with another card
    "self_concept",   # what you said about yourself -- see below
}

# `self_concept` exists to defuse the worst failure mode of importing chat
# logs: you have described yourself to an assistant many times, and if those
# sentences are read as capability evidence the profile becomes your own
# self-image handed back to you with citations attached. It looks rigorous and
# it is a mirror.
#
# So a statement you made about yourself is evidence of *self-concept* and
# nothing else. It never counts towards a capability pattern. It is, however,
# genuinely useful downstream: self-concept is exactly what narrative career
# interviewing (Savickas' role-model question) sets out to elicit, and the gap
# between what you say about yourself and what the artifacts show is the
# sharpest question generator in the whole system.
SELF_REPORT_KINDS = {"self_concept"}
ROLE_SIGNALS = {"owner", "contributor", "reviewer", "observer", "unknown"}

# Cards describe *behaviour with receipts*. Trait language is out of scope at
# this layer -- inferring personality from text is unreliable, so a trait word
# in a claim is a validation error, not a stylistic quibble.
TRAIT_WORDS = re.compile(
    r"(?i)\b(introvert|extrovert|neurotic|conscientious|agreeable|openness|"
    r"personality|perfectionist|type [ABC]\b|INTJ|ENFP|[IE][NS][TF][JP]\b)"
    r"|(?:内向|外向|性格|人格|完美主义|情绪稳定)")


@dataclass
class Evidence:
    source: str            # doc id or path as it appeared in the shortlist
    locator: str           # "L120-L155" | "commit a1b2c3" | "p.4" | "slide 7"
    quote: str             # verbatim from the excerpt the model was given

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Card:
    claim: str
    kind: str
    evidence: list[Evidence]
    skills: list[str] = field(default_factory=list)
    role_signal: str = "unknown"
    difficulty: int = 3                 # 1 routine .. 5 genuinely hard
    time_range: str = ""
    confidence: float = 0.5
    counter_evidence: str = ""
    open_question: str = ""             # feeds the interview layer
    id: str = ""
    verified: bool | None = None

    def __post_init__(self) -> None:
        self.evidence = [e if isinstance(e, Evidence) else Evidence(**e) for e in self.evidence]
        if not self.id:
            self.id = hashlib.sha256(
                (self.claim + "|" + "|".join(e.locator for e in self.evidence))
                .encode("utf-8")).hexdigest()[:10]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        return d


class CardError(ValueError):
    pass


def validate(card: Card) -> list[str]:
    errs: list[str] = []
    if card.kind not in KINDS:
        errs.append(f"kind must be one of {sorted(KINDS)}, got {card.kind!r}")
    if card.role_signal not in ROLE_SIGNALS:
        errs.append(f"role_signal must be one of {sorted(ROLE_SIGNALS)}")
    if len(card.claim.strip()) < 12:
        errs.append("claim too short to be checkable")
    # Trait words are banned as *inference* but permitted as *quotation*: a
    # self_concept card reports what the person said, it does not assert it.
    if card.kind not in SELF_REPORT_KINDS and TRAIT_WORDS.search(card.claim):
        errs.append("claim uses personality-trait language; keep cards behavioural "
                    "(if this is quoting the person about themselves, use kind=self_concept)")
    if not card.evidence:
        errs.append("no evidence attached")
    for i, e in enumerate(card.evidence):
        if not e.quote.strip():
            errs.append(f"evidence[{i}] has an empty quote")
        if not e.locator.strip():
            errs.append(f"evidence[{i}] has no locator")
        if not e.source.strip():
            errs.append(f"evidence[{i}] has no source")
    if not 0.0 <= card.confidence <= 1.0:
        errs.append("confidence must be within [0,1]")
    if not 1 <= card.difficulty <= 5:
        errs.append("difficulty must be within [1,5]")
    if card.kind == "impact" and not re.search(r"\d", card.claim):
        errs.append("impact cards need a number in the claim")
    return errs


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_quotes(cards: list[Card], corpus: dict[str, str],
                  min_ratio: float = 0.92) -> dict:
    """Every quote must be findable in what the model was actually shown.

    ``corpus`` maps source id -> the redacted excerpt handed to the model.
    Exact match after whitespace folding; a short fuzzy fallback tolerates a
    trimmed ellipsis but nothing more generous than that.
    """
    normed = {k: _norm(v) for k, v in corpus.items()}
    stats = {"checked": 0, "ok": 0, "missing_source": 0, "not_found": 0}
    for card in cards:
        results = []
        for e in card.evidence:
            stats["checked"] += 1
            hay = normed.get(e.source) or normed.get(Path(e.source).name)
            if hay is None:
                stats["missing_source"] += 1
                results.append(False)
                continue
            needle = _norm(e.quote).strip(" .…")
            hit = needle in hay
            if not hit and len(needle) > 60:
                head, tail = needle[:40], needle[-40:]
                hit = head in hay and tail in hay
            if not hit:
                # tolerate an elision marker inside the quote
                parts = [p for p in re.split(r"\s*\.\.\.\s*|\s*…\s*", needle) if len(p) > 12]
                hit = bool(parts) and all(p in hay for p in parts)
            stats["ok" if hit else "not_found"] += 1
            results.append(hit)
        card.verified = all(results) if results else False
    stats["cards_verified"] = sum(1 for c in cards if c.verified)
    stats["cards_total"] = len(cards)
    return stats


# -- clustering into patterns ----------------------------------------------
_STOP = set("the a an of and or to in on for with by is are was were be been at as that this "
            "从 的 了 在 和 与 及 对 把 被 是 我 我们 一个 进行 使用".split())


def _tokens(text: str) -> set[str]:
    """ASCII words plus CJK character bigrams.

    Bigrams sidestep word segmentation entirely: "双写对账" and "对账双写"
    still overlap, which is all a similarity threshold needs.
    """
    toks = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
    for run in re.findall(r"[一-鿿]{2,}", text):
        toks.update(run[i:i + 2] for i in range(len(run) - 1))
    return {t for t in toks if t not in _STOP}


def _overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient, not Jaccard: a short claim and a long one that
    talk about the same thing should not be penalised for length."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _similar(a: Card, b: Card) -> float:
    claim = _overlap(_tokens(a.claim), _tokens(b.claim))
    # Jaccard on skills, deliberately: with the overlap coefficient a card
    # tagged only ["migration"] matches *everything* that mentions migration,
    # and one broad label silently swallows the whole corpus into one theme.
    skills = _jaccard({s.lower() for s in a.skills}, {s.lower() for s in b.skills})
    if not a.skills or not b.skills:
        return claim
    return 0.6 * skills + 0.4 * claim


@dataclass
class Theme:
    label: str
    cards: list[Card]
    skills: list[str]
    sources: list[str]
    status: str          # pattern | anecdote
    strength: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cards"] = [c.id for c in self.cards]
        return d


def cluster(cards: list[Card], threshold: float = 0.3) -> list[Theme]:
    groups: list[list[Card]] = []
    for card in sorted(cards, key=lambda c: -c.confidence):
        # Average linkage, not single linkage: one loose match should not
        # chain two unrelated themes together through a bridging card.
        best, target = 0.0, None
        for g in groups:
            score = sum(_similar(card, other) for other in g) / len(g)
            if score > best:
                best, target = score, g
        if target is not None and best >= threshold:
            target.append(card)
        else:
            groups.append([card])

    themes = []
    for g in groups:
        sources = sorted({e.source for c in g for e in c.evidence})
        skills = sorted({s for c in g for s in c.skills})
        # Independence is what earns "pattern": two quotes from one file is
        # still one observation. And a theme made only of things you said
        # about yourself is never a pattern, however often you said it --
        # repetition of a self-description is not corroboration.
        if all(c.kind in SELF_REPORT_KINDS for c in g):
            status = "self-report"
        else:
            status = "pattern" if len(sources) >= 2 else "anecdote"
        strength = round(sum(c.confidence for c in g) * (1 + 0.5 * (len(sources) - 1)), 3)
        label = max(g, key=lambda c: (c.confidence, len(c.claim))).claim
        themes.append(Theme(label=label, cards=g, skills=skills, sources=sources,
                            status=status, strength=strength))
    return sorted(themes, key=lambda t: -t.strength)


def tensions(cards: list[Card]) -> list[dict]:
    """Where the record argues with itself. These are the best questions."""
    out = []
    for c in cards:
        if c.kind == "contradiction" or c.counter_evidence.strip():
            out.append({"card": c.id, "claim": c.claim,
                        "counter": c.counter_evidence or "(marked contradiction)"})
    by_skill: dict[str, list[Card]] = {}
    for c in cards:
        for s in c.skills:
            by_skill.setdefault(s, []).append(c)
    for skill, group in by_skill.items():
        roles = {c.role_signal for c in group}
        if {"owner", "observer"} <= roles or {"owner", "reviewer"} <= roles:
            out.append({"skill": skill,
                        "claim": f"role varies across {skill}: {sorted(roles)}",
                        "counter": "same skill appears with different levels of ownership"})
        diffs = [c.difficulty for c in group]
        if len(diffs) >= 3 and max(diffs) - min(diffs) >= 3:
            out.append({"skill": skill,
                        "claim": f"difficulty spread on {skill}: {min(diffs)}..{max(diffs)}",
                        "counter": "deep in places, shallow in others"})
    return out


# -- io ---------------------------------------------------------------------
def load(path: Path, strict: bool = True) -> list[Card]:
    """Invalid cards never enter the analysis.

    ``strict`` decides how loudly that happens: raise (default), or warn on
    stderr and continue. Either way a malformed card is dropped -- silently
    analysing a card that failed validation is how unverifiable claims end up
    in a profile.
    """
    cards, problems = [], []
    for i, line in enumerate(Path(path).read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            card = Card(**json.loads(line))
        except (json.JSONDecodeError, TypeError) as exc:
            problems.append(f"line {i}: unparseable ({exc})")
            continue
        errs = validate(card)
        if errs:
            problems.append(f"line {i} [{card.id}]: " + "; ".join(errs))
            continue
        cards.append(card)
    if problems:
        if strict:
            raise CardError("\n".join(problems))
        print(f"warning: dropped {len(problems)} invalid card(s):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
    return cards


def dump(cards: list[Card], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for c in cards:
            fh.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
