"""The gate. Nothing reaches a model without passing through here.

Design commitments
------------------
1. **Fail closed.** Every redacted document is re-scanned. If a credential
   still matches after redaction, we raise instead of returning text. A gate
   that silently half-works is worse than no gate.
2. **Secrets are destroyed, identities are pseudonymised.** Credentials are
   replaced by a label plus a short fingerprint; the plaintext is never
   persisted by this pipeline. Names, emails, hosts and internal code names
   get a *stable* alias so the structure of the story survives -- which is the
   whole point, because "who I worked with and on what" is career evidence.
3. **The vault is the crown jewel.** Reversible mappings live in one file,
   0600, gitignored. Everything else in the workspace is safe to share.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import rules
from .rules import Finding, PII, SECRET, TERM


class RedactionError(RuntimeError):
    """Raised when the post-redaction verification pass still finds a secret."""


# Both alias shapes: [[EMAIL_01]] (reversible) and [[AWS_KEY_REDACTED:a1b2c3]] (destroyed).
ALIAS_RE = re.compile(r"\[\[[A-Z0-9_]+(?::[0-9a-f]{6})?\]\]")


def _canonical(value: str) -> str:
    """Case- and whitespace-insensitive key for identity folding."""
    return re.sub(r"\s+", " ", value.strip()).lower()


def _fingerprint(value: str, salt: str) -> str:
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()[:12]


@dataclass
class Vault:
    """Fingerprint -> alias, plus the reverse map for pseudonymised entities."""

    path: Path
    salt: str = ""
    aliases: dict[str, str] = field(default_factory=dict)      # fingerprint -> alias
    originals: dict[str, str] = field(default_factory=dict)    # alias -> plaintext (PII only)
    counters: dict[str, int] = field(default_factory=dict)     # label -> next index

    @classmethod
    def load(cls, path: Path) -> "Vault":
        path = Path(path)
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            return cls(path=path, salt=data.get("salt", ""),
                       aliases=data.get("aliases", {}),
                       originals=data.get("originals", {}),
                       counters=data.get("counters", {}))
        v = cls(path=path, salt=hashlib.sha256(os.urandom(32)).hexdigest())
        v.save()
        return v

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "_warning": "Contains the mapping back to real names/emails. Never commit or upload.",
            "salt": self.salt, "aliases": self.aliases,
            "originals": self.originals, "counters": self.counters,
        }, ensure_ascii=False, indent=2), "utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)

    def alias_for(self, value: str, label: str, reversible: bool,
                  canonical: str | None = None) -> str:
        """``canonical`` folds surface variants ("Acme Corp" / "acme corp")
        onto one alias. The first surface form seen is what ``restore`` puts
        back, which is good enough for reading a report locally."""
        fp = _fingerprint(canonical or value, self.salt)
        if fp in self.aliases:
            return self.aliases[fp]
        idx = self.counters.get(label, 0) + 1
        self.counters[label] = idx
        alias = f"[[{label}_{idx:02d}]]"
        self.aliases[fp] = alias
        if reversible:
            self.originals[alias] = value
        return alias

    def restore(self, text: str) -> str:
        """Put the real identities back -- for reading a final report locally."""
        for alias, original in sorted(self.originals.items(), key=lambda kv: -len(kv[0])):
            text = text.replace(alias, original)
        return text


@dataclass
class RedactionReport:
    source: str
    findings: list[Finding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    verified: bool = False

    @property
    def secret_count(self) -> int:
        return sum(1 for f in self.findings if f.kind == SECRET)

    def to_dict(self) -> dict:
        return {
            "source": self.source, "verified": self.verified, "counts": self.counts,
            "findings": [
                # Deliberately no plaintext: the report itself must be shareable.
                {"rule": f.rule, "kind": f.kind, "label": f.label,
                 "fingerprint": f.fingerprint, "line_hint": f.meta.get("line"),
                 "length": len(f.text)}
                for f in self.findings
            ],
        }


class Redactor:
    def __init__(self, vault: Vault, terms: list[dict] | None = None,
                 allowlist: list[str] | None = None, keep_pii_reversible: bool = True):
        self.vault = vault
        self.keep_pii_reversible = keep_pii_reversible
        self.allowlist = {a.lower() for a in (allowlist or [])}
        self.term_rules = self._compile_terms(terms or [])

    @staticmethod
    def _compile_terms(terms: list[dict]) -> list[rules.Rule]:
        """User-declared sensitive strings: client names, project code names,
        internal system names. CJK gets no \\b, so match the literal."""
        out = []
        for i, t in enumerate(terms):
            raw = t["term"] if isinstance(t, dict) else str(t)
            label = (t.get("label") if isinstance(t, dict) else None) or "ORG"
            if t.get("regex") if isinstance(t, dict) else False:
                pat = re.compile(raw, re.IGNORECASE)
            else:
                # Tolerate "Acme  Corp" / "Acme\nCorp" wrapping in real docs.
                esc = r"\s+".join(re.escape(part) for part in raw.split())
                boundary = raw[:1].isascii() and raw[-1:].isascii()
                pat = re.compile(rf"\b{esc}\b" if boundary else esc, re.IGNORECASE)
            out.append(rules.Rule(f"term_{i}_{label.lower()}", TERM, pat, label))
        return out

    # -- core -------------------------------------------------------------
    def redact_text(self, text: str, source: str = "<memory>",
                    extra_literals: list[str] | None = None) -> tuple[str, RedactionReport]:
        active = rules.ALL_RULES + self.term_rules + self._literal_rules(extra_literals)
        findings = [f for f in rules.scan_text(text, active)
                    if f.text.lower() not in self.allowlist]

        report = RedactionReport(source=source, findings=findings)
        out, cursor = [], 0
        for f in findings:
            f.fingerprint = _fingerprint(f.text, self.vault.salt)
            f.meta["line"] = text.count("\n", 0, f.start) + 1
            reversible = self.keep_pii_reversible and f.kind in (PII, TERM)
            alias = (self.vault.alias_for(f.text, f.label, reversible,
                                          canonical=_canonical(f.text))
                     if f.kind != SECRET
                     else f"[[{f.label}_REDACTED:{f.fingerprint[:6]}]]")
            out.append(text[cursor:f.start])
            out.append(alias)
            cursor = f.end
            report.counts[f.label] = report.counts.get(f.label, 0) + 1
        out.append(text[cursor:])
        redacted = "".join(out)

        leftovers = self.verify(redacted)
        if leftovers:
            raise RedactionError(
                f"{source}: {len(leftovers)} credential(s) survived redaction "
                f"({', '.join(sorted({l.rule for l in leftovers}))}). Refusing to emit. "
                f"Add a rule or an allowlist entry, then re-run."
            )
        report.verified = True
        self.vault.save()
        return redacted, report

    @staticmethod
    def _literal_rules(literals: list[str] | None) -> list[rules.Rule]:
        """Findings handed to us by an external scanner (gitleaks) become
        literal patterns so they are removed even if our regexes missed them."""
        out = []
        for i, lit in enumerate(literals or []):
            if len(lit) < 6:
                continue
            out.append(rules.Rule(f"external_{i}", SECRET, re.compile(re.escape(lit)), "EXTERNAL_SECRET"))
        return out

    def verify(self, text: str) -> list[Finding]:
        """Second pass. Only credentials are fatal; a stray alias is fine."""
        remaining = rules.scan_text(text, rules.SECRET_RULES + self.term_rules)
        return [f for f in remaining
                if f.kind in (SECRET, TERM)
                and not ALIAS_RE.fullmatch(f.text)
                and f.text.lower() not in self.allowlist]


# --------------------------------------------------------------------------
# Optional external reinforcement. Absent tools are not an error -- they just
# mean the built-in rules are on their own.
# --------------------------------------------------------------------------
def gitleaks_literals(target: Path, timeout: int = 300) -> list[str]:
    """Run gitleaks over a directory and return the raw matched secrets."""
    if not shutil.which("gitleaks"):
        return []
    with_report = target / ".gitleaks-report.json"
    try:
        subprocess.run(
            ["gitleaks", "detect", "--source", str(target), "--no-git",
             "--report-format", "json", "--report-path", str(with_report), "--exit-code", "0"],
            capture_output=True, timeout=timeout, check=False)
        if not with_report.exists():
            return []
        data = json.loads(with_report.read_text("utf-8") or "[]")
        return [d.get("Secret", "") for d in data if d.get("Secret")]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []
    finally:
        with_report.unlink(missing_ok=True)


def presidio_findings(text: str) -> list[Finding]:
    """NER-based PII (names, orgs, locations) when Presidio is installed."""
    try:
        from presidio_analyzer import AnalyzerEngine  # type: ignore
    except Exception:
        return []
    engine = AnalyzerEngine()
    wanted = {"PERSON": "PERSON", "LOCATION": "LOCATION", "ORGANIZATION": "ORG",
              "NRP": "PERSON", "DATE_TIME": None}
    out = []
    for r in engine.analyze(text=text, language="en"):
        label = wanted.get(r.entity_type)
        if not label or r.score < 0.6:
            continue
        out.append(Finding(rule=f"presidio_{r.entity_type.lower()}", kind=PII, label=label,
                           start=r.start, end=r.end, text=text[r.start:r.end]))
    return out


def capability_report() -> dict:
    """What reinforcement is actually available on this machine."""
    try:
        import presidio_analyzer  # noqa: F401
        presidio = True
    except Exception:
        presidio = False
    return {
        "builtin_rules": len(rules.ALL_RULES),
        "gitleaks": bool(shutil.which("gitleaks")),
        "trufflehog": bool(shutil.which("trufflehog")),
        "presidio": presidio,
    }
