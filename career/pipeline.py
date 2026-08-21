"""Wires the stages together: scan -> redact -> score -> select -> read packs."""

from __future__ import annotations

import json
from pathlib import Path

from . import ingest, triage
from .config import Config
from .ingest import Document
from .redact import (Redactor, RedactionError, Vault, excise_topics,
                     gitleaks_literals, secret_flood)


def build_redactor(cfg: Config) -> Redactor:
    """Config terms plus, optionally, every identity in your git history.

    Harvesting git identities matters more than it looks: colleagues' names
    and work emails are the PII most likely to be sitting in commit trailers
    and code comments, and no generic detector knows they are sensitive.
    Your own identities collapse to ``[[ME_xx]]`` so the model can still tell
    which contributions are yours.
    """
    vault = Vault.load(cfg.vault_path)
    terms = list(cfg.sensitive_terms)
    mine = {a.strip().lower() for a in cfg.authors if a.strip()}

    if cfg.pseudonymize_git_identities:
        seen: set[str] = set()
        for root in cfg.roots:
            repo = ingest.repo_root(root)
            if not repo:
                continue
            for name, email, _commits in ingest.git_identities(repo):
                for value in (name, email):
                    key = value.strip().lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    is_me = any(m in key or key in m for m in mine)
                    terms.append({"term": value, "label": "ME" if is_me else "PERSON"})
    return Redactor(vault, terms=terms, allowlist=cfg.allowlist,
                    keep_pii_reversible=cfg.keep_pii_reversible)


def run_scan(cfg: Config) -> tuple[list[Document], dict]:
    docs, stats = ingest.scan(cfg.scan_roots, cfg.authors, since=cfg.since)
    ingest.write_manifest(docs, cfg.manifest_path)
    (cfg.ws / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), "utf-8")
    return docs, stats


