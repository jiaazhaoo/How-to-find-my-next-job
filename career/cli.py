"""Command line entry point: ``python -m career <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cards as cards_mod
from . import interview as interview_mod
from . import profile as profile_mod
from . import pipeline, triage
from .config import DEFAULT_CONFIG_PATH, Config, write_template
from .discover import find_repos, git_identity
from .redact import Redactor, Vault, capability_report


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def cmd_init(args: argparse.Namespace) -> int:
    from .config import TEMPLATE
    from .discover import detect_sources

    path = Path(args.config)
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)")
        return 1

    found = detect_sources()
    template = json.loads(json.dumps(TEMPLATE))   # deep copy
    if found["identity"]:
        template["authors"] = found["identity"]

    repos = find_repos(identity=found["identity"]) if found["identity"] else []
    keep = [r for r in repos if r.my_commits >= args.min_commits][:args.max_repos]
    if keep:
        template["sources"] = [r.path for r in keep]
    for name, info in found["connectors"].items():
        if info.get("discovered_path"):
            template["connectors"].setdefault(name, {})["path"] = info["discovered_path"]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"wrote {path}\n")

    if found["identity"]:
        print(f"  authors      : {', '.join(found['identity'])}   (from git config)")
    else:
        print("  authors      : NOT FOUND -- set `git config user.name/user.email`, "
              "or fill it in by hand, or ownership scoring is disabled")
    for name, info in sorted(found["connectors"].items()):
        mark = "ready" if info["ready"] else "-"
        extra = f"   (found {info['discovered_path']})" if info.get("discovered_path") else ""
        print(f"  {name:14}: {mark}{extra}")

    if keep:
        print(f"\n  sources      : {len(keep)} repo(s) you have committed to")
        for r in keep[:12]:
            print(f"      {r.my_commits:5} commits  {r.my_share:.0%} yours  "
                  f"last {r.last_mine or '?':10}  {r.path}")
        if len(keep) > 12:
            print(f"      ... and {len(keep) - 12} more, all written to the config")
        if len(repos) > len(keep):
            print(f"      ({len(repos) - len(keep)} more had fewer than "
                  f"{args.min_commits} commits from you -- see `career repos`)")
    else:
        print("\n  sources      : no repos found with commits from you -- "
              "add paths by hand, or check `authors`")

    print("\nOne thing still needs you, because it is not a fact on disk:\n"
          "  `sensitive_terms` -- client and project code names. "
          "No scanner knows these are confidential.\n"
          "\nAlso worth a look: `career repos` shows every repo it found and why.\n"
          "\nThen: python -m career doctor")
    return 0


def cmd_repos(args: argparse.Namespace) -> int:
    """Show the authorship evidence behind `sources`.

    Deliberately not a judgement call by a model: every line here is a count
    git recorded at the time, so you can disagree with a number rather than
    with an opinion.
    """
    try:
        cfg = Config.load(Path(args.config))
        identity = cfg.authors
        configured = {str(Path(p).expanduser().resolve()) for p in cfg.sources}
    except (FileNotFoundError, ValueError):
        identity, configured = git_identity(), set()

    if not identity:
        print("no git identity -- set `git config user.name/user.email` or fill in `authors`")
        return 1

    repos = find_repos(identity=identity, hints=args.search or None)
    if not repos:
        print(f"no repositories with commits by {', '.join(identity)}")
        return 1

    print(f"repositories with commits by {', '.join(identity)}\n")
    print(f"  {'commits':>8} {'yours':>6} {'authors':>7}  {'last':10}  path")
    for r in repos:
        mark = "*" if str(Path(r.path).resolve()) in configured else " "
        if r.my_commits < args.min_commits:
            mark = "-"
        print(f"{mark} {r.my_commits:8} {r.my_share:6.0%} {r.authors:7}  "
              f"{r.last_mine or '?':10}  {r.path}")
    print("\n  * already in `sources`   - below --min-commits")

    if args.write:
        keep = [r.path for r in repos if r.my_commits >= args.min_commits]
        data = json.loads(Path(args.config).read_text("utf-8"))
        data["sources"] = keep
        Path(args.config).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"\nwrote {len(keep)} path(s) to `sources` in {args.config}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    caps = capability_report()
    print("redaction capabilities")
    print(f"  built-in rules   : {caps['builtin_rules']}")
    for tool in ("gitleaks", "trufflehog", "presidio"):
        state = "available" if caps[tool] else "not installed"
        print(f"  {tool:17}: {state}")
    if not caps["gitleaks"]:
        print("\n  gitleaks adds a second, independent opinion on credentials.")
        print("  Install: https://github.com/gitleaks/gitleaks  (optional but recommended)")
    if not caps["presidio"]:
        print("  presidio adds NER for names/orgs the regexes cannot see.")
        print("  Install: pip install presidio-analyzer && python -m spacy download en_core_web_lg")
    print(f"\n  pdf extraction   : {caps['pdf'] or 'MISSING'}")
    if not caps["pdf"]:
        print("  Without it every PDF is dropped silently into redaction-report failures.")
        print("  Install: pip install pypdf   (or apt-get install poppler-utils)")
    try:
        cfg = Config.load(Path(args.config))
    except (FileNotFoundError, ValueError) as exc:
        print(f"\nconfig: {exc}")
        return 1
    from .discover import detect_sources
    found = detect_sources(cfg.connectors)
    if not cfg.authors and found["identity"]:
        print(f"\n  git identity found: {', '.join(found['identity'])} "
              f"-- copy it into `authors`")
    for name, info in sorted(found["connectors"].items()):
        if info.get("discovered_path") and not cfg.connectors.get(name, {}).get("path"):
            print(f"\n  {name}: found an export at {info['discovered_path']}")
            print(f"    set connectors['{name}'].path to use it")
    if not cfg.connectors.get("notion-export", {}).get("path"):
        print("\n  no Notion export configured -- if Notion MCP is connected in your CLI,")
        print("    run the `import-notion` skill instead of exporting by hand")

    print(f"\nconfig {args.config}")
    print(f"  sources          : {len(cfg.sources)}")
    missing = [str(r) for r in cfg.roots if not r.exists()]
    for r in cfg.roots:
        print(f"    {'!' if not r.exists() else '-'} {r}")
    print(f"  authors          : {cfg.authors or '(none -- ownership scoring disabled)'}")
    print(f"  sensitive terms  : {len(cfg.sensitive_terms)}")
    print(f"  token budget     : {cfg.budget_tokens:,}")
    if missing:
        print(f"\n  {len(missing)} source path(s) do not exist")
        return 1
    if not cfg.authors:
        print("\n  warning: without `authors`, every file scores as if you did not write it")
    return 0


def cmd_connectors(args: argparse.Namespace) -> int:
    from .connectors import REGISTRY
    try:
        cfg = Config.load(Path(args.config))
    except (FileNotFoundError, ValueError):
        cfg = None
    print("connectors (import into staging; they never read for the model)\n")
    for name, connector in sorted(REGISTRY.items()):
        conf = (cfg.connectors.get(name, {}) if cfg else {})
        ok, note = connector.available(conf)
        enabled = conf.get("enabled", True)
        state = "ready" if ok else "unavailable"
        if ok and not enabled:
            state = "disabled in config"
        print(f"  {name:14} [{state}]")
        print(f"      {connector.description}")
        print(f"      {note}")
        if cfg:
            staged = list((cfg.staging_root / name).glob("*.md")) \
                if cfg.staging_root.exists() else []
            if staged:
                print(f"      staged: {len(staged)} item(s)")
    print("\n  career connect <name>   imports into workspace/00_staging/<name>/")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    from .connectors import REGISTRY, write_items
    cfg = Config.load(Path(args.config))
    connector = REGISTRY.get(args.connector)
    if not connector:
        print(f"unknown connector {args.connector!r}; known: {', '.join(sorted(REGISTRY))}")
        return 1
    conf = cfg.connectors.get(args.connector, {})
    ok, note = connector.available(conf)
    if not ok:
        print(f"{args.connector} unavailable: {note}")
        return 1
    if conf.get("enabled") is False and not args.force:
        print(f"{args.connector} is disabled in config (use --force)")
        return 1

    items = connector.fetch(conf, limit=args.limit)
    if not items:
        print(f"{args.connector}: nothing met the import threshold ({note})")
        for problem in getattr(connector, "problems", [])[:10]:
            print(f"  {problem}")
        return 1
    out, n = write_items(cfg.ws, args.connector, items, clean=not args.append)
    print(f"{args.connector}: staged {n} item(s) -> {out}")

    spans = [i.created_at[:10] for i in items if i.created_at]
    if spans:
        print(f"  span          : {min(spans)} .. {max(spans)}")
    repos: dict[str, int] = {}
    for i in items:
        r = i.meta.get("repo") or i.meta.get("breadcrumbs")
        if r:
            repos[r] = repos.get(r, 0) + 1
    if repos:
        top = sorted(repos.items(), key=lambda kv: -kv[1])[:6]
        print(f"  contexts      : {', '.join(f'{k}({v})' for k, v in top)}")
    words = sum(int(i.meta.get("human_chars") or 0) for i in items)
    if words:
        print(f"  your words    : {words:,} chars across {n} item(s)")
    unparsed = sum(int(i.meta.get("unparsed_records") or 0) for i in items)
    if unparsed:
        print(f"  unparsed      : {unparsed} record(s) in an unrecognised shape "
              f"(format may have changed)")
    counts = getattr(connector, "last_counts", None)
    if counts:
        print(f"  filtered      : " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    skipped = getattr(connector, "last_skipped", 0)
    if skipped:
        print(f"  below cutoff  : {skipped} item(s) scored under min_relevance")
    problems = getattr(connector, "problems", None)
    if problems:
        print(f"  notes         :")
        for note in problems[:10]:
            print(f"      {note}")
        if len(problems) > 10:
            print(f"      ... and {len(problems) - 10} more")
    hist = getattr(connector, "last_histogram", None)
    if hist:
        print(f"  relevance     : " + "  ".join(f"{k}:{v}" for k, v in hist.items()))
        print(f"                  (tune connectors['{args.connector}'].min_relevance)")
    print(f"\nNext: python -m career scan   (staging is picked up automatically)")
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    """Stage items an agent fetched itself (e.g. over an MCP connector).

    Keeps the contract intact: an agent may do the *fetching* -- that is what
    an OAuth-only MCP server is good for -- but the material still lands as
    files in staging and still goes through redaction like everything else.
    Header format, relevance scoring and provenance stay here, in tested code,
    rather than being hand-rolled differently by each importer.
    """
    from .connectors import StagedItem, write_items
    from .relevance import histogram, work_score

    cfg = Config.load(Path(args.config))
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text("utf-8")
    threshold = args.min_relevance

    items, scores, skipped, bad = [], [], 0, 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            text = rec["text"]
            native_id = str(rec["native_id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            bad += 1
            continue
        if not text.strip():
            continue
        score, signals = work_score(text)
        scores.append(score)
        if score < threshold:
            skipped += 1
            continue
        header = (f"<!-- source_type={args.source_type} connector={args.connector} "
                  f"work_relevance={score} -->")
        items.append(StagedItem(
            native_id=native_id, title=rec.get("title") or native_id,
            text=f"{header}\n\n{text}",
            created_at=rec.get("created_at", ""), updated_at=rec.get("updated_at", ""),
            meta={"source_type": args.source_type, "work_relevance": score,
                  "signals": signals, **(rec.get("meta") or {})}))

    if not items:
        print(f"nothing staged ({skipped} below cutoff, {bad} unparseable)")
        return 1
    out, n = write_items(cfg.ws, args.connector, items, clean=not args.append)
    print(f"{args.connector}: staged {n} item(s) -> {out}")
    if skipped:
        print(f"  below cutoff  : {skipped} (min_relevance={threshold})")
    if bad:
        print(f"  unparseable   : {bad} line(s)")
    print(f"  relevance     : " + "  ".join(f"{k}:{v}" for k, v in histogram(scores).items()))
    print(f"\nNext: python -m career scan")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = Config.load(Path(args.config))
    docs, stats = pipeline.run_scan(cfg)
    kept_pct = 100 * stats["bytes_kept"] / max(stats["bytes_seen"], 1)
    print(f"files seen      : {stats['seen']:,}")
    print(f"  excluded      : {stats['excluded']:,}")
    for reason, n in sorted(stats["reasons"].items(), key=lambda kv: -kv[1])[:8]:
        print(f"      {reason:24} {n:,}")
    print(f"  duplicates    : {stats['duplicates']:,}")
    print(f"kept            : {stats['kept']:,}  ({_human(stats['bytes_kept'])} of "
          f"{_human(stats['bytes_seen'])}, {kept_pct:.1f}%)")
    print(f"manifest        : {cfg.manifest_path}")
    if not docs:
        print("\nnothing to read -- check `sources` in your config")
        return 1
    return 0


def cmd_prep(args: argparse.Namespace) -> int:
    cfg = Config.load(Path(args.config))
    if not cfg.manifest_path.exists():
        print("no manifest; run `python -m career scan` first")
        return 1
    s = pipeline.run_prep(cfg, use_gitleaks=not args.no_gitleaks, verbose=args.verbose)
    print(f"\nredacted        : {sum(s['redaction_totals'].values()):,} spans")
    for label, n in list(s["redaction_totals"].items())[:10]:
        print(f"      {label:22} {n:,}")
    if s["extract_failure_count"]:
        print(f"  unreadable    : {s['extract_failure_count']} file(s) "
              f"(see redaction-report.json)")
    print(f"\ncandidates      : {s['candidates']:,}  ({s['candidate_tokens']:,} est. tokens)")
    print(f"selected        : {s['selected']:,}  ({s['selected_tokens']:,} est. tokens, "
          f"{100 * s['selected_tokens'] / max(s['candidate_tokens'], 1):.1f}% of the pile)")
    print(f"  by class      : {s['by_class']}")
    print(f"  repos         : {', '.join(s['repos']) or '(none)'}")
    print(f"\nread packs      : {len(s['packs'])}")
    for p in s["packs"]:
        print(f"      {p}")
    print(f"\nNext: run the `deep-read` skill over each pack, appending cards to "
          f"{cfg.cards_path}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = Config.load(Path(args.config))
    path = Path(args.cards or cfg.cards_path)
    if not path.exists():
        print(f"{path} not found")
        return 1
    try:
        cards = cards_mod.load(path, strict=not args.lenient)
    except cards_mod.CardError as exc:
        print("card validation failed:\n" + str(exc))
        return 1
    corpus = json.loads(cfg.corpus_path.read_text("utf-8")) if cfg.corpus_path.exists() else {}
    stats = cards_mod.verify_quotes(cards, corpus)
    print(f"cards           : {stats['cards_total']}")
    print(f"quotes checked  : {stats['checked']}")
    print(f"  found         : {stats['ok']}")
    print(f"  not in source : {stats['not_found']}")
    print(f"  unknown source: {stats['missing_source']}")
    print(f"cards verified  : {stats['cards_verified']}/{stats['cards_total']}")
    bad = [c for c in cards if not c.verified]
    for c in bad[:10]:
        print(f"  ! {c.id} {c.claim[:60]}")
    return 0 if not bad else 2


def cmd_themes(args: argparse.Namespace) -> int:
    cfg = Config.load(Path(args.config))
    path = Path(args.cards or cfg.cards_path)
    cards = cards_mod.load(path, strict=False)
    if cfg.corpus_path.exists():
        cards_mod.verify_quotes(cards, json.loads(cfg.corpus_path.read_text("utf-8")))
    usable = [c for c in cards if c.verified is not False] if args.verified_only else cards
    themes = cards_mod.cluster(usable)
    ts = cards_mod.tensions(usable)
    payload = {"themes": [t.to_dict() for t in themes], "tensions": ts,
               "counts": {"cards": len(usable),
                          "patterns": sum(1 for t in themes if t.status == "pattern"),
                          "anecdotes": sum(1 for t in themes if t.status == "anecdote")}}
    cfg.themes_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")

    print(f"cards           : {payload['counts']['cards']}")
    print(f"patterns        : {payload['counts']['patterns']}  "
          f"(>=2 independent sources)")
    print(f"anecdotes       : {payload['counts']['anecdotes']}\n")
    for t in themes[:15]:
        mark = "*" if t.status == "pattern" else " "
        print(f" {mark} [{t.strength:5.2f}] {t.label[:70]}")
        print(f"      skills={','.join(t.skills[:6])}  sources={len(t.sources)}")
    if ts:
        print(f"\ntensions ({len(ts)}) -- these are the interview questions:")
        for t in ts[:10]:
            print(f"  ? {t['claim'][:70]}")
            print(f"      vs {t['counter'][:66]}")
    print(f"\nwrote {cfg.themes_path}")
    return 0


def _load_graph(cfg: Config, cards_path: Path | None = None):
    """Cards plus themes, with quotes verified where the corpus is available."""
    path = cards_path or cfg.cards_path
    cards = cards_mod.load(path, strict=False)
    if cfg.corpus_path.exists():
        corpus = json.loads(cfg.corpus_path.read_text("utf-8"))
        # Interview answers verify against the answer file, not the corpus.
        corpus.setdefault("interview", "")
        if cfg.answers_path.exists():
            corpus["interview"] = cfg.answers_path.read_text("utf-8")
        cards_mod.verify_quotes(cards, corpus)
    return cards, cards_mod.cluster(cards)


def cmd_questions(args: argparse.Namespace) -> int:
    cfg = Config.load(Path(args.config))
    if not cfg.cards_path.exists():
        print("no cards yet; run the `deep-read` skill first")
        return 1
    cards, themes = _load_graph(cfg)
    candidates = interview_mod.generate(cards, themes, now_year=args.year)
    if not candidates:
        print("no questions generated -- the record has no gaps or contradictions "
              "to ask about, which usually means too few cards")
        return 1
    chosen = interview_mod.rank(candidates, limit=args.limit)
    interview_mod.dump(chosen, cfg.questions_path,
                       extra={"considered": len(candidates)})

    print(f"considered      : {len(candidates)} candidate(s)")
    print(f"selected        : {len(chosen)}\n")
    for i, c in enumerate(chosen, 1):
        print(f"{i}. [{c.kind}  w={c.weight:.1f}]  {c.subject}")
        print(f"   记录显示 : {c.observation}")
        print(f"   能解决   : {c.resolves}")
        print(f"   角度     : {interview_mod.CCI_ANGLES.get(c.angle, c.angle)}")
        print(f"   草稿     : {c.draft}")
        print(f"   依据卡片 : {', '.join(c.card_ids) or '-'}\n")
    cov = interview_mod.coverage(chosen)
    print(f"coverage        : {cov['kinds']}")
    if cov["missing_angles"]:
        print(f"  angles unused : {', '.join(cov['missing_angles'])}")
    print(f"\nwrote {cfg.questions_path}")
    print("Next: run the `interview` skill to ask these properly, then "
          "`career answers --file <answers.jsonl>`")
    return 0


def cmd_answers(args: argparse.Namespace) -> int:
    """Fold interview answers back in as cards from an independent source."""
    cfg = Config.load(Path(args.config))
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text("utf-8")
    records = []
    for line in raw.splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"skipping unparseable line: {line[:60]}")
    new_cards = interview_mod.answers_to_cards(records)
    if not new_cards:
        print("no answers to record")
        return 1

    with cfg.answers_path.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    existing = cards_mod.load(cfg.cards_path, strict=False) if cfg.cards_path.exists() else []
    known = {c.id for c in existing}
    added = [c for c in new_cards if c.id not in known]
    cards_mod.dump(existing + added, cfg.cards_path)

    print(f"recorded        : {len(records)} answer(s)")
    print(f"new cards       : {len(added)}")
    promoted = [t for t in cards_mod.cluster(existing + added)
                if t.status == "pattern" and any(
                    e.source == "interview" for c in t.cards for e in c.evidence)]
    if promoted:
        print(f"\n你的回答让 {len(promoted)} 个主题获得了独立佐证：")
        for t in promoted[:5]:
            print(f"  - {t.label[:60]}")
    print(f"\nNext: python -m career profile")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    cfg = Config.load(Path(args.config))
    cards, themes = _load_graph(cfg)

    if args.check:
        target = Path(args.check)
        if not target.exists():
            print(f"{target} not found")
            return 1
        skeleton = profile_mod.build_skeleton(cards, themes)
        problems = profile_mod.validate(target.read_text("utf-8"), cards, skeleton)
        errors = [p for p in problems if p.severity == "error"]
        for p in problems:
            print(f"  {p.severity:7} [{p.where}] {p.message}")
        print(f"\n{len(errors)} error(s), {len(problems) - len(errors)} warning(s)")
        if not problems:
            print("画像的每一条断言都挂在证据上。")
        return 0 if not errors else 2

    skeleton = profile_mod.build_skeleton(cards, themes)
    profile_mod.dump_skeleton(skeleton, cfg.skeleton_path)
    st = skeleton.stats
    if st["quote_check_ran"]:
        print(f"cards           : {st['cards']}  "
              f"({st['verified']} 引用已核对, {st['failed']} 未通过)")
    else:
        print(f"cards           : {st['cards']}  "
              f"(引用未核对——找不到 {cfg.corpus_path.name}，先跑 prep)")
    print(f"可以写进画像的   : {st['patterns']} 个主题  "
          f"（{st['citable_ids']} 张卡片可被引用）")
    print(f"只能标为单一来源 : {st['anecdotes']}")
    print(f"只能标为自述     : {st['self_reports']}\n")
    for e in skeleton.qualified[:12]:
        print(f"  * [{e.strength:5.2f}] {e.label[:60]}")
        print(f"      skills={','.join(e.skills[:5])}  sources={e.sources}  "
              f"roles={','.join(e.role_signals)}  max_difficulty={e.difficulty_max}")
        for stance in e.stance[:2]:
            print(f"      本人表态: {stance[:64]}")
    if skeleton.gaps:
        print(f"\n记录读不出来的（必须写进画像）: {len(skeleton.gaps)}")
        for g in skeleton.gaps[:6]:
            print(f"  ? {g[:70]}")
    print(f"\nwrote {cfg.skeleton_path}")
    print("Next: run the `profile` skill to write it, then "
          "`career profile --check workspace/09_profile.md`")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    cfg = Config.load(Path(args.config))
    vault = Vault.load(cfg.vault_path)
    text = Path(args.file).read_text("utf-8") if args.file != "-" else sys.stdin.read()
    sys.stdout.write(vault.restore(text))
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    """One-off: redact a single file or stdin. Useful for ad-hoc pasting."""
    cfg = Config.load(Path(args.config))
    redactor = pipeline.build_redactor(cfg)
    text = Path(args.file).read_text("utf-8") if args.file != "-" else sys.stdin.read()
    out, report = redactor.redact_text(text, source=args.file)
    sys.stdout.write(out)
    if args.report:
        sys.stderr.write(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover("tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="career",
        description="Redact, then read deeply: the input layer for evidence-based "
                    "career profiling.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = p.add_subparsers(dest="command", required=True)

    # Accept --config on either side of the subcommand. SUPPRESS keeps the
    # subparser from stomping the global value with its own default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS)

    s = sub.add_parser("init", parents=[common], help="write a config template")
    s.add_argument("--force", action="store_true")
    s.add_argument("--min-commits", type=int, default=3,
                   help="how many commits of yours make a repo count (default 3)")
    s.add_argument("--max-repos", type=int, default=40)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("repos", parents=[common],
                       help="show every git repo you have committed to, and why")
    s.add_argument("--min-commits", type=int, default=3)
    s.add_argument("--search", action="append", help="extra directory to search (repeatable)")
    s.add_argument("--write", action="store_true", help="write the result into `sources`")
    s.set_defaults(func=cmd_repos)

    s = sub.add_parser("doctor", parents=[common], help="check redaction tooling and config")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("connectors", parents=[common], help="list import connectors")
    s.set_defaults(func=cmd_connectors)

    s = sub.add_parser("connect", parents=[common], help="import a source into staging")
    s.add_argument("connector")
    s.add_argument("--limit", type=int)
    s.add_argument("--append", action="store_true", help="keep previously staged items")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_connect)

    s = sub.add_parser("stage", parents=[common],
                       help="stage items fetched elsewhere (JSONL in, files out)")
    s.add_argument("--connector", required=True, help="name for the staging folder, e.g. notion-mcp")
    s.add_argument("--source-type", default="notes", choices=["notes", "chat", "public", "file"])
    s.add_argument("--file", default="-", help="JSONL path, or - for stdin")
    s.add_argument("--min-relevance", type=float, default=0.35)
    s.add_argument("--append", action="store_true")
    s.set_defaults(func=cmd_stage)

    s = sub.add_parser("scan", parents=[common], help="stage A: build the manifest")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("prep", parents=[common], help="stages B-C: redact, score, select, write read packs")
    s.add_argument("--no-gitleaks", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_prep)

    s = sub.add_parser("verify", parents=[common], help="stage D check: every quote must exist")
    s.add_argument("--cards")
    s.add_argument("--lenient", action="store_true")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("themes", parents=[common], help="stage E: cluster cards, surface tensions")
    s.add_argument("--cards")
    s.add_argument("--verified-only", action="store_true")
    s.set_defaults(func=cmd_themes)

    s = sub.add_parser("questions", parents=[common],
                       help="stage 6: mine the card graph for what to ask you")
    s.add_argument("--limit", type=int, default=7)
    s.add_argument("--year", type=int, help="current year, for dormant-skill detection")
    s.set_defaults(func=cmd_questions)

    s = sub.add_parser("answers", parents=[common],
                       help="fold interview answers back in as cards")
    s.add_argument("--file", default="-")
    s.set_defaults(func=cmd_answers)

    s = sub.add_parser("profile", parents=[common],
                       help="stage 5: what may be claimed, and check what was written")
    s.add_argument("--check", metavar="FILE", help="validate a written profile")
    s.set_defaults(func=cmd_profile)

    s = sub.add_parser("redact", parents=[common], help="redact one file or stdin")
    s.add_argument("file")
    s.add_argument("--report", action="store_true")
    s.set_defaults(func=cmd_redact)

    s = sub.add_parser("restore", parents=[common], help="put real identities back (local only)")
    s.add_argument("file")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("selftest", parents=[common], help="run the test suite")
    s.set_defaults(func=cmd_selftest)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
