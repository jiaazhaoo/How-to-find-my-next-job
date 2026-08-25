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
| 填 sensitive_terms | **ask them** — see below |
| 亲眼过一遍脱敏结果 | run the `career-evidence-redaction` skill, then `touch workspace/.reviewed` |
| 深读 | run `career-evidence-read` on each pack in `workspace/04_packs/` |
| 回答问题 | run the `career-evidence-interview` skill |
| 写画像 | run the `career-evidence-profile` skill |

After each one, run `career-evidence run` again. Never guess the next step
from memory — the state machine knows and you do not.

## The two gates that are actually theirs

**`sensitive_terms`.** Do not skip past this and do not invent entries. Ask
directly: which client or company names, project code names, internal system
names, and colleagues who never appear in git history are in this material?
Nothing else in the pipeline can discover these — credentials have rules
behind them, a client's name does not. If they genuinely have none, have them
say so explicitly rather than leaving the sample values in place.

**The interview.** Their answers are the only new information the whole
system produces. Conduct it properly via the `career-evidence-interview` skill; do not
summarise questions at them or answer on their behalf.

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
