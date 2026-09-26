"""Liveness and readiness probes.

/health answers "is the process alive?" and never touches dependencies, so a
database outage doesn't make the platform restart a healthy process in a loop.
/ready answers "can this instance do useful work right now?" by checking
Postgres and Redis (Redis is also the Celery broker).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text

from app.core.redis import get_redis
from app.db.session import get_engine

router = APIRouter(tags=["health"])
log = structlog.get_logger()

ReadinessCheck = Callable[[], Awaitable[None]]
CHECK_TIMEOUT_SECONDS = 2.0
# Neon's free tier suspends after 5 idle minutes. A first connection that wakes it
# took ~3 s from the Oracle VM (TLS on 1/8 OCPU included), so 2 s reported a healthy
# database as down. A cancelled connect never lands in the pool, so it never recovered.
CHECK_TIMEOUTS_SECONDS = {"database": 8.0}


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
    async def run(name: str, check: ReadinessCheck) -> str:
        timeout = CHECK_TIMEOUTS_SECONDS.get(name, CHECK_TIMEOUT_SECONDS)
        started = time.perf_counter()
        try:
            await asyncio.wait_for(check(), timeout=timeout)
        except Exception as exc:
            # Only the exception type: callers never see details, and messages could
            # quote connection parameters.
            log.warning(
                "ready.check_failed",
                check=name,
                error=type(exc).__name__,
                elapsed_s=round(time.perf_counter() - started, 2),
                timeout_s=timeout,
            )
            return "error"
        return "ok"

    outcomes = await asyncio.gather(*(run(name, check) for name, check in checks.items()))
    results = dict(zip(checks, outcomes, strict=True))
    is_ready = all(result == "ok" for result in results.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if is_ready else "unavailable", "checks": results}
