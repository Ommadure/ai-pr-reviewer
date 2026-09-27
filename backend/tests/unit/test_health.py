import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.routes import health
from app.api.v1.routes.health import ReadinessCheck, get_readiness_checks


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("database down")


def _override(app: FastAPI, checks: dict[str, ReadinessCheck]) -> None:
    app.dependency_overrides[get_readiness_checks] = lambda: checks


async def test_health_is_always_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_all_dependencies_ok(app: FastAPI, client: httpx.AsyncClient) -> None:
    _override(app, {"database": _ok})
    response = await client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


async def test_not_ready_when_a_dependency_fails(app: FastAPI, client: httpx.AsyncClient) -> None:
    _override(app, {"database": _fail})
    response = await client.get("/api/v1/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"] == {"database": "error"}
    # Error details are not leaked to unauthenticated callers.
    assert "database down" not in response.text


async def test_each_check_has_its_own_timeout_and_failures_are_logged(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow first connection (a waking Neon database) isn't reported as down."""

    async def _slow() -> None:
        await asyncio.sleep(0.05)

    class _Recorder:
        def __init__(self) -> None:
            self.warnings: list[tuple[str, dict[str, Any]]] = []

        def warning(self, event: str, **fields: Any) -> None:
            self.warnings.append((event, fields))

    recorder = _Recorder()
    monkeypatch.setattr(health, "log", recorder)
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(health, "CHECK_TIMEOUTS_SECONDS", {"database": 1.0})
    _override(app, {"database": _slow, "other": _slow})

    response = await client.get("/api/v1/ready")

    assert response.json()["checks"] == {"database": "ok", "other": "error"}
    assert [(event, f["check"], f["error"]) for event, f in recorder.warnings] == [
        ("ready.check_failed", "other", "TimeoutError")
    ]


def test_the_database_gets_time_to_wake_up() -> None:
    assert health.CHECK_TIMEOUTS_SECONDS["database"] >= 5
