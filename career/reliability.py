"""Measure whether the card layer says the same thing twice.

The gap this closes
-------------------
Every claim this pipeline makes is traceable, which is not the same as
reproducible. `deep-read` is a sampled model pass: run it on the same pack
twice and you get two card sets, and until now nobody -- including me -- knew
how different they are. Everything downstream inherits that variance, so
without a number here, the precision of the later stages is decorative.

What it measures, in increasing order of what actually matters
--------------------------------------------------------------
1. **Claims** -- do the runs make the same assertions? Matched by similarity,
   not by id: ids hash the wording, so a rephrased claim would score zero
   agreement while meaning the same thing.
2. **Evidence** -- do the runs cite the same passages? Stronger than claim
   wording: two runs quoting the same lines found the same thing even if they
   described it differently.
3. **Ratings** -- for claims both runs found, do they agree on difficulty and
   confidence? This is where an unanchored 1-5 scale shows up.
4. **Themes** -- do the same findings reach `pattern`? Card-level noise is
   tolerable if the conclusions are stable; this is the first metric that
   measures something a user actually sees.
5. **Questions** -- does the interview come out the same? The true end-to-end
   measure, because questions are the product.

An honest caveat, stated in the output too: these are **raw agreement rates,
not chance-corrected**. A chance-corrected statistic (kappa and relatives)
needs a defined universe of possible cards, which does not exist when the
universe is "any sentence a model might write". Raw agreement always looks
better than kappa would. Read these as descriptive, not as psychometrics.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean, pstdev

from .cards import Card, _similar, cluster

# Proposed reading bands. These are conventions for this tool, not established
# standards -- there is no accepted threshold for "LLM extraction reliability".
BANDS = ((0.80, "stable"), (0.60, "usable"), (0.40, "shaky"), (0.0, "unreliable"))


def band(value: float) -> str:
    for floor, label in BANDS:
        if value >= floor:
            return label
    return "unreliable"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _k_of_n(counts: list[int], n: int) -> dict:
    hist: dict[str, int] = {}
    for c in counts:
        hist[f"{c}/{n}"] = hist.get(f"{c}/{n}", 0) + 1
    total = len(counts) or 1
    return {
        "histogram": dict(sorted(hist.items(), reverse=True)),
        "in_all_runs": sum(1 for c in counts if c == n),
        "in_one_run_only": sum(1 for c in counts if c == 1),
        "stable_fraction": round(sum(1 for c in counts if c == n) / total, 3),
        "majority_fraction": round(sum(1 for c in counts if c > n / 2) / total, 3),
    }


def _pairwise_jaccard(sets: list[set]) -> dict:
    scores = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            scores.append(len(sets[i] & sets[j]) / len(union) if union else 1.0)
    if not scores:
        return {"mean": 1.0, "min": 1.0, "pairs": 0}
    return {"mean": round(mean(scores), 3), "min": round(min(scores), 3),
            "pairs": len(scores)}


def _soft_agreement(per_run: list[list[tuple[str, set]]], threshold: float = 0.5) -> dict:
    """Group items across runs by set overlap rather than exact identity.

    Same treatment claims get, and for the same reason: near-identical
    findings that differ by one label are one finding, not two.
    """
    pooled: list[dict] = []
    for run_index, items in enumerate(per_run):
        for key, members in items:
            for group in pooled:
                union = group["members"] | members
                if union and len(group["members"] & members) / len(union) >= threshold:
                    group["members"] |= members
                    group["runs"].add(run_index)
                    break
            else:
                pooled.append({"key": key, "members": set(members), "runs": {run_index}})

    n = len(per_run)
    counts = [len(g["runs"]) for g in pooled]
    # Pairwise agreement, counted through the groups so soft matches count.
    scores = []
    for i in range(n):
        for j in range(i + 1, n):
            both = sum(1 for g in pooled if {i, j} <= g["runs"])
            either = sum(1 for g in pooled if g["runs"] & {i, j})
            scores.append(both / either if either else 1.0)
    out = {"mean": round(mean(scores), 3) if scores else 1.0,
           "min": round(min(scores), 3) if scores else 1.0,
           "pairs": len(scores), "distinct": len(pooled)}
    out.update(_k_of_n(counts, n))
    return out


@dataclass
class ReliabilityReport:
    runs: int = 0
    cards_per_run: list[int] = field(default_factory=list)
    claims: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    ratings: dict = field(default_factory=dict)
    themes: dict = field(default_factory=dict)
    questions: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _consensus_groups(runs: list[list[Card]], threshold: float = 0.3) -> list[dict]:
    """Pool every run's cards, group by similarity, count distinct runs.

    Reuses the same clustering the pipeline uses downstream, so the agreement
    measured here is agreement as the pipeline itself would see it.
    """
    tagged: dict[str, int] = {}
    pooled: list[Card] = []
    for run_index, run in enumerate(runs):
        for card in run:
            key = f"{run_index}:{card.id}:{len(pooled)}"
            tagged[key] = run_index
            copy = Card(claim=card.claim, kind=card.kind, evidence=list(card.evidence),
                        skills=list(card.skills), role_signal=card.role_signal,
                        difficulty=card.difficulty, confidence=card.confidence,
                        time_range=card.time_range, id=key)
            pooled.append(copy)

    groups = []
    for theme in cluster(pooled, threshold=threshold):
        members = theme.cards
        run_ids = {tagged[c.id] for c in members}
        groups.append({"runs": sorted(run_ids), "n_runs": len(run_ids),
                       "label": theme.label, "members": members})
    return groups


def analyse(runs: list[list[Card]], now_year: int | None = None) -> ReliabilityReport:
    from . import interview as interview_mod

    n = len(runs)
    report = ReliabilityReport(runs=n, cards_per_run=[len(r) for r in runs])
    if n < 2:
        report.notes.append("需要至少 2 次独立运行才能测信度")
        return report

    counts = report.cards_per_run
    if max(counts) and (max(counts) - min(counts)) / max(counts) > 0.4:
        report.notes.append(
            f"各次产出的卡片数差异很大（{min(counts)}–{max(counts)}）——"
            f"提取的详略程度本身就不稳定，比措辞不稳定更值得担心")

    # 1. claims
    groups = _consensus_groups(runs)
    report.claims = _k_of_n([g["n_runs"] for g in groups], n)
    report.claims["distinct_claims"] = len(groups)
    report.claims["band"] = band(report.claims["stable_fraction"])

    # 2. evidence -- the same passages, however described
    quote_sets = [{(e.source, _norm(e.quote)[:120]) for c in run for e in c.evidence}
                  for run in runs]
    report.evidence = _pairwise_jaccard(quote_sets)
    seen: dict[tuple, int] = {}
    for s in quote_sets:
        for item in s:
            seen[item] = seen.get(item, 0) + 1
    report.evidence.update(_k_of_n(list(seen.values()), n))
    report.evidence["band"] = band(report.evidence["mean"])

    # 3. skills
    skill_sets = [{s.lower() for c in run for s in c.skills} for run in runs]
    report.skills = _pairwise_jaccard(skill_sets)
    report.skills["band"] = band(report.skills["mean"])
    report.skills["labels_per_run"] = [len(s) for s in skill_sets]

    # 4. ratings on claims the runs agree exist
    diffs, confs, big_gaps = [], [], 0
    for g in groups:
        if g["n_runs"] < 2:
            continue
        ds = [c.difficulty for c in g["members"]]
        cs = [c.confidence for c in g["members"]]
        if len(ds) > 1:
            spread = max(ds) - min(ds)
            diffs.append(spread)
            big_gaps += spread >= 2
        if len(cs) > 1:
            confs.append(round(max(cs) - min(cs), 3))
    report.ratings = {
        "matched_claims": len(diffs),
        "difficulty_spread_mean": round(mean(diffs), 3) if diffs else 0.0,
        "difficulty_spread_sd": round(pstdev(diffs), 3) if len(diffs) > 1 else 0.0,
        "difficulty_disagreements_ge_2": big_gaps,
        "confidence_spread_mean": round(mean(confs), 3) if confs else 0.0,
    }
    if diffs and mean(diffs) >= 1.0:
        report.notes.append(
            "difficulty 在同一条主张上平均差 ≥1 级——这个 1-5 分没有锚点，"
            "而它在驱动 diff>=4 之类的触发条件")

    # 5. themes -- the first metric a user would notice.
    # Identified by skill set, not by label. A theme's label is one member
    # card's wording, so matching on it counts a rephrased title as a
    # different finding -- the same mistake as matching cards by id.
    def theme_key(theme) -> str:
        skills = tuple(sorted(s.lower() for s in theme.skills))
        return "|".join(skills) if skills else _norm(theme.label)[:60]

    # Exact set matching is still too strict here: two runs can find the same
    # theme with slightly different member cards, so its skill union differs
    # by one label and the theme counts as absent. Themes are matched softly,
    # the same way claims are.
    per_run = [[(theme_key(t), {s.lower() for s in t.skills})
                for t in cluster(run) if t.status == "pattern"] for run in runs]
    report.themes = _soft_agreement(per_run, threshold=0.5)
    report.themes["patterns_per_run"] = [len(p) for p in per_run]
    report.themes["band"] = band(report.themes["mean"])

    # 6. questions -- the actual product
    kind_sets = []
    for run in runs:
        cands = interview_mod.generate(run, cluster(run), now_year=now_year)
        kind_sets.append({f"{c.kind}:{c.subject}" for c in interview_mod.rank(cands)})
    report.questions = _pairwise_jaccard(kind_sets)
    report.questions["questions_per_run"] = [len(s) for s in kind_sets]
    report.questions["band"] = band(report.questions["mean"])

    return report


def load_runs(paths: list[Path]) -> list[list[Card]]:
    from .cards import load
    return [load(p, strict=False) for p in paths]


def dump(report: ReliabilityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), "utf-8")
