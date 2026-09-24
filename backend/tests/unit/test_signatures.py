import hashlib
import hmac

import pytest

from app.github.signatures import compute_signature, is_valid_signature

SECRET = "It's a Secret to Everybody"
BODY = b"Hello, World!"


def test_matches_githubs_documented_example() -> None:
    # Test vector from GitHub's "Validating webhook deliveries" docs.
    expected = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert compute_signature(SECRET, BODY) == expected


def test_valid_signature_accepted() -> None:
    assert is_valid_signature(SECRET, BODY, compute_signature(SECRET, BODY))


@pytest.mark.parametrize(
    "header",
    [
        None,  # missing
        "",  # empty
        "sha256=" + "0" * 64,  # wrong digest
        compute_signature("wrong-secret", BODY),
        "sha1=" + hmac.new(SECRET.encode(), BODY, hashlib.sha1).hexdigest(),  # legacy algorithm
        compute_signature(SECRET, BODY).removeprefix("sha256="),  # no prefix
    ],
)
def test_invalid_signatures_rejected(header: str | None) -> None:
    assert not is_valid_signature(SECRET, BODY, header)


def test_tampered_body_rejected() -> None:
    signature = compute_signature(SECRET, BODY)
    assert not is_valid_signature(SECRET, BODY + b" ", signature)


def test_empty_secret_never_validates() -> None:
    # Otherwise anyone could compute a "valid" signature with an empty key.
    assert not is_valid_signature("", BODY, compute_signature("", BODY))
