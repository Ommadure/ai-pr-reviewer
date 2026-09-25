"""FastAPI application entry point: `uvicorn app.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import get_redis
from app.db.session import get_engine
from app.github.client import create_http_client

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("api.startup", app_env=get_settings().app_env)
    yield
    # Close pooled connections cleanly so shutdown doesn't leave sockets open.
    await app.state.github_web_http.aclose()
    await app.state.github_api_http.aclose()
    await get_engine().dispose()
    await get_redis().aclose()
    log.info("api.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_env)
    app = FastAPI(
        title="ReviewPilot",
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs only outside production.
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
    )
    # Shared HTTP clients for dashboard login (github.com) and user-token API calls.
    app.state.github_web_http = httpx.AsyncClient(timeout=15)
    app.state.github_api_http = create_http_client()
    app.include_router(api_router)
    return app


app = create_app()
