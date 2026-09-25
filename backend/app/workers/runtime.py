"""Per-task resources for Celery tasks that run async code.

Celery tasks are synchronous functions, so each task runs its async body with
`asyncio.run(...)`, which creates a fresh event loop every time (ADR 0004).
Async connection pools are bound to the loop that created them, so each task
builds its own engine, Redis client and HTTP clients, and closes them before
the loop ends.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.rate_limit import RedisRateLimiter
from app.github.app_auth import GitHubAppAuth, RedisTokenCache
from app.github.client import create_http_client
from app.review.llm.factory import build_provider, price_table, review_budget, review_model
from app.services.commands import CommandDeps
from app.services.orchestrator import ReviewDeps

# Longer than the task's hard time limit (300 s), so a lock can't expire under a live
# review; short enough that a hard-killed worker doesn't block the PR for long.
PR_LOCK_TIMEOUT_SECONDS = 360
LLM_HTTP_TIMEOUT_SECONDS = 150


class RedisPRLock:
    """One review per PR at a time, across every worker process."""

    def __init__(self, redis: Redis, timeout: int = PR_LOCK_TIMEOUT_SECONDS) -> None:
        self._redis = redis
        self._timeout = timeout

    @asynccontextmanager
    async def hold(self, pull_request_id: int) -> AsyncIterator[bool]:
        lock = self._redis.lock(f"lock:pr:{pull_request_id}", timeout=self._timeout)
        acquired = bool(await lock.acquire(blocking=False))
        try:
            yield acquired
        finally:
            if acquired:
                with suppress(LockError):  # already expired: nothing to release
                    await lock.release()


@dataclass(frozen=True)
class WorkerContext:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    github_auth: GitHubAppAuth
    llm_http: httpx.AsyncClient

    def review_deps(self) -> ReviewDeps:
        return ReviewDeps(
            sessionmaker=self.sessionmaker,
            github_auth=self.github_auth,
            llm=build_provider(self.settings, self.llm_http),
            model=review_model(self.settings),
            summary_model=self.settings.llm_summary_model or None,
            lock=RedisPRLock(self.redis),
            budget=review_budget(self.settings),
            prices=price_table(self.settings),
        )

    def command_deps(self, enqueue_review: Callable[[int], None]) -> CommandDeps:
        return CommandDeps(
            sessionmaker=self.sessionmaker,
            github_auth=self.github_auth,
            rate_limiter=RedisRateLimiter(self.redis),
            enqueue_review=enqueue_review,
            docs_url=self.settings.docs_url,
        )


@asynccontextmanager
async def worker_context() -> AsyncIterator[WorkerContext]:
    settings = get_settings()
    # NullPool: open a connection when needed and close it after. Pooling across
    # tasks is impossible anyway because each task has its own event loop.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    github_http = create_http_client()
    # Separate client: GitHub's default headers must not be sent to the LLM provider.
    llm_http = httpx.AsyncClient(timeout=LLM_HTTP_TIMEOUT_SECONDS)
    try:
        auth = GitHubAppAuth(
            issuer=settings.github_app_jwt_issuer,
            private_key_pem=settings.github_app_private_key_pem,
            http=github_http,
            cache=RedisTokenCache(redis),
        )
        yield WorkerContext(
            settings=settings,
            sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
            redis=redis,
            github_auth=auth,
            llm_http=llm_http,
        )
    finally:
        await llm_http.aclose()
        await github_http.aclose()
        await redis.aclose()
        await engine.dispose()
