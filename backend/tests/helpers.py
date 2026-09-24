"""Test doubles and small helpers shared across tests."""

import copy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.github.signatures import compute_signature
from app.services.webhook_router import ReviewJob

FIXTURES = Path(__file__).parent / "fixtures"
WEBHOOK_SECRET = "test-webhook-secret"
BOT_LOGIN = "reviewpilot-test[bot]"


def load_webhook(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / "webhooks" / f"{name}.json").read_text())
    return copy.deepcopy(payload)


async def send_webhook(
    client: httpx.AsyncClient,
    event: str,
    payload: dict[str, Any],
    *,
    delivery_id: str | None = None,
    signature: str | None = None,
) -> httpx.Response:
    """POST a webhook exactly like GitHub does: raw bytes + HMAC header.

    signature=None signs correctly; pass "" to omit the header entirely.
    """
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id or str(uuid4()),
    }
    signature = compute_signature(WEBHOOK_SECRET, body) if signature is None else signature
    if signature:
        headers["X-Hub-Signature-256"] = signature
    return await client.post("/api/v1/webhooks/github", content=body, headers=headers)


class RecordingDispatcher:
    """Stands in for Celery: remembers what would have been enqueued."""

    def __init__(self) -> None:
        self.jobs: list[ReviewJob] = []

    def enqueue_review(self, job: ReviewJob) -> None:
        self.jobs.append(job)


class InMemoryTokenCache:
    """Stands in for Redis in token-cache tests; records TTLs for assertions."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.values[key] = value
        self.ttls[key] = ttl_seconds

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
