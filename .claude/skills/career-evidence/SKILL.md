---
name: career-evidence
description: "Drive the whole career-profiling pipeline from wherever it currently stands. Use when the user wants to start, continue, or check on building their evidence-based career profile, or asks what to do next with this project. Handles the sequencing so they never have to remember which of seventeen commands comes next."
---

# Drive the pipeline

The user should never have to remember the order of anything. Run
`career-evidence run`, do whatever it stops on, run it again. Repeat until
it stops on something only they can supply.

## First, work out how to call it

The CLI may be on PATH, or it may be sitting in the plugin directory with
nothing installed. Resolve it once and reuse that form for every command:

```bash
career-evidence --help >/dev/null 2>&1 \
  && CE="career-evidence" \
  || CE="python3 \"${CLAUDE_PLUGIN_ROOT:-.}/scripts/career-evidence\""
```

If `CLAUDE_PLUGIN_ROOT` is unset and you are inside a checkout, the launcher is
at `./scripts/career-evidence`. Everything below writes `career-evidence`; use
whichever form actually resolved.

## The loop

```bash
career-evidence run
```

It performs every automatic step and stops at the first gate, printing a
checklist and one instruction. Then:

| it stops on | you do |
|---|---|
| 填 authors / 指定材料 | help them fix the config, then loop |
| 自动识别并隐藏敏感词 | nothing — it runs, then tell them what was protected |
| 报告自动脱敏覆盖不到的部分 | nothing — relay the residual categories it prints |
| 深读 | run `career-evidence-read` on each pack in `workspace/04_packs/` |
| 回答问题 | run the `career-evidence-interview` skill |
| 写画像 | run the `career-evidence-profile` skill |

After each one, run `career-evidence run` again. Never guess the next step
from memory — the state machine knows and you do not.

## Do not ask about sensitive terms

This used to be a question and no longer is. `career-evidence terms --auto`
scans the material, finds the patterns a machine can find — company and
project names by the word that follows them, internal hostnames, other
committers in git — and protects **all** of them. The costs are not
symmetric: over-redacting adds a few aliases to text that stays readable,
because aliases are stable, while under-redacting leaks a client's name. A
question whose safe answer is always "all of them" is not a question.

So: let it run, then **report** what it protected and what it could not reach.
Offer, in one line, that anything wrongly aliased can be deleted from
`sensitive_terms`. Do not present a checklist and wait. Expect a false
positive or two in the list — that is the intended direction of error.

## The one thing that is still theirs

**The interview.** Their answers are the only new information the whole
system produces, and the one thing artifacts structurally cannot supply: a
repository can prove someone is good at something, never whether they still
want to do it. Conduct it properly via `career-evidence-interview`; do not
summarise the questions at them or answer on their behalf.

## When a step fails

Show them the actual error and stop. Do not tune thresholds, widen quotas or
edit weights to make something pass — every number in this pipeline is still
hand-set on small samples, so a bad result is more likely a bug than a
misconfiguration. Two runs on real material have produced four bugs so far,
one of which silently manufactured false corroboration. Treat surprising
output as a finding to report, not a setting to adjust.

## Reporting

Keep it short. After each round, say what just happened, what the numbers
were (`career-evidence run` prints them), and the one thing you need from them. Do not
re-explain the architecture; they can read `docs/` if they want it.

## Language

Reply in whatever language the user is writing in. These instructions are in
English because the code is; that is not a reason to answer a Chinese question
in English.
