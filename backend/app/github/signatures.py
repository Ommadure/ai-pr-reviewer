"""Webhook signature verification.

GitHub signs the raw request body with HMAC-SHA256 using the webhook secret and
sends `X-Hub-Signature-256: sha256=<hex>`. Recomputing it proves the request came
from GitHub and wasn't modified. It must be computed over the exact bytes
received: re-serialising parsed JSON would change whitespace and break it.
"""

import hashlib
import hmac

SIGNATURE_PREFIX = "sha256="


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def is_valid_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret or not signature_header or not signature_header.startswith(SIGNATURE_PREFIX):
        return False
    # compare_digest takes the same time whether the first or last character
    # differs, so an attacker can't guess the signature byte by byte from timings.
    return hmac.compare_digest(compute_signature(secret, body), signature_header)
