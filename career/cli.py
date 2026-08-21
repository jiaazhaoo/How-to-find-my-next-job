"""Command line entry point: ``python -m career <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cards as cards_mod
from . import pipeline, triage
from .config import DEFAULT_CONFIG_PATH, Config, write_template
from .redact import Redactor, Vault, capability_report


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)")
        return 1
    write_template(path)
    print(f"wrote {path}\n\nNext:\n"
          f"  1. edit `sources` and `authors` (git name + email, so ownership works)\n"
          f"  2. add every client / project code name to `sensitive_terms`\n"
          f"  3. python -m career doctor\n"
          f"  4. python -m career scan && python -m career prep")
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
        ok, note = connector.available()
        conf = (cfg.connectors.get(name, {}) if cfg else {})
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
    ok, note = connector.available()
    if not ok:
        print(f"{args.connector} unavailable: {note}")
        return 1
    conf = cfg.connectors.get(args.connector, {})
    if conf.get("enabled") is False and not args.force:
        print(f"{args.connector} is disabled in config (use --force)")
        return 1

    items = connector.fetch(conf, limit=args.limit)
    if not items:
        print(f"{args.connector}: nothing met the import threshold ({note})")
        return 1
    out, n = write_items(cfg.ws, args.connector, items, clean=not args.append)
    print(f"{args.connector}: staged {n} item(s) -> {out}")

    spans = [i.created_at[:10] for i in items if i.created_at]
    if spans:
        print(f"  span          : {min(spans)} .. {max(spans)}")
    repos: dict[str, int] = {}
    for i in items:
        r = i.meta.get("repo") or "(none)"
        repos[r] = repos.get(r, 0) + 1
    top = sorted(repos.items(), key=lambda kv: -kv[1])[:6]
    print(f"  contexts      : {', '.join(f'{k}({v})' for k, v in top)}")
    words = sum(int(i.meta.get("human_chars") or 0) for i in items)
    if words:
        print(f"  your words    : {words:,} chars across {n} item(s)")
    print(f"\nNext: python -m career scan   (staging is picked up automatically)")
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
    s.set_defaults(func=cmd_init)

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
