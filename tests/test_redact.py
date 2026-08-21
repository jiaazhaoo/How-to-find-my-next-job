import tempfile
import unittest
from pathlib import Path

from career import rules
from career.redact import Redactor, RedactionError, Vault


def synth(*parts: str) -> str:
    """Assemble a fake credential at runtime.

    Split into fragments on purpose: these samples are invented, but a secret
    scanner cannot tell, and a repository about redaction should be the last
    place shipping literal-looking tokens. Push protection blocks them, and it
    is right to.
    """
    return "".join(parts)


GITHUB_TOKEN = synth("ghp", "_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
AWS_KEY = synth("AKIA", "IOSFODNN7EXAMPLE")
AWS_SECRET = synth("wJalrXUtnFEMI", "/K7MDENG/", "bPxRfiCYEXAMPLEKEY")
STRIPE_KEY = synth("sk", "_live_", "51H8xQ2mLz7pWr4TtAbCdEfGh")
GOOGLE_KEY = synth("AIza", "SyA1234567890abcdefghijklmnopqrstuv")
SLACK_TOKEN = synth("xoxb", "-123456789012-", "abcdefghijklmnopqrst")


def fresh_redactor(**kw):
    vault = Vault.load(Path(tempfile.mkdtemp()) / "aliases.json")
    return Redactor(vault, **kw), vault


class TestSecretDetection(unittest.TestCase):
    def test_provider_tokens_are_removed(self):
        r, _ = fresh_redactor()
        samples = [GITHUB_TOKEN, AWS_KEY, STRIPE_KEY, GOOGLE_KEY, SLACK_TOKEN]
        for s in samples:
            with self.subTest(s=s[:12]):
                out, rep = r.redact_text(f"key = {s}")
                self.assertNotIn(s, out)
                self.assertTrue(rep.verified)

    def test_connection_string_password(self):
        r, _ = fresh_redactor()
        out, _ = r.redact_text("postgres://app:Xk9vQ2mLz7pWr4Tt@db.internal:5432/x")
        self.assertNotIn("Xk9vQ2mLz7pWr4Tt", out)
        self.assertIn("app", out, "the username is not a credential and should survive")

    def test_placeholders_are_left_alone(self):
        r, _ = fresh_redactor()
        for benign in ['password = "changeme"', 'token = os.environ["GH_TOKEN"]',
                       'api_key = settings.API_KEY', 'password: <your-password>']:
            out, rep = r.redact_text(benign)
            self.assertEqual(out, benign, benign)
            self.assertEqual(rep.secret_count, 0)

    def test_hostname_is_not_mistaken_for_a_code_reference(self):
        """`settings.API_KEY` is a reference; `db.internal` is a real host.
        The reference heuristic must not swallow the second."""
        r, _ = fresh_redactor()
        out, _ = r.redact_text("connect to db.internal:5432 using settings.API_KEY")
        self.assertNotIn("db.internal", out)
        self.assertIn("settings.API_KEY", out)

    def test_secret_plaintext_never_enters_the_vault(self):
        r, vault = fresh_redactor()
        secret = GITHUB_TOKEN
        r.redact_text(f"t = {secret}")
        blob = vault.path.read_text("utf-8")
        self.assertNotIn(secret, blob)
        self.assertNotIn(secret, "".join(vault.originals.values()))

    def test_report_carries_no_plaintext(self):
        r, _ = fresh_redactor()
        secret = STRIPE_KEY
        _, rep = r.redact_text(f"k={secret}\nmail: a@b.com")
        self.assertNotIn(secret, str(rep.to_dict()))
        self.assertNotIn("a@b.com", str(rep.to_dict()))


class TestFailClosed(unittest.TestCase):
    def test_verify_detects_a_surviving_credential(self):
        r, _ = fresh_redactor()
        leftovers = r.verify(f"AWS_SECRET_ACCESS_KEY={AWS_SECRET}")
        self.assertTrue(leftovers)

    def test_aliases_do_not_retrigger_the_gate(self):
        r, _ = fresh_redactor()
        self.assertEqual(r.verify("k=[[ENV_SECRET_REDACTED:a1b2c3]] and [[EMAIL_01]]"), [])

    def test_broken_substitution_raises_instead_of_emitting(self):
        """The whole point of the gate: if redaction silently no-ops, the
        caller gets an exception, never half-clean text."""

        class BrokenVault(Vault):
            def alias_for(self, value, label, reversible, canonical=None):
                return value  # simulate a substitution bug

        broken = Vault.load(Path(tempfile.mkdtemp()) / "aliases.json")
        broken.__class__ = BrokenVault
        r = Redactor(broken, terms=[{"term": "Acme", "label": "ORG"}])
        with self.assertRaises(RedactionError):
            r.redact_text("Acme ships on time")


class TestPseudonymisation(unittest.TestCase):
    def test_alias_is_stable_and_case_folded(self):
        r, _ = fresh_redactor(terms=[{"term": "Acme Corp", "label": "ORG"}])
        out, _ = r.redact_text("Acme Corp / acme corp / ACME  CORP")
        self.assertEqual(out.count("[[ORG_01]]"), 3)

    def test_relationships_survive(self):
        r, _ = fresh_redactor()
        out, _ = r.redact_text("a@x.com reviewed b@x.com; later a@x.com left")
        first = out.split(" ")[0]
        self.assertEqual(out.count(first), 2)

    def test_restore_round_trips_pii(self):
        r, vault = fresh_redactor(terms=[{"term": "Acme Corp", "label": "ORG"}])
        out, _ = r.redact_text("Acme Corp hired li.wei@acme.com")
        self.assertEqual(vault.restore(out), "Acme Corp hired li.wei@acme.com")

    def test_secrets_are_not_restorable(self):
        r, vault = fresh_redactor()
        out, _ = r.redact_text(f"k = {GITHUB_TOKEN}")
        self.assertEqual(vault.restore(out), out)


class TestValidators(unittest.TestCase):
    def test_cn_id_checksum_filters_random_digits(self):
        self.assertTrue(rules.cn_id_ok("11010519491231002X"))
        self.assertFalse(rules.cn_id_ok("110105194912310021"))

    def test_luhn_filters_non_cards(self):
        self.assertTrue(rules.luhn_ok("4111111111111111"))
        self.assertFalse(rules.luhn_ok("1234567890123456"))

    def test_long_id_numbers_are_not_blanket_redacted(self):
        r, _ = fresh_redactor()
        out, _ = r.redact_text("order id 1234567890123456 for build 20240101123456")
        self.assertIn("1234567890123456", out)

    def test_entropy_gate(self):
        self.assertLess(rules.shannon_entropy("aaaaaaaa"), 1.0)
        self.assertGreater(rules.shannon_entropy("Xk9vQ2mLz7pWr4Tt"), 3.0)


if __name__ == "__main__":
    unittest.main()
