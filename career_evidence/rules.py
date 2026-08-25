"""Detection rules for the redaction gate.

Two families, treated very differently downstream:

* ``secret``  -- credentials. Irreversibly masked. The original is never
  written anywhere by this pipeline, not even to the alias vault.
* ``pii``     -- people, orgs, contact details, internal code names. Replaced
  by a *stable* pseudonym so that relationships survive the redaction
  ("P03 reviewed every commit of O01's payment service" is still analysable).

Everything here is stdlib-only so the gate runs on a laptop with no network
and no install step. External scanners (gitleaks, Presidio) are optional
reinforcements wired up in ``career_evidence.redact``, never prerequisites.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

SECRET = "secret"
PII = "pii"
TERM = "term"


@dataclass(frozen=True)
class Rule:
    name: str
    kind: str
    pattern: re.Pattern
    label: str
    group: int = 0
    min_entropy: float = 0.0
    validator: str | None = None


@dataclass
class Finding:
    rule: str
    kind: str
    label: str
    start: int
    end: int
    text: str
    fingerprint: str = ""
    context: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def severity(self) -> str:
        return "high" if self.kind == SECRET else "medium"


def shannon_entropy(value: str) -> float:
    """Bits per character. Random-looking blobs land above ~3.5."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def luhn_ok(digits: str) -> bool:
    nums = [int(d) for d in digits if d.isdigit()]
    if len(nums) < 13:
        return False
    checksum, parity = 0, len(nums) % 2
    for i, d in enumerate(nums):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


_CN_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_CN_ID_CHECK = "10X98765432"


def cn_id_ok(value: str) -> bool:
    """Mainland China resident ID checksum (GB 11643-1999)."""
    value = value.strip().upper()
    if len(value) != 18 or not value[:17].isdigit():
        return False
    total = sum(int(c) * w for c, w in zip(value[:17], _CN_ID_WEIGHTS))
    return _CN_ID_CHECK[total % 11] == value[17]


VALIDATORS = {"luhn": luhn_ok, "cn_id": cn_id_ok}


def _r(pattern: str, flags: int = 0) -> re.Pattern:
    return re.compile(pattern, flags)


