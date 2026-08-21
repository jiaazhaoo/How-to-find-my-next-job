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
| `kind` | `capability` · `decision` · `impact` · `collaboration` · `interest` · `constraint` · `contradiction` · `self_concept` |
| `evidence` | ≥1 item. `source` = the `id` from the SOURCE header. `quote` = **verbatim** from the pack, ≤200 chars. `locator` = section/heading/line hint. |
| `skills` | Short lowercase labels. Reuse labels across cards — clustering depends on it. |
| `role_signal` | `owner` · `contributor` · `reviewer` · `observer` · `unknown`. Derive from `my_line_share`/`commits`, not from tone. |
| `difficulty` | 1 routine … 5 genuinely hard. A CRUD endpoint is 1 even if the file is long. |
| `confidence` | 0–1. Start at 0.6; raise only with independent corroboration, lower on any doubt from pass 3. |
| `counter_evidence` | What would make this claim wrong, if anything in the pack suggests it. |
| `open_question` | The one thing you would have to ask the person to settle this. This field feeds the interview layer — a good question here is worth more than three extra cards. |

## Reading a chat session (`class=chat_session`)

Session transcripts are the richest source in the corpus and the easiest to
misread. A repository records what shipped; a transcript records what was
*tried* — abandoned approaches, the questions someone had to ask, the places
they argued back. None of that survives into a commit, and all of it is
evidence.

Five rules specific to transcripts:

1. **Only `## you` blocks are evidence.** Assistant turns are stubs, present
   so the human turns make sense. Quoting the assistant and attributing it to
   the person is the single most likely mistake here, and it produces a card
   that is wrong in the most flattering possible direction.
2. **The self-flattery trap.** People describe themselves to assistants
   constantly — "我一般习惯先…", "I'm not great at…". Read as capability
   evidence, these turn the profile into the person's own self-image handed
   back with citations: rigorous-looking, and a mirror. Any statement someone
   makes *about themselves* is `kind: "self_concept"`, never `capability`.
   Self-concept cards can quote trait language (that is what was said); they
   can never be promoted to a pattern, however often they recur. They are
   valuable as interview fuel: the **gap between the self-description and what
   the artifacts show** is the best question generator in the system.
3. **Pushback is the competence signal.** Where someone corrects, overrides or
   argues with the model, they are demonstrating domain knowledge — you cannot
   argue about a field you do not understand. Long stretches of "yes, do that"
   demonstrate nothing. The header gives you a `pushback=` count; the turns
   themselves tell you what it was about.
4. **Abandoned work counts.** "We tried X, it didn't hold up because Y" is a
   `decision` card even though nothing shipped. This is the one place such
   evidence exists.
5. **`<!-- block removed by topic policy: … -->`** means a turn was excised for
   containing health, money, legal or personal material. Note the gap, never
   speculate about its content, and never treat surrounding turns as a
   continuous conversation.

One more: a session where the person typed very little and the assistant did
everything is weak evidence about the person, whatever got built. Check
`human_turns` and `human_weight` in the header before writing an `owner` card.

## Reading work notes (`class=work_note`) and public posts (`class=public_post`)

Both come from places people use for everything, so both arrive with a
`work_relevance=` score in the header. That score got them past the import
filter; it says nothing about whether any individual claim in them is true.

**Work notes** (Notion and similar) are written fast, never edited, and are
often the only written record a project ever got. Two things to watch:
*plans are not outcomes* — a note saying "we will move to event sourcing in
Q3" is evidence of intent, not of a migration, and needs a `constraint` or
`decision` card at most; and a note may be minutes of a meeting where someone
else decided, so check whether the writer is reporting or deciding.

**Public posts** are the trickiest source in the corpus, because they read
like evidence and are not. A timeline post is *someone describing their own
work to an audience*, with nothing backing it. So:

- A post claiming an outcome ("cut the job from 6 hours to 40 minutes") is a
  **self-report**, not an `impact` card. Write it as `self_concept`, or as a
  low-confidence card whose `open_question` asks for the artifact — and
  promote it only if a repository, note or session independently shows it.
- What posts *are* good evidence of: sustained interest (what someone returns
  to unprompted over years), how they explain things to non-experts, and how
  they position themselves. `interest` and `collaboration` cards are fair;
  `capability` cards from a post alone are not.
- `posts=N` in the header means N posts were reassembled into one thread. A
  long thread is someone working an argument through; a single post is a
  remark. Do not treat engagement counts as quality — they are not in the
  header on purpose, because likes measure the audience, not the author.

## Hard rules

1. **Never invent a quote.** If you cannot quote it, you cannot claim it.
2. **No personality inference.** "Careful", "introverted", "perfectionist",
   "MBTI/Big Five anything" — banned at this layer, and the validator rejects
   them. (The one exception is a `self_concept` card, where you are quoting
   the person's own words about themselves rather than concluding anything.) LLM personality inference from text correlates below r≈0.3 with
   actual instruments; behaviour with receipts is what this stage produces.
   Traits, if they ever appear, come from a real instrument the person fills
   in, not from you reading their code.
3. **`impact` cards need a number** in the claim. No number → it is a
   `capability` card.
4. **Do not card the obvious.** "Used git", "wrote tests", "structured the
   repo into modules" is noise.
5. **Uncertainty is data.** `confidence: 0.4` plus a sharp `open_question`
   beats a confident guess.
6. **Claims about your own work, made by you, are never `impact`.** Whether
   the sentence appears in a chat log, a note or a post, an outcome with no
   artifact behind it is `self_concept` until something independent shows it.
7. **8–20 cards per pack** is the healthy range. Forty cards means you carded
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
