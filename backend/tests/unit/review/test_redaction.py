"""Secrets are assembled at runtime so this file itself never contains a
secret-shaped string (GitHub push protection and scanners would flag it)."""

import pytest

from app.review.diff_parser import parse_patch
from app.review.redaction import REDACTED, redact_files, redact_line

AWS = "AKIA" + "IOSFODNN7EXAMPLE"  # AWS's documented example key id
GITHUB = "ghp_" + "a1B2" * 9
GOOGLE = "AIza" + "Sy" + "X" * 33
SLACK = "xoxb-" + "1234567890-abcdefghij"
STRIPE = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "a" * 20


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (f'key = "{AWS}"', "AWS access key ID"),
        (f"token: {GITHUB}", "GitHub token"),
        (f"maps_key = '{GOOGLE}'", "Google API key"),
        (f"SLACK={SLACK}", "Slack token"),
        (f"stripe.api_key = '{STRIPE}'", "Stripe live key"),
        (f"Authorization: Bearer {JWT}", "JSON Web Token"),
        ('DB_PASSWORD = "hunter2-but-longer!"', "hardcoded credential"),
        ('"api_key": "q8Zr2mW9xK4vN7bT"', "hardcoded credential"),
    ],
)
def test_detects_and_redacts(line: str, kind: str) -> None:
    redacted, kinds = redact_line(line)
    assert kind in kinds
    assert REDACTED in redacted
    for secret in (
        AWS,
        GITHUB,
        GOOGLE,
        SLACK,
        STRIPE,
        JWT,
        "hunter2-but-longer!",
        "q8Zr2mW9xK4vN7bT",
    ):
        assert secret not in redacted


@pytest.mark.parametrize(
    "line",
    [
        'password = os.environ["DB_PASSWORD"]',  # read from env: fine
        'api_key = "your_api_key_here"',  # placeholder
        'SECRET = "${SECRET_FROM_VAULT}"',  # template
        'token = "short"',  # too short to be a credential
        "tokenizer = build_tokenizer()",
    ],
)
def test_ignores_non_secrets(line: str) -> None:
    assert redact_line(line) == (line, [])


def test_redacts_all_lines_but_only_flags_added_ones() -> None:
    patch = f'@@ -1,2 +1,2 @@\n OLD_KEY = "{AWS}"\n-x = "{GITHUB}"\n+y = "{GOOGLE}"'
    [file], findings = redact_files([parse_patch("settings.py", patch)])

    assert all(REDACTED in line.content for line in file.hunks[0].lines)
    # Context and removed secrets are hidden from the LLM but aren't this PR's fault.
    assert [(f.line, f.kind) for f in findings] == [(2, "Google API key")]


def test_private_key_block_is_redacted_whole() -> None:
    begin, end = "-----BEGIN RSA " + "PRIVATE KEY-----", "-----END RSA " + "PRIVATE KEY-----"
    patch = f"@@ -0,0 +1,4 @@\n+{begin}\n+MIIEowIBAAKCAQEA\n+abcdef\n+{end}"
    [file], findings = redact_files([parse_patch("key.pem", patch, status="added")])

    assert [line.content for line in file.hunks[0].lines] == [REDACTED] * 4
    assert [(f.line, f.kind) for f in findings] == [(1, "private key")]


def test_one_finding_per_kind_per_line() -> None:
    _, findings = redact_files([parse_patch("a.py", f'@@ -0,0 +1 @@\n+k = "{AWS}"; j = "{AWS}"')])
    assert len(findings) == 1
