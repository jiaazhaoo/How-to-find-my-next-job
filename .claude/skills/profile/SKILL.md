---
name: profile
description: Write the career profile from workspace/08_skeleton.json, then validate it with `career profile --check`. Use only after cards, themes and ideally the interview exist. Every claim must cite the cards that back it.
---

# Write the profile

This is the most dangerous document in the pipeline. It is the part someone
will paste into a CV, and the part a language model will cheerfully improve
into flattery. Assume that pressure is on you and work against it.

## Input

`workspace/08_skeleton.json` decides what may be claimed. You do not get to
add themes to it. It contains:

- `qualified` — themes with two or more independent sources. **Only these may
  appear in the evidence section.** Each carries `card_ids` (cite these),
  `role_signals`, `difficulty_max`, `sources`, and `stance`.
- `single_source` — real, but seen once
- `self_reported` — what the person said about themselves, with `corroborated`
- `gaps` — open questions the record cannot answer
- `stats` — including `quote_check_ran`. If it is false, quotes were never
  verified; say so in the document rather than writing as if they were.

## Required structure

Four sections, each opening with its marker on its own line. The prose heading
can be in any language; the marker is what the validator finds.

```markdown
## 有证据的
{{evidence}}
...

## 你自己说的
{{self-reported}}
...

## 只出现过一次
{{single-source}}
...

## 记录里读不出来的
{{gaps}}
...
```

These four are different epistemic states and **must not borrow each other's
authority**. Collapsing them is exactly how "I once wrote a migration script"
becomes "seasoned migration architect".

## Rules the validator enforces

- Every claim line in `{{evidence}}` carries card ids: `... [de0fca3c11, d04e180003]`
- Cited ids must exist, must belong to a `qualified` theme, and must not be
  self-report cards
- `{{gaps}}` must not be empty

Run it and fix every error:

```bash
python3 -m career profile --check workspace/09_profile.md
```

## Rules the validator cannot enforce

**Write what the evidence supports, at the size it supports it.** Two sources
is the threshold for saying something at all, not licence to call it a
specialism. `sources=2, max_difficulty=3` is "有做过，做成了"; it is not
"专家".

**The `stance` field is the most valuable thing in the document.** Where
someone is demonstrably strong at something and has said they do not want to
keep doing it, say both, in the same breath, plainly:

> 迁移与数据一致性是记录里最扎实的一块（4 个来源，主导）[de0fca3c11,
> d04e180003]。**但本人明确表示不想再把它当主线**，只愿意在需要时兜底 [276d341c2c]。

Note the second citation. A stance sentence is still a claim and still needs
its source — the interview card that recorded it. An interview `interest` card
is a legal citation in this section (it is evidence, from an independent
source); a `self_concept` card is not.

That sentence is worth more than the rest of the profile, and no
artifact-only pipeline can produce it. Never smooth it away into "经验丰富且
兴趣广泛".

**No trait language.** Not "细致"、"内向"、"抗压能力强"、any Big Five or MBTI
vocabulary. Inferring personality from text is unreliable enough that this
pipeline refuses to do it. Describe behaviour with citations instead.

**`role_signals` are load-bearing.** A theme containing both `owner` and
`observer` cards is not uniformly owned; write "主导了 X，在 Y 中是评审角色",
not "负责了 X 和 Y".

**Say what is missing.** `{{gaps}}` is not a disclaimer section — it is the
part that makes the document useful for deciding what to do next. Include the
open questions, the eras with no coverage, and what kind of evidence would
settle each one. If the interview has not run, say that: the largest gap in
any artifact-only profile is what the person actually wants.

**Length**: one page. If it does not fit, the evidence does not support what
you are writing.

## Finish

Report: how many themes were usable, how many claims you made, the check
result, and the single most decision-relevant sentence in the document. If the
check returns errors, fix them and re-run before reporting anything.
