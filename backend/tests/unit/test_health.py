import httpx
from fastapi import FastAPI

from app.api.v1.routes.health import ReadinessCheck, get_readiness_checks


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("redis down")


def _override(app: FastAPI, checks: dict[str, ReadinessCheck]) -> None:
    app.dependency_overrides[get_readiness_checks] = lambda: checks


async def test_health_is_always_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_all_dependencies_ok(app: FastAPI, client: httpx.AsyncClient) -> None:
    _override(app, {"database": _ok, "redis": _ok})
    response = await client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_not_ready_when_a_dependency_fails(app: FastAPI, client: httpx.AsyncClient) -> None:
    _override(app, {"database": _ok, "redis": _fail})
    response = await client.get("/api/v1/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"] == {"database": "ok", "redis": "error"}
    # Error details are not leaked to unauthenticated callers.
    assert "redis down" not in response.text
