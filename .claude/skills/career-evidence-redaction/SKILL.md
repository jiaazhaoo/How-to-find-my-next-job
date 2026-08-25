---
name: career-evidence-redaction
description: "Audit what the redaction gate produced before any of it reaches a model or leaves the machine. Use after `career-evidence prep`, when redaction-report.json needs reviewing, or when the user asks whether their material is safe to share with an AI."
---

# Redaction review

The gate in `career/redact.py` fails closed: any document where a credential
survives the pass is dropped, not emitted. That covers the failure mode where
a regex misses. It does **not** cover the failure mode that actually matters
in practice — *sensitive strings that no generic detector knows are
sensitive*. That is what this review is for, and it needs a human.

## Run the audit

```bash
career-evidence doctor        # which scanners are actually installed
career-evidence prep -v       # writes workspace/redaction-report.json
```

`redaction-report.json` contains no plaintext by design — labels, rule names,
fingerprints and counts only, so the report itself is shareable.

## What to check, in order

**1. Was anything dropped?** `failures[]` with `reason` starting `redaction:`
means the gate refused a file. That is the system working, but the file is now
invisible to the whole pipeline. Read it manually, fix the rule or add an
allowlist entry, and re-run — do not shrug it off, dropped files are usually
the interesting ones (config-heavy services, incident writeups).

**2. Do the totals look plausible?** Compare `totals` against what you know of
the corpus. Zero `EMAIL` across forty repositories means the scan did not
reach the material. A thousand `IP_ADDR` usually means test fixtures — noisy
but harmless.

**3. The real work: read a pack with your own eyes.** Open
`workspace/04_packs/pack-01.md` and hunt for what the regexes structurally
cannot catch:

- **Colleague and client names in prose.** The pipeline pseudonymises every
  identity in git history and every term in `sensitive_terms`. A reviewer
  mentioned only in a document body — `评审：Wang Fang` — is invisible to it
  unless Presidio is installed or you add the name to `sensitive_terms`.
- **Project and product code names** that were never in git.
- **Internal URLs, ticket ids, Slack channels, meeting-room names.**
- **Numbers that identify a client** even without the name: a revenue figure,
  a headcount, an incident date.
- **Unreleased or embargoed work** — the risk here is contractual, not
  technical, and no scanner will flag it.

Add every one you find to `sensitive_terms` in the config, then re-run `prep`.
Aliases are stable, so re-running is cheap and idempotent.

**4. For chat material, check the topic policy.** `topic_excisions` and
`excised_blocks` in the summary count turns removed for health, money, legal
or personal content, and `failures[]` lists sessions dropped because most of
their turns hit. Two failure directions, both worth catching by eye: a
legaltech engineer's work turns being excised as "legal" (add nothing — narrow
`topic_policy.extra_topics` or drop the category), and a personal turn that
slipped through because it used words the patterns do not know. The policy is
turn-level on purpose: one doctor's appointment should not cost you a good
session, but it must still be visibly removed rather than quietly kept.

**5. Check the vault is where it should be.** `workspace/vault/aliases.json`
maps aliases back to real names and must be mode 0600 and gitignored. It is
the one file in the workspace that is *more* sensitive than the originals,
because it is the whole cast list in one place. Never commit it, never upload
it, never paste it. `career-evidence restore` uses it locally to read a
finished report with real names.

## Reporting back

State plainly:
- how many documents were dropped by the gate and why,
- which categories were redacted and in what volume,
- **what you found by eye that the tooling missed**, and whether it is now in
  `sensitive_terms`,
- whether gitleaks and Presidio are installed, and that the built-in rules are
  running unassisted if they are not.

Then say explicitly whether the packs are cleared to send to a model. If you
are unsure about a single string, treat it as unsafe: adding a term costs one
line of config, and a leaked client name cannot be recalled.
