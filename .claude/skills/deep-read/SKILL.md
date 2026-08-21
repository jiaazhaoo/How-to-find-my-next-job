---
name: deep-read
description: Read a redacted read pack and emit verifiable evidence cards. Use when processing workspace/04_packs/pack-NN.md produced by `python -m career prep`, or whenever asked to extract career evidence from source material. Not for writing a profile — this stage only produces checkable claims with quotes.
---

# Deep read: turn a pack into evidence cards

You are doing the **map** stage of the reading funnel. Your output is not prose.
It is a set of cards, each of which is a single claim that someone else could
falsify by opening the source.

## Why cards, not a summary

A summary of forty repositories is a paragraph nobody can check, and the parts
that flatter the author are exactly the parts that are usually invented. A card
carries its own receipt: claim, source, locator, verbatim quote. `python -m
career verify` then mechanically checks every quote against the exact excerpt
you were shown. **A card whose quote is not in the pack is a failed card**, and
it will be caught, so do not write one.

## Input

`workspace/04_packs/pack-NN.md`. Each `### SOURCE <id>` block gives you:

- the redacted path,
- an HTML comment with metadata: `class=`, `lang=`, `my_line_share=`,
  `commits=`, `last_touched=`, `excerpt=`,
- the excerpt itself.

Read the metadata as *facts*, not hints. `my_line_share=1.0 commits=12` means
the person owns this file; `my_line_share=0.05` means they touched it once and
you must not write "built" or "designed" about it.

Aliases are stable across the entire corpus: `[[ORG_01]]` in pack 1 and
`[[ORG_01]]` in pack 7 are the same company. `[[ME_xx]]` is the subject.
`[[X_REDACTED:abc123]]` was a credential — never speculate about its value.
`... [N lines elided] ...` means the file was structurally reduced; you are
seeing the load-bearing lines, not the whole file, so do not claim anything
about what is missing.

## Three passes over each pack

**Pass 1 — orient (no cards yet).** Skim every source block. Note out loud, in
two or three sentences: what system is this, what era, what was the person's
apparent role. This pass exists to stop you writing forty disconnected cards
about one project.

**Pass 2 — extract.** Now write cards. One checkable claim each. Prefer the
places where someone *explained a choice or reported an outcome* — those are
already the reason the file was selected. For each card ask: could a reader
open the source and agree this is what it says?

**Pass 3 — challenge.** Re-read your own cards adversarially before writing
them out:
- What is the most boring explanation for this evidence? (Often: the person
  followed a template, or inherited the design.) If you cannot rule it out,
  lower `confidence` and say why in `counter_evidence`.
- Does another card in this pack contradict it? Then set
  `kind: "contradiction"` on the weaker one, or fill `counter_evidence`.
- Is the claim actually about the person, or about the company? Only the
  former is evidence.
- Would this claim be true of every engineer at that level? If yes, drop it —
  it carries no information.

## Card format

Append one JSON object per line to `workspace/05_cards.jsonl`:

```json
{"claim":"设计并落地了支付网关迁移的双写+对账灰度方案，并给出三方案对比","kind":"decision","evidence":[{"source":"3e81e5b24a54778f","locator":"ADR-003 决策段","quote":"我们决定用双写 + 对账做灰度，因为一次性切换无法回滚"}],"skills":["migration","data-consistency"],"role_signal":"owner","difficulty":4,"time_range":"2023Q3","confidence":0.85,"counter_evidence":"","open_question":"三方案对比是你独立做的，还是团队已有的结论？"}
```

Fields:

| field | rule |
|---|---|
| `claim` | One sentence, behavioural, checkable. Same language as the source. |
| `kind` | `capability` · `decision` · `impact` · `collaboration` · `interest` · `constraint` · `contradiction` |
| `evidence` | ≥1 item. `source` = the `id` from the SOURCE header. `quote` = **verbatim** from the pack, ≤200 chars. `locator` = section/heading/line hint. |
| `skills` | Short lowercase labels. Reuse labels across cards — clustering depends on it. |
| `role_signal` | `owner` · `contributor` · `reviewer` · `observer` · `unknown`. Derive from `my_line_share`/`commits`, not from tone. |
| `difficulty` | 1 routine … 5 genuinely hard. A CRUD endpoint is 1 even if the file is long. |
| `confidence` | 0–1. Start at 0.6; raise only with independent corroboration, lower on any doubt from pass 3. |
| `counter_evidence` | What would make this claim wrong, if anything in the pack suggests it. |
| `open_question` | The one thing you would have to ask the person to settle this. This field feeds the interview layer — a good question here is worth more than three extra cards. |

## Hard rules

1. **Never invent a quote.** If you cannot quote it, you cannot claim it.
2. **No personality inference.** "Careful", "introverted", "perfectionist",
   "MBTI/Big Five anything" — banned at this layer, and the validator rejects
   them. LLM personality inference from text correlates below r≈0.3 with
   actual instruments; behaviour with receipts is what this stage produces.
   Traits, if they ever appear, come from a real instrument the person fills
   in, not from you reading their code.
3. **`impact` cards need a number** in the claim. No number → it is a
   `capability` card.
4. **Do not card the obvious.** "Used git", "wrote tests", "structured the
   repo into modules" is noise.
5. **Uncertainty is data.** `confidence: 0.4` plus a sharp `open_question`
   beats a confident guess.
6. **8–20 cards per pack** is the healthy range. Forty cards means you carded
   boilerplate; three means you skimmed.

## Finish

After writing cards for a pack:

```bash
python -m career verify        # every quote must exist in the corpus
python -m career themes        # cluster; ≥2 independent sources = pattern
```

Fix or delete any card that fails verification. Then report: cards written,
cards verified, and the two or three open questions you would most want
answered. Do **not** write a profile — that is a later stage, and it must be
built only from cards marked `pattern`.
