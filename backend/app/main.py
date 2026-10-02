"""FastAPI application entry point: `uvicorn app.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, new_context, request_id_from
from app.core.observability import init_sentry
from app.db.session import get_engine
from app.github.client import create_http_client
from app.workers.runtime import worker_context
from app.workers.worker import Worker

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("api.startup", app_env=get_settings().app_env)
    # The background worker lives in this process, on this event loop (ADR 0016).
    async with worker_context() as ctx:
        app.state.worker = worker = Worker(ctx)
        worker.start()
        try:
            yield
        finally:
            await worker.stop()
    # Close pooled connections cleanly so shutdown doesn't leave sockets open.
    await app.state.github_web_http.aclose()
    await app.state.github_api_http.aclose()
    await get_engine().dispose()
    log.info("api.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_env)
    init_sentry(settings, component="api")
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

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Each request is one unit of work for logs and Sentry; routes add to it.
        request_id = request_id_from(request.headers.get("x-request-id"))
        new_context(request_id=request_id, path=request.url.path)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    return app


app = create_app()
