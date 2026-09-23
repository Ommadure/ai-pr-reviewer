"""Liveness and readiness probes.

/health answers "is the process alive?" and never touches dependencies, so a
database outage doesn't make the platform restart a healthy process in a loop.
/ready answers "can this instance do useful work right now?" by checking
Postgres and Redis (Redis is also the Celery broker).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text

from app.core.redis import get_redis
from app.db.session import get_engine

router = APIRouter(tags=["health"])

ReadinessCheck = Callable[[], Awaitable[None]]
CHECK_TIMEOUT_SECONDS = 2.0


async def check_database() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_redis() -> None:
    await get_redis().ping()


def get_readiness_checks() -> dict[str, ReadinessCheck]:
    """Dependency so tests can swap in fakes instead of real Postgres/Redis."""
    return {"database": check_database, "redis": check_redis}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    response: Response,
    checks: Annotated[dict[str, ReadinessCheck], Depends(get_readiness_checks)],
) -> dict[str, object]:
    async def run(check: ReadinessCheck) -> str:
        try:
            await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        except Exception:
            return "error"
        return "ok"

    outcomes = await asyncio.gather(*(run(check) for check in checks.values()))
    results = dict(zip(checks, outcomes, strict=True))
    is_ready = all(result == "ok" for result in results.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if is_ready else "unavailable", "checks": results}