def run_prep(cfg: Config, use_gitleaks: bool = True, verbose: bool = False) -> dict:
    docs = ingest.read_manifest(cfg.manifest_path)
    redactor = build_redactor(cfg)

    external: list[str] = []
    if use_gitleaks:
        for root in cfg.roots:
            external.extend(gitleaks_literals(root))
        external = sorted(set(external))

    red_dir = cfg.ws / "03_redacted"
    red_dir.mkdir(parents=True, exist_ok=True)

    scored: list[triage.Scored] = []
    reports: list[dict] = []
    failures: list[dict] = []
    topic_stats: dict[str, int] = {}
    excised_blocks = 0
    by_id: dict[str, Document] = {d.id: d for d in docs}

    for doc in docs:
        text, status = ingest.extract_text(Path(doc.path))
        doc.extract, doc.chars = status, len(text)
        if status != "ok" or not text.strip():
            failures.append({"path": doc.path, "reason": status or "empty"})
            continue
        try:
            redacted, report = redactor.redact_text(text, source=doc.id,
                                                    extra_literals=external)
        except (RedactionError, OSError, ValueError) as exc:
            # Fail closed: a document we cannot fully clean simply does not
            # enter the corpus.
            failures.append({"path": doc.path, "reason": f"redaction:{exc}"})
            continue
        flood = secret_flood(report)
        if flood:
            failures.append({"path": doc.path, "reason": flood})
            continue
        if cfg.topic_applies_to(doc.source_type):
            outcome = excise_topics(redacted, cfg.topics(),
                                    float((cfg.topic_policy or {}).get("drop_ratio", 0.5)))
            for name, n in outcome.topics.items():
                topic_stats[name] = topic_stats.get(name, 0) + n
            if outcome.dropped:
                failures.append({"path": doc.path, "reason": outcome.drop_reason})
                continue
            redacted = outcome.text
            excised_blocks += outcome.excised_blocks
        reports.append(report.to_dict())
        (red_dir / f"{doc.id}.txt").write_text(redacted, "utf-8")
        s = triage.score_document(doc, redacted)
        # Paths leak twice over: the absolute prefix exposes the local
        # filesystem, and folder names are often the most sensitive strings in
        # the whole corpus (`clients/acme-bank/...`). Relativise, then redact.
        s.path = redactor.redact_text(_display_path(doc), source=f"{doc.id}:path")[0]
        scored.append(s)
        if verbose:
            print(f"  {s.score:6.2f} {s.artifact_class:16} {s.path}")

    scored = triage.select(scored, cfg.budget_tokens, excerpt_cap=cfg.excerpt_cap,
                           per_source_share=cfg.per_source_share)
    triage.to_jsonl(scored, cfg.shortlist_path)

    corpus: dict[str, str] = {}
    for s in scored:
        if not s.selected:
            continue
        raw = (red_dir / f"{s.doc_id}.txt").read_text("utf-8")
        text, strategy = triage.excerpt(raw, cfg.excerpt_cap)
        s.excerpt_strategy = strategy
        corpus[s.doc_id] = text
    cfg.corpus_path.write_text(json.dumps(corpus, ensure_ascii=False), "utf-8")

    packs = write_packs(cfg, scored, corpus, by_id)

    summary = triage.summarise(scored, cfg.excerpt_cap)
    summary["packs"] = packs
    summary["extract_failures"] = failures[:50]
    summary["extract_failure_count"] = len(failures)
    summary["external_secret_literals"] = len(external)
    summary["topic_excisions"] = topic_stats
    summary["excised_blocks"] = excised_blocks
    summary["redaction_totals"] = _totals(reports)
    cfg.redaction_report_path.write_text(
        json.dumps({"documents": reports, "totals": summary["redaction_totals"],
                    "topic_excisions": topic_stats, "excised_blocks": excised_blocks,
                    "failures": failures}, ensure_ascii=False, indent=2), "utf-8")
    (cfg.ws / "prep-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    return summary


def _display_path(doc: Document) -> str:
    try:
        rel = Path(doc.path).relative_to(doc.root)
    except ValueError:
        return Path(doc.path).name
    return f"{Path(doc.root).name}/{rel}"


def _totals(reports: list[dict]) -> dict:
    totals: dict[str, int] = {}
    for r in reports:
        for label, n in r["counts"].items():
            totals[label] = totals.get(label, 0) + n
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


PACK_HEADER = """<!-- READ PACK {n}/{total} -- redacted. Aliases like [[ORG_01]] / [[ME_01]]
are stable across the whole corpus: the same alias always means the same real
entity. [[LABEL_REDACTED:xxxxxx]] was a credential and is gone for good.
"... [N lines elided] ..." marks a structural reduction, not the end of the file. -->

"""


def write_packs(cfg: Config, scored: list[triage.Scored], corpus: dict[str, str],
                by_id: dict[str, Document]) -> list[str]:
    """Group selected excerpts into model-sized bundles.

    Packs are ordered so that related material travels together: same repo,
    then artifact class, then score. A model reading one coherent slab of a
    project writes better cards than one hopping between six code bases.
    """
    cfg.packs_dir.mkdir(parents=True, exist_ok=True)
    for old in cfg.packs_dir.glob("pack-*.md"):
        old.unlink()

    selected = [s for s in scored if s.selected and s.doc_id in corpus]
    selected.sort(key=lambda s: (s.repo or "", s.artifact_class, -s.score))

    packs: list[list[triage.Scored]] = [[]]
    used = 0
    for s in selected:
        cost = triage.estimate_tokens(corpus[s.doc_id])
        if used + cost > cfg.pack_tokens and packs[-1]:
            packs.append([])
            used = 0
        packs[-1].append(s)
        used += cost

    written = []
    for i, group in enumerate(packs, 1):
        if not group:
            continue
        parts = [PACK_HEADER.format(n=i, total=len([p for p in packs if p])).rstrip() + "\n"]
        for s in group:
            doc = by_id.get(s.doc_id)
            meta = [f"id={s.doc_id}", f"class={s.artifact_class}"]
            if doc:
                meta += [f"lang={doc.lang}", f"my_line_share={doc.my_line_share}",
                         f"commits={doc.commits}"]
                if doc.last_commit:
                    meta.append(f"last_touched={doc.last_commit[:10]}")
            meta.append(f"excerpt={s.excerpt_strategy}")
            parts.append(f"\n\n### SOURCE {s.doc_id}\n"
                         f"`{s.path}`\n"
                         f"<!-- {' '.join(meta)} -->\n\n"
                         f"```\n{corpus[s.doc_id]}\n```\n")
        path = cfg.packs_dir / f"pack-{i:02d}.md"
        path.write_text("".join(parts), "utf-8")
        written.append(str(path))
    return written


def dump_shortlist(cfg: Config) -> list[dict]:
    return [json.loads(l) for l in cfg.shortlist_path.read_text("utf-8").splitlines() if l.strip()]
