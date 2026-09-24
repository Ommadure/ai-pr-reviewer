from typing import Any

import httpx
import pytest
import respx

from app.core.config import Settings
from app.github.check_setup import check
from app.github.client import GITHUB_API_URL, GitHubClient

GOOD_APP: dict[str, Any] = {
    "id": 123,
    "slug": "reviewpilot-test",
    "name": "ReviewPilot Test",
    "permissions": {
        "pull_requests": "write",
        "contents": "read",
        "checks": "write",
        "issues": "write",
        "metadata": "read",
    },
    "events": ["pull_request", "issue_comment"],
}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        github_app_slug="reviewpilot-test",
        github_webhook_secret="x" * 64,
    )


@pytest.fixture
def client() -> GitHubClient:
    async def token() -> str:
        return "app-jwt"

    return GitHubClient(httpx.AsyncClient(base_url=GITHUB_API_URL), token)


@respx.mock(base_url=GITHUB_API_URL)
async def test_good_setup_passes(
    respx_mock: respx.MockRouter, settings: Settings, client: GitHubClient
) -> None:
    respx_mock.get("/app").respond(200, json=GOOD_APP)
    respx_mock.get("/app/installations").respond(
        200, json=[{"id": 4001, "account": {"login": "octocat"}}]
    )
    report = await check(settings, client)
    assert not report.failed, report.lines
    assert any("installed on: octocat (installation 4001)" in line for line in report.lines)


@respx.mock(base_url=GITHUB_API_URL)
async def test_reports_every_problem(
    respx_mock: respx.MockRouter, settings: Settings, client: GitHubClient
) -> None:
    bad_app = GOOD_APP | {
        "slug": "reviewpilot-other",
        "permissions": {"pull_requests": "read", "metadata": "read", "administration": "write"},
        "events": ["pull_request"],
    }
    respx_mock.get("/app").respond(200, json=bad_app)
    respx_mock.get("/app/installations").respond(200, json=[])

    report = await check(settings, client)

    text = "\n".join(report.lines)
    assert report.failed
    assert "App's slug is 'reviewpilot-other'" in text
    assert "permission pull_requests is 'read', needs 'write'" in text
    assert "permission checks is 'none'" in text
    assert "extra permissions not needed (least privilege): administration" in text
    assert "not subscribed to events: issue_comment" in text
    assert "isn't installed anywhere" in text


@respx.mock(base_url=GITHUB_API_URL)
async def test_bad_credentials(
    respx_mock: respx.MockRouter, settings: Settings, client: GitHubClient
) -> None:
    respx_mock.get("/app").respond(401, json={"message": "A JSON web token could not be decoded"})
    report = await check(settings, client)
    assert report.failed
    assert "GitHub rejected the App credentials (401" in "\n".join(report.lines)
