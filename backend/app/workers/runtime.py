"""What background jobs need, built once when the API starts (ADR 0016).

The worker runs inside the API process, on the API's event loop, so jobs share the
API's database pool. GitHub and the LLM get their own HTTP clients: GitHub's default
headers must not be sent to the LLM provider.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.github.app_auth import GitHubAppAuth, MemoryTokenCache
from app.github.client import create_http_client
from app.review.llm.factory import build_provider, price_table, review_budget, review_model
from app.services.commands import CommandDeps
from app.services.orchestrator import ReviewDeps

LLM_HTTP_TIMEOUT_SECONDS = 150


@dataclass(frozen=True)
class WorkerContext:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    github_auth: GitHubAppAuth
    llm_http: httpx.AsyncClient

    def review_deps(self) -> ReviewDeps:
        # Built per job: a missing LLM setting fails that job, not the API's startup.
        return ReviewDeps(
            sessionmaker=self.sessionmaker,
            github_auth=self.github_auth,
            llm=build_provider(self.settings, self.llm_http),
            model=review_model(self.settings),
            summary_model=self.settings.llm_summary_model or None,
            budget=review_budget(self.settings),
            prices=price_table(self.settings),
        )

    def command_deps(self, wake_worker: Callable[[], None]) -> CommandDeps:
        return CommandDeps(
            sessionmaker=self.sessionmaker,
            github_auth=self.github_auth,
            wake_worker=wake_worker,
            docs_url=self.settings.docs_url,
        )


@asynccontextmanager
async def worker_context() -> AsyncIterator[WorkerContext]:
    settings = get_settings()
    github_http = create_http_client()
    llm_http = httpx.AsyncClient(timeout=LLM_HTTP_TIMEOUT_SECONDS)
    try:
        yield WorkerContext(
            settings=settings,
            sessionmaker=get_sessionmaker(),
            github_auth=GitHubAppAuth(
                issuer=settings.github_app_jwt_issuer,
                private_key_pem=settings.github_app_private_key_pem,
                http=github_http,
                cache=MemoryTokenCache(),
            ),
            llm_http=llm_http,
        )
    finally:
        await llm_http.aclose()
        await github_http.aclose()
