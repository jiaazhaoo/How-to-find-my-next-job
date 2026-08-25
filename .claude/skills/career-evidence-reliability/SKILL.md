---
name: career-evidence-reliability
description: "Measure whether repeated deep-read runs on the same pack produce the same cards. Use when asked how reliable or reproducible the extraction is, before trusting a profile, or after changing the deep-read instructions. Produces a number for the pipeline's largest unmeasured unknown."
---

# Reliability check

Every claim this pipeline makes is traceable. That is not the same as
reproducible. `career-evidence-read` is a sampled model pass: run it twice on the same
pack and you get two card sets, and everything downstream inherits whatever
that variance is. This measures it.

## The one rule that decides whether the number means anything

> **The runs must not see each other.**

If run 2 can see run 1's cards — same context, same conversation, or even a
summary — you are measuring memory, not reliability, and the number will be
inflated in the flattering direction. A reliability figure produced that way
is worse than no figure, because it will be believed.

In practice: `/clear` between runs, or run each in a separate session. Do not
read the previous run's file "just to check the format".

## Procedure

1. **Pick one pack** and keep it fixed: `workspace/04_packs/pack-01.md`.
   Three runs is the useful minimum; five is better if the pack is short.

2. **Run `career-evidence-read` N times**, writing to separate files and clearing
   context between each:

   ```
   /clear
   /career-evidence-read workspace/04_packs/pack-01.md   → write cards to workspace/rel/run1.jsonl
   /clear
   /career-evidence-read workspace/04_packs/pack-01.md   → write cards to workspace/rel/run2.jsonl
   /clear
   /career-evidence-read workspace/04_packs/pack-01.md   → write cards to workspace/rel/run3.jsonl
   ```

   Follow the deep-read contract exactly each time. Do not try to be
   consistent with a previous run — consistency you impose on purpose is the
   thing being measured, and imposing it destroys the measurement.

3. **Analyse:**

   ```bash
   career-evidence reliability workspace/rel/run*.jsonl --year 2026
   ```

## Reading the output

Five agreement rates, ordered from raw material to finished product:

| metric | what disagreement means |
|---|---|
| 引用段落 | the runs looked at different parts of the pack — the most basic failure |
| 主张 | same passages, different conclusions drawn from them |
| 技能标签 | unstable labels; clustering downstream depends on these |
| 主题(pattern) | the findings themselves move between runs |
| 最终问题 | the interview would have been different — this is the product |

**Read it bottom-up.** Card-level noise is tolerable if the questions are
stable: it means aggregation is absorbing the variance, which is what the
two-independent-sources rule is for. The alarming pattern is the reverse —
stable cards but unstable questions, which would mean the ranking is
amplifying small differences.

Also watch `difficulty` spread. It is a 1–5 scale with no anchors that drives
threshold logic (`diff >= 4`), so disagreement of a full level or more there
is a design problem, not noise.

## Reporting honestly

Three things must appear in whatever you write up:

1. **These are raw agreement rates, not chance-corrected.** A kappa-type
   statistic needs a defined universe of possible cards; here the universe is
   "any sentence a model might write". True agreement is lower than the
   printed number, never higher.
2. **The bands (stable/usable/shaky/unreliable) are conventions for this
   tool**, not a field standard. There is no accepted threshold for LLM
   extraction reliability.
3. **N is small.** Three runs on one pack is a spot check, not an estimate
   with an interval. Say so.

If the numbers are poor, do not quietly rerun until they improve. That is
p-hacking with extra steps. Report the first honest measurement, then change
something specific — the deep-read instructions, the pack, the card schema —
and measure again as a separate, labelled result.

## Language

Reply in whatever language the user is writing in. These instructions are in
English because the code is; that is not a reason to answer a Chinese question
in English.