# --------------------------------------------------------------------------
# Credentials. Ordered most-specific first; overlapping matches are resolved
# by span in redact.py, so a provider-specific hit always beats the generic
# assignment rule.
# --------------------------------------------------------------------------
SECRET_RULES: list[Rule] = [
    Rule("private_key_block", SECRET,
         _r(r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]{0,8000}?-----END[ A-Z]*PRIVATE KEY-----"),
         "PRIVATE_KEY"),
    Rule("aws_access_key", SECRET, _r(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"), "AWS_KEY"),
    Rule("aws_secret_key", SECRET,
         _r(r"(?i)aws[_\-. ]?secret[_\-. ]?(?:access[_\-. ]?)?key\W{0,4}([A-Za-z0-9/+=]{40})"),
         "AWS_SECRET", group=1),
    Rule("github_token", SECRET,
         _r(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
         "GITHUB_TOKEN"),
    Rule("gitlab_token", SECRET, _r(r"\bglpat-[A-Za-z0-9\-_]{20,}\b"), "GITLAB_TOKEN"),
    Rule("slack_token", SECRET, _r(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b"), "SLACK_TOKEN"),
    Rule("slack_webhook", SECRET,
         _r(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"), "SLACK_WEBHOOK"),
    Rule("stripe_key", SECRET, _r(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), "STRIPE_KEY"),
    Rule("openai_key", SECRET, _r(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"), "LLM_API_KEY"),
    Rule("anthropic_key", SECRET, _r(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"), "LLM_API_KEY"),
    Rule("google_api_key", SECRET, _r(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "GOOGLE_API_KEY"),
    Rule("aliyun_key", SECRET, _r(r"\bLTAI[A-Za-z0-9]{12,24}\b"), "ALIYUN_KEY"),
    Rule("tencent_key", SECRET, _r(r"\bAKID[A-Za-z0-9]{13,40}\b"), "TENCENT_KEY"),
    Rule("jwt", SECRET,
         _r(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "JWT"),
    Rule("conn_string_password", SECRET,
         _r(r"(?i)\b[a-z][a-z0-9+.\-]{2,}://[^\s:/@]{1,64}:([^\s/@]{3,})@"),
         "DB_PASSWORD", group=1),
    Rule("bearer_header", SECRET,
         _r(r"(?i)\bauthorization\s*[:=]\s*[\"']?(?:bearer|basic|token)\s+([A-Za-z0-9._\-+/=]{12,})"),
         "AUTH_HEADER", group=1),
    Rule("ssh_known_secret", SECRET,
         _r(r"(?i)\b(?:client[_\-]?secret|app[_\-]?secret|refresh[_\-]?token|access[_\-]?token)"
            r"\W{0,4}[\"']?([A-Za-z0-9._\-+/=]{16,})[\"']?"),
         "OAUTH_SECRET", group=1, min_entropy=3.0),
    # Generic assignment. Entropy-gated so `password = "changeme"` in a doc or
    # `token = self.token` in code do not drown the report in noise.
    Rule("generic_assignment", SECRET,
         _r(r"(?i)\b(?:pass(?:wd|word)?|pwd|secret|api[_\-]?key|apikey|access[_\-]?key|"
            r"private[_\-]?key|auth[_\-]?token|credentials?)\s*[:=]\s*[\"']([^\"'\n]{8,120})[\"']"),
         "CREDENTIAL", group=1, min_entropy=3.0),
    Rule("dotenv_line", SECRET,
         _r(r"(?m)^[ \t]*(?:export[ \t]+)?[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)"
            r"[A-Z0-9_]*[ \t]*=[ \t]*[\"']?([^\s\"'#]{8,200})[\"']?"),
         "ENV_SECRET", group=1, min_entropy=2.6),
]

# --------------------------------------------------------------------------
# Personal / commercial identifiers. Pseudonymised, not destroyed.
# --------------------------------------------------------------------------
PII_RULES: list[Rule] = [
    Rule("email", PII, _r(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "EMAIL"),
    Rule("cn_id_card", PII, _r(r"(?<![0-9])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                               r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9])"),
         "ID_CARD", validator="cn_id"),
    Rule("cn_mobile", PII, _r(r"(?<![0-9])1[3-9]\d{9}(?![0-9])"), "PHONE"),
    Rule("intl_phone", PII,
         _r(r"(?<![\w.])\+\d{1,3}[ \-]?\(?\d{1,4}\)?[ \-]?\d{3,4}[ \-]?\d{3,4}(?![\w.])"),
         "PHONE"),
    Rule("credit_card", PII,
         _r(r"(?<![0-9])(?:\d[ \-]?){13,19}(?![0-9])"), "PAYMENT_CARD", validator="luhn"),
    Rule("ipv4_private_or_public", PII,
         _r(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?(?![\w.])"), "IP_ADDR"),
    Rule("internal_host", PII,
         _r(r"(?i)\b[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:internal|intranet|corp|local|lan|test)\b"),
         "INTERNAL_HOST"),
    Rule("aws_account_id", PII, _r(r"\barn:aws:[a-z0-9\-]*:[a-z0-9\-]*:(\d{12}):"),
         "CLOUD_ACCOUNT", group=1),
]

ALL_RULES = SECRET_RULES + PII_RULES

# Values that match a pattern but carry no secret. Extend via config.
PLACEHOLDER_VALUES = {
    "changeme", "password", "your_password", "yourpassword", "example", "xxxxxxxx",
    "placeholder", "redacted", "dummy", "test", "secret", "none", "null", "todo",
    "<password>", "${password}", "your-api-key", "your_api_key", "sk-xxx",
    "0.0.0.0", "127.0.0.1", "255.255.255.255", "1.1.1.1", "8.8.8.8",
}

_TEMPLATE = _r(r"^\s*(?:\{\{.*\}\}|\$\{.*\}|<[^>]+>|%\(.*\)s|\$[A-Z_]+|\*+|x+|X+)\s*$")


def is_placeholder(value: str, kind: str = SECRET) -> bool:
    """Is this match a stand-in rather than a real value?

    The code-reference heuristic below applies to credentials only: a dotted
    identifier is a plausible way to write "the password lives elsewhere",
    but `db.internal` is a genuine hostname, not a reference.
    """
    v = value.strip()
    if not v:
        return True
    if v.lower() in PLACEHOLDER_VALUES:
        return True
    if _TEMPLATE.match(v):
        return True
    if kind != SECRET:
        return False
    # `token = os.environ["X"]` / `password: config.pwd` -- a reference, not a
    # value. Must show real syntax (dot / index / call), otherwise a genuine
    # `ghp_...` token would be dismissed as an identifier.
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
                r"(?:\[[^\]]*\])?(?:\(\))?$", v):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\[[^\]]*\]$", v):
        return True
    return False


def scan_text(text: str, rules: list[Rule] | None = None) -> list[Finding]:
    """Return non-overlapping findings, most specific rule winning."""
    rules = rules if rules is not None else ALL_RULES
    hits: list[Finding] = []
    for rule in rules:
        for m in rule.pattern.finditer(text):
            value = m.group(rule.group) if rule.group else m.group(0)
            if value is None:
                continue
            if is_placeholder(value, rule.kind):
                continue
            if rule.min_entropy and shannon_entropy(value) < rule.min_entropy:
                continue
            if rule.validator and not VALIDATORS[rule.validator](value):
                continue
            start = m.start(rule.group) if rule.group else m.start(0)
            end = m.end(rule.group) if rule.group else m.end(0)
            hits.append(Finding(
                rule=rule.name, kind=rule.kind, label=rule.label,
                start=start, end=end, text=value,
                context=text[max(0, start - 40):start].replace("\n", " ")[-40:],
            ))
    return _dedupe_spans(hits)


def _dedupe_spans(hits: list[Finding]) -> list[Finding]:
    """Keep the longest match per overlapping region.

    Precedence: credentials first, then user-declared terms (they are
    deliberate and more specific than a generic email/host pattern), then
    pattern-detected PII.
    """
    order = {SECRET: 0, TERM: 1, PII: 2}
    hits.sort(key=lambda f: (f.start, order[f.kind], -(f.end - f.start)))
    kept: list[Finding] = []
    for f in hits:
        if kept and f.start < kept[-1].end:
            prev = kept[-1]
            better = (order[f.kind], -(f.end - f.start)) < (order[prev.kind], -(prev.end - prev.start))
            if f.end > prev.end and better:
                kept[-1] = f
            continue
        kept.append(f)
    return kept
