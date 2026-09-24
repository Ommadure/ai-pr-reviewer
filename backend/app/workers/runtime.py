"""Per-task resources for Celery tasks that run async code.

Celery tasks are synchronous functions, so each task runs its async body with
`asyncio.run(...)`, which creates a fresh event loop every time (see
docs/adr/0004). Async connection pools are bound to the loop that created
them, so we can't share the API's cached engine or Redis client here: each
task builds its own and closes it before the loop ends.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.github.app_auth import GitHubAppAuth, RedisTokenCache
from app.github.client import create_http_client


@dataclass(frozen=True)
class WorkerContext:
    sessionmaker: async_sessionmaker[AsyncSession]
    github_auth: GitHubAppAuth


@asynccontextmanager
async def worker_context() -> AsyncIterator[WorkerContext]:
    settings = get_settings()
    # NullPool: open a connection when needed and close it after. Pooling across
    # tasks is impossible anyway because each task has its own event loop.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    http = create_http_client()
    try:
        auth = GitHubAppAuth(
            issuer=settings.github_app_jwt_issuer,
            private_key_pem=settings.github_app_private_key_pem,
            http=http,
            cache=RedisTokenCache(redis),
        )
        yield WorkerContext(
            sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
            github_auth=auth,
        )
    finally:
        await http.aclose()
        await redis.aclose()
        await engine.dispose()
