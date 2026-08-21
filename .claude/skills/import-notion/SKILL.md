---
name: import-notion
description: Import Notion pages into the career pipeline's staging directory using this session's Notion MCP connector, so no manual export is needed. Use when the user asks to import Notion, or when connectors['notion-export'].path is unset but Notion MCP tools are available.
---

# Import Notion over MCP

The `notion-export` connector needs a manually unzipped export. This skill
skips that: Notion's hosted MCP server is already authenticated in this
session, so you can fetch pages directly and stage them.

## Read this before running it

The material passes through **your context** on its way to disk, and your
context has already been sent to a model provider. That is a real difference
from the export path, where content goes disk → redact → model and nothing
un-redacted ever reaches a model at all.

So state the trade-off to the user and let them choose. Do not decide for them:

| | export path | this skill |
|---|---|---|
| effort | download + unzip once | one command |
| raw content reaches a model before redaction | **no** | **yes, once** |

If they care about the second row, the alternative is the export path, or a
self-hosted Notion MCP with an integration token driven from Python — neither
routes content through a model. If they don't, this is strictly more
convenient. Ask once; don't re-litigate it on later runs.

## What this skill does not do

**You are a pipe, not an editor.** Three rules, and the first one is not
negotiable:

1. **Never paraphrase, summarise or clean up page text.** Copy it verbatim.
   The evidence layer verifies every quote against the exact text a model was
   shown; a tidied-up page silently breaks that check, and a summarised one
   makes it impossible to tell your words from the user's.
2. **Do not filter by judgement.** You will be tempted to skip pages that look
   personal or trivial. Don't — `career stage` scores every page with
   `career.relevance` and reports the distribution, which is tunable and
   auditable. Your taste is neither.
3. **Do not fetch what wasn't asked for.** Default to the user's own pages.
   Shared team spaces contain other people's material and other people did not
   consent to this.

## Procedure

1. **Scope it with the user.** Whole workspace, or specific spaces/pages?
   How far back? Default: their own pages, no date limit.

2. **Find pages.** Use `notion-search` (and `notion-list-recent-pages` /
   `notion-list-private-pages` where useful). Collect page ids — do not fetch
   yet.

3. **Fetch each page** with `notion-fetch`. Keep: the page title, the full
   markdown body, `created_time` and `last_edited_time`, and the page id.

4. **Write one JSONL line per page** to a scratch file:

   ```json
   {"native_id":"<page id>","title":"<page title>","created_at":"<created_time>","updated_at":"<last_edited_time>","text":"<verbatim markdown body>"}
   ```

5. **Stage it:**

   ```bash
   python3 -m career stage --connector notion-mcp --source-type notes --file /path/to/pages.jsonl
   ```

   Add `--append` if you are importing in batches; without it the folder is
   replaced, which is what you want for a fresh full import.

6. **Report**: how many pages were fetched, how many `stage` kept, and the
   relevance histogram it printed. If most pages fell below the cutoff, say so
   and mention `--min-relevance` rather than silently importing almost
   nothing.

7. **Point at the next step**: `python3 -m career scan && python3 -m career prep`,
   then `/redaction-review` before anything goes further.

## Scale

A large workspace is a lot of `notion-fetch` calls and a lot of context.
Fetch in batches of 20–30, staging each batch with `--append`, and stop to
check with the user if you are past ~200 pages. Volume also has a downstream
cost: `notes` material shares one source-type quota, so importing 2000 pages
does not get 2000 pages read — it just makes the selection noisier.
