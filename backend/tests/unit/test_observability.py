"""Log context per unit of work and Sentry scrubbing."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import sentry_sdk
import structlog

from app.core import logging as app_logging
from app.core.config import Settings
from app.core.logging import bind_context, configure_logging, new_context, request_id_from
from app.core.observability import init_sentry, scrub_event


@pytest.fixture(autouse=True)
def _clean_context() -> Iterator[None]:
    new_context()
    yield
    new_context()


def test_request_ids_are_reused_only_when_safe() -> None:
    assert request_id_from("abc-123.def_4") == "abc-123.def_4"
    for hostile in (None, "", "a" * 65, "x\ninjected=1", "<script>"):
        generated = request_id_from(hostile)
        assert generated != hostile and len(generated) == 16


def test_context_reaches_logs_from_any_module(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("production")  # JSON lines
    new_context(request_id="r1")
    bind_context(delivery_id="d1", repo="octo/app", pr=7, skipped=None)
    # A logger that knows nothing about the request, like the GitHub client's.
    structlog.get_logger("app.github.client").info("github.retry")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line | {"request_id": "r1", "delivery_id": "d1", "repo": "octo/app", "pr": 7} == line
    assert "skipped" not in line


async def test_api_responses_carry_a_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "trace-me-1"})
    assert response.headers["X-Request-ID"] == "trace-me-1"
    response = await client.get("/api/v1/health")
    assert len(response.headers["X-Request-ID"]) == 16


def test_sentry_is_off_without_a_dsn(settings: Settings) -> None:
    assert init_sentry(settings.model_copy(update={"sentry_dsn": ""}), component="api") is False


def test_sentry_events_are_scrubbed_and_tagged() -> None:
    new_context(request_id="r9")
    bind_context(delivery_id="d9", run_id=3, repo="octo/app", pr=12)
    event: dict[str, Any] = {
        "request": {
            "url": "https://api.example/api/v1/auth/github/callback",
            "query_string": "code=secret-code&state=s",
            "data": '{"diff": "private source"}',
            "cookies": {"rp_session": "jwt"},
            "headers": {
                "Authorization": "Bearer t",
                "Cookie": "rp_session=jwt",
                "X-Hub-Signature-256": "sha256=...",
                "X-GitHub-Event": "pull_request",
            },
        }
    }
    scrubbed = scrub_event(event, {})  # type: ignore[arg-type]
    assert scrubbed is not None
    request = scrubbed["request"]
    assert set(request) == {"url", "headers"}
    assert request["headers"] == {"X-GitHub-Event": "pull_request"}
    assert scrubbed["tags"] == {
        "request_id": "r9",
        "delivery_id": "d9",
        "run_id": "3",
        "repo": "octo/app",
        "pr": "12",
    }


def test_logged_errors_reach_sentry_only_when_it_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", captured.append)
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda msg, level: captured.append(msg))

    class Client:
        active = False

        def is_active(self) -> bool:
            return self.active

    fake = Client()
    monkeypatch.setattr(sentry_sdk, "get_client", lambda: fake)
    error = RuntimeError("enqueue failed")

    app_logging.report_logged_errors(None, "exception", {"event": "e", "exc_info": error})
    assert captured == []  # Sentry not configured: nothing sent

    fake.active = True
    app_logging.report_logged_errors(None, "exception", {"event": "e", "exc_info": error})
    app_logging.report_logged_errors(None, "error", {"event": "queue down"})
    app_logging.report_logged_errors(None, "info", {"event": "fine"})
    assert captured == [error, "queue down"]
