# ruff: noqa: E402  (env vars must be set before app modules are imported)
import os

# Must be set before any app module reads settings. Tests never need real
# credentials and never talk to GitHub or an LLM.
os.environ["APP_ENV"] = "test"
# Integration tests use a separate database so they can't touch dev data.
# Default = the docker compose Postgres (host port 5433); CI overrides it.
TEST_DATABASE_URL = os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://reviewpilot:reviewpilot@localhost:5433/reviewpilot_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy import make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.services.dispatch import get_review_dispatcher
from tests.helpers import WEBHOOK_SECRET, RecordingDispatcher

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        github_webhook_secret=WEBHOOK_SECRET,
        github_app_slug="reviewpilot-test",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # ASGITransport calls the app in-process: no server, no network.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


# ---- database (integration tests only) ----


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Create the test database once and run the real Alembic migrations on it.

    Going down to "base" and back up also proves every downgrade works.
    """
    url = make_url(TEST_DATABASE_URL)
    try:
        asyncio.run(_ensure_database_exists(url))
    except OSError as exc:
        pytest.fail(
            f"Integration tests need Postgres at {url.render_as_string(hide_password=True)}. "
            f"Start it with `docker compose up -d postgres`. ({exc})"
        )
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


async def _ensure_database_exists(url: URL) -> None:
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    await admin.dispose()


@pytest.fixture
async def sessionmaker(migrated_database: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # NullPool: each test has its own event loop, and pooled asyncpg connections
    # can't move between loops.
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def dispatcher() -> RecordingDispatcher:
    return RecordingDispatcher()


@pytest.fixture
async def api(
    app: FastAPI,
    sessionmaker: async_sessionmaker[AsyncSession],
    dispatcher: RecordingDispatcher,
) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client for the app wired to the test database and a recording dispatcher."""

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_review_dispatcher] = lambda: dispatcher
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


# ---- GitHub App key ----


@pytest.fixture(scope="session")
def rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def private_key_pem(rsa_private_key: rsa.RSAPrivateKey) -> str:
    return rsa_private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
