"""Fixtures for integration tests that run the review pipeline."""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from app.github import events
from app.github.app_auth import GitHubAppAuth
from app.github.client import GITHUB_API_URL
from app.repositories import pull_requests, review_runs
from app.services.installations import ensure_repository
from tests.helpers import InMemoryTokenCache, load_webhook
from tests.integration.review_fakes import BASE, HEAD, FakeGitHub, Sessions


@pytest.fixture
async def run_id(sessionmaker: Sessions) -> int:
    event = events.PullRequestEvent.model_validate(load_webhook("pull_request_opened"))
    async with sessionmaker() as session:
        repository_id = await ensure_repository(session, event.installation.id, event.repository)
        pr = await pull_requests.upsert_from_payload(
            session, repository_id=repository_id, payload=event.pull_request
        )
        run = await review_runs.create_queued(
            session,
            pull_request_id=pr.id,
            trigger="opened",
            mode="full",
            base_sha=BASE,
            head_sha=HEAD,
        )
        await session.commit()
        return run.id


@pytest.fixture
async def github_auth(private_key_pem: str) -> AsyncIterator[GitHubAppAuth]:
    async with httpx.AsyncClient(base_url=GITHUB_API_URL) as http:
        yield GitHubAppAuth(
            issuer="Iv23liTest",
            private_key_pem=private_key_pem,
            http=http,
            cache=InMemoryTokenCache(),
        )


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    with respx.mock(base_url=GITHUB_API_URL, assert_all_called=False) as router:
        yield FakeGitHub(router)
