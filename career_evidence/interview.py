"""Stage 6: turn the record's disagreements with itself into questions.

Why this stage exists at all
----------------------------
Everything before it can only rearrange what the artifacts already contain.
This is the only stage that produces *new* information -- your answers -- and
that matters more than it sounds, because artifacts have one structural blind
spot they can never fill:

    A repository can prove you are good at something.
    It can never show whether you still want to do it.

So the question generator treats "strong evidence, no sign you enjoy it" as a
first-class thing to ask about, not an afterthought.

How candidates are found
------------------------
Deterministically, from the card graph -- not by asking a model what it would
like to know. A question earns its place by pointing at a specific place where
the record is thin, self-contradictory, or self-reported. Every candidate
carries the card ids that provoked it, so you can see why you are being asked.

The two filters that kill most questions:

* **Already answered.** If the cards contain the answer, asking wastes a turn.
* **No consequence.** If every possible answer leads to the same profile, the
  question carries no information, however interesting it sounds.

Grounding
---------
The angles come from Savickas' Career Construction Interview, which elicits
career narrative through five prompts -- role models, preferred environments,
favourite stories, mottos, early recollections -- and listens for life themes
across the answers. This is an adaptation, not the instrument: rather than
asking the five stock questions, each candidate is tagged with the CCI purpose
it serves, and the phrasing is built from *this person's* contradictions. The
instrument's value here is the set of angles, not its script.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .cards import Card, Theme, SELF_REPORT_KINDS

# Savickas' prompts, restated as the purpose each one serves. A candidate is
# tagged with the purpose it advances, so a question set can be checked for
# lopsidedness (five questions all probing self-concept is a bad interview).
CCI_ANGLES = {
    "self_concept": "how you see yourself (CCI: role models)",
    "environment": "the settings and problems you gravitate to (CCI: favourite media)",
    "script": "how you expect change to happen (CCI: favourite stories)",
    "advice": "the rule you would give someone in your position (CCI: mottos)",
    "preoccupation": "what is unresolved for you right now (CCI: early recollections)",
}

# Per-kind priority. `capability_without_interest` leads deliberately: it is
# the one thing the artifacts structurally cannot answer.
KIND_WEIGHT = {
    "capability_without_interest": 3.0,
    "unverified_claim": 2.6,
    "self_report_gap": 2.6,
    "ownership_dispute": 2.2,
    "dormant_strength": 2.0,
    "counter_evidence": 1.9,
    "interest_without_capability": 1.8,
    "difficulty_spread": 1.5,
    "single_source": 1.3,
    "card_open_question": 1.2,
}

KIND_ANGLE = {
    "capability_without_interest": "preoccupation",
    "unverified_claim": "self_concept",
    "self_report_gap": "self_concept",
    "ownership_dispute": "script",
    "dormant_strength": "preoccupation",
    "counter_evidence": "advice",
    "interest_without_capability": "environment",
    "difficulty_spread": "advice",
    "single_source": "environment",
    "card_open_question": "advice",
}

EVIDENCE_KINDS = {"capability", "decision", "impact"}
YEAR = re.compile(r"(19|20)\d{2}")


@dataclass
class Candidate:
    kind: str
    subject: str                     # the skill or theme in question
    observation: str                 # what the record shows, in plain terms
    resolves: str                    # what an answer would settle
    card_ids: list[str] = field(default_factory=list)
    angle: str = "advice"
    weight: float = 1.0
    draft: str = ""                  # a serviceable phrasing; the skill improves it

    def to_dict(self) -> dict:
        d = asdict(self)
        d["angle_meaning"] = CCI_ANGLES.get(self.angle, "")
        return d


def _by_skill(cards: list[Card]) -> dict[str, list[Card]]:
    out: dict[str, list[Card]] = {}
    for card in cards:
        for skill in card.skills:
            out.setdefault(skill.lower(), []).append(card)
    return out


def _latest_year(cards: list[Card]) -> int | None:
    years = [int(m.group(0)) for c in cards for m in [YEAR.search(c.time_range or "")] if m]
    return max(years) if years else None


def generate(cards: list[Card], themes: list[Theme] | None = None,
             now_year: int | None = None) -> list[Candidate]:
    """Mine the card graph for places worth asking about."""
    themes = themes or []
    out: list[Candidate] = []
    by_skill = _by_skill(cards)

    for skill, group in by_skill.items():
        evidence = [c for c in group if c.kind in EVIDENCE_KINDS]
        interest = [c for c in group if c.kind == "interest"]
        self_reports = [c for c in group if c.kind in SELF_REPORT_KINDS]
        strong = [c for c in evidence if c.difficulty >= 4 or c.role_signal == "owner"]

        # The blind spot: proven, but nothing says you still want it.
        if len(evidence) >= 2 and strong and not interest:
            out.append(Candidate(
                kind="capability_without_interest", subject=skill,
                observation=f"{len(evidence)} 条证据显示你在 {skill} 上做过实事"
                            f"（其中 {len(strong)} 条是主导或高难度），但没有任何材料显示你"
                            f"在没人要求的时候主动碰它",
                resolves="这是你想继续做的事，还是你恰好很擅长的事——"
                         "作品永远回答不了这个问题",
                card_ids=[c.id for c in evidence[:4]],
                angle=KIND_ANGLE["capability_without_interest"],
                weight=KIND_WEIGHT["capability_without_interest"] * min(2.0, len(evidence) / 2),
                draft=f"关于 {skill}：你在这方面的记录很扎实。如果下一份工作完全不碰它，"
                      f"你会松一口气还是会觉得可惜？"))

        # Wanting without doing.
        if interest and not evidence:
            out.append(Candidate(
                kind="interest_without_capability", subject=skill,
                observation=f"{skill} 反复出现在你主动关注的材料里，但没有对应的产出证据",
                resolves="是没机会、试过没成、还是只停留在兴趣",
                card_ids=[c.id for c in interest[:3]],
                angle=KIND_ANGLE["interest_without_capability"],
                weight=KIND_WEIGHT["interest_without_capability"],
                draft=f"{skill} 你关注了不止一次，但没看到你真的做过。"
                      f"是没碰上机会，还是试过之后发现不是那么回事？"))

        # You said it; nothing shows it.
        if self_reports and not evidence:
            out.append(Candidate(
                kind="self_report_gap", subject=skill,
                observation=f"你自己提到过 {skill}，但作品里没有对应的痕迹",
                resolves="自述和产出之间的落差——是记录没留下来，还是自我认知需要修正",
                card_ids=[c.id for c in self_reports[:3]],
                angle=KIND_ANGLE["self_report_gap"],
                weight=KIND_WEIGHT["self_report_gap"],
                draft=f"你说过自己在 {skill} 上的情况，但材料里我找不到对应的东西。"
                      f"是这部分工作没留下记录，还是我该把它理解成一个方向而不是一段经历？"))

        roles = {c.role_signal for c in evidence}
        if {"owner", "observer"} <= roles or {"owner", "reviewer"} <= roles:
            out.append(Candidate(
                kind="ownership_dispute", subject=skill,
                observation=f"同样是 {skill}，有些地方你是主导，有些地方只是参与",
                resolves="你的真实位置，以及那个差别是能力问题还是环境问题",
                card_ids=[c.id for c in evidence[:4]],
                angle=KIND_ANGLE["ownership_dispute"],
                weight=KIND_WEIGHT["ownership_dispute"],
                draft=f"{skill} 上你有时是主导、有时是旁观。那个差别是怎么造成的——"
                      f"是团队分工，还是你自己选择不往前站？"))

        diffs = [c.difficulty for c in evidence]
        if len(diffs) >= 3 and max(diffs) - min(diffs) >= 3:
            out.append(Candidate(
                kind="difficulty_spread", subject=skill,
                observation=f"{skill} 上你的工作难度跨度很大（{min(diffs)} 到 {max(diffs)}）",
                resolves="深的地方是不是同一类问题，浅的地方是不是被别人挡住了",
                card_ids=[c.id for c in evidence[:4]],
                angle=KIND_ANGLE["difficulty_spread"],
                weight=KIND_WEIGHT["difficulty_spread"],
                draft=f"{skill} 里最难的那件事和最简单的那件事差得很远。"
                      f"最难那次，如果重来一遍你会怎么做？"))

        if now_year and len(evidence) >= 2:
            last = _latest_year(evidence)
            if last and now_year - last >= 3:
                out.append(Candidate(
                    kind="dormant_strength", subject=skill,
                    observation=f"{skill} 的证据集中在 {last} 年前后，之后没有了",
                    resolves="主动离开还是被环境推开——这两种情况指向完全不同的下一步",
                    card_ids=[c.id for c in evidence[:3]],
                    angle=KIND_ANGLE["dormant_strength"],
                    weight=KIND_WEIGHT["dormant_strength"],
                    draft=f"{skill} 你有几年没碰了。是主动放下的，还是换了环境就没机会了？"))

    # An outcome you asserted with nothing behind it. Distinct from
    # `self_report_gap`: there the skill has no trace at all, here there is a
    # specific quantified claim -- "cut it from 6 hours to 40 minutes" -- and
    # the artifacts do not show it. That claim is the kind of thing that ends
    # up in a CV, so it is worth settling before it does.
    number = re.compile(r"\d")
    for card in cards:
        if card.kind not in SELF_REPORT_KINDS or not number.search(card.claim):
            continue
        skills = {s.lower() for s in card.skills}
        corroborating = [o for o in cards
                         if o.id != card.id and o.kind in EVIDENCE_KINDS
                         and skills & {s.lower() for s in o.skills}]
        if corroborating:
            continue
        out.append(Candidate(
            kind="unverified_claim", subject=card.skills[0] if card.skills else "这个结果",
            observation=f"你自己说过「{card.claim[:40]}」，但作品里没有能佐证的东西",
            resolves="这个结果能不能拿出去讲——没有工件支撑的数字进了简历是要被追问的",
            card_ids=[card.id], angle=KIND_ANGLE["unverified_claim"],
            weight=KIND_WEIGHT["unverified_claim"],
            draft=f"你提到过「{card.claim[:35]}」。这件事有留下什么可以拿出来的东西吗——"
                  f"代码、文档、当时的数据？"))

    for card in cards:
        if card.counter_evidence.strip():
            out.append(Candidate(
                kind="counter_evidence", subject=card.skills[0] if card.skills else "这段经历",
                observation=f"「{card.claim[:40]}」，但同一批材料里也有反面线索",
                resolves="哪一边更接近实际情况",
                card_ids=[card.id], angle=KIND_ANGLE["counter_evidence"],
                weight=KIND_WEIGHT["counter_evidence"] * (1.5 - card.confidence),
                draft=f"关于「{card.claim[:30]}」——材料里有一处对不上：{card.counter_evidence[:60]}。"
                      f"实际是怎么回事？"))
        if card.open_question.strip():
            out.append(Candidate(
                kind="card_open_question",
                subject=card.skills[0] if card.skills else "这段经历",
                observation=f"读到「{card.claim[:40]}」时留下的问题",
                resolves="这条证据的归属和边界",
                card_ids=[card.id], angle=KIND_ANGLE["card_open_question"],
                weight=KIND_WEIGHT["card_open_question"] * (1.5 - card.confidence),
                draft=card.open_question.strip()))

    for theme in themes:
        if theme.status != "anecdote":
            continue
        hard = [c for c in theme.cards if c.difficulty >= 4]
        if hard:
            out.append(Candidate(
                kind="single_source", subject=theme.skills[0] if theme.skills else theme.label[:20],
                observation=f"「{theme.label[:40]}」只有一处证据支撑，但看起来是件难事",
                resolves="这是一次性的，还是你反复做过只是没留下别的记录",
                card_ids=[c.id for c in theme.cards[:3]],
                angle=KIND_ANGLE["single_source"], weight=KIND_WEIGHT["single_source"],
                draft=f"「{theme.label[:30]}」我只看到一次。这类事你做过几回？"))
    return out


def rank(candidates: list[Candidate], limit: int = 7, per_subject: int = 1,
         per_kind: int = 1, per_angle: int = 2) -> list[Candidate]:
    """Best questions first, with diversity quotas.

    Seven is not arbitrary: an interview people actually finish is short, and
    five questions about the same skill is one question asked five ways.

    ``per_kind`` matters more than it looks. Two `capability_without_interest`
    questions about two different skills are different questions on paper and
    the *same* question to the person answering -- same shape, same framing,
    asked twice. One well-aimed version of each kind beats three variations.
    """
    ordered = sorted(candidates, key=lambda c: -c.weight)
    chosen: list[Candidate] = []
    subjects: dict[str, int] = {}
    kinds: dict[str, int] = {}
    angles: dict[str, int] = {}
    for cand in ordered:
        if subjects.get(cand.subject, 0) >= per_subject:
            continue
        if kinds.get(cand.kind, 0) >= per_kind:
            continue
        if angles.get(cand.angle, 0) >= per_angle:
            continue
        chosen.append(cand)
        subjects[cand.subject] = subjects.get(cand.subject, 0) + 1
        kinds[cand.kind] = kinds.get(cand.kind, 0) + 1
        angles[cand.angle] = angles.get(cand.angle, 0) + 1
        if len(chosen) >= limit:
            break
    return chosen


def coverage(chosen: list[Candidate]) -> dict:
    kinds: dict[str, int] = {}
    angles: dict[str, int] = {}
    for c in chosen:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
        angles[c.angle] = angles.get(c.angle, 0) + 1
    return {"kinds": kinds, "angles": angles,
            "missing_angles": sorted(set(CCI_ANGLES) - set(angles))}


def answers_to_cards(answers: list[dict]) -> list[Card]:
    """Your answers become cards like anything else -- with one difference.

    The source is the interview, so the "quote" is your own sentence and it
    verifies against the answer file rather than against an artifact. That is
    what lets a single-source anecdote become a pattern: your testimony is a
    genuinely independent source, as long as it is recorded as one and not
    quietly merged into the artifact evidence.
    """
    from .cards import Evidence

    out = []
    for a in answers:
        text = (a.get("answer") or "").strip()
        if not text:
            continue
        out.append(Card(
            claim=(a.get("claim") or text)[:300],
            kind=a.get("kind", "self_concept"),
            evidence=[Evidence(source="interview", locator=a.get("question_id", "q"),
                               quote=text[:200])],
            skills=a.get("skills", []), role_signal=a.get("role_signal", "unknown"),
            difficulty=int(a.get("difficulty", 3)),
            confidence=float(a.get("confidence", 0.6)),
            time_range=a.get("time_range", ""),
        ))
    return out


def dump(candidates: list[Candidate], path: Path, extra: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"questions": [c.to_dict() for c in candidates],
               "coverage": coverage(candidates), "angles": CCI_ANGLES}
    payload.update(extra or {})
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
