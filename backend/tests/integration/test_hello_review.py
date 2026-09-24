"""The Phase 1 worker pipeline against real Postgres and a mocked GitHub API."""

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github import events
from app.github.app_auth import GitHubAppAuth
from app.github.client import GITHUB_API_URL, GitHubError
from app.repositories import pull_requests
from app.services.installations import ensure_repository
from app.services.orchestrator import CHECK_RUN_NAME, run_hello_review
from tests.helpers import InMemoryTokenCache, load_webhook

Sessions = async_sessionmaker[AsyncSession]
HEAD_SHA = "a" * 40
REPO = "/repos/octocat/playground"
PATCH = "@@ -1,3 +1,4 @@\n import os\n+import sys\n \n def main():"


@pytest.fixture
async def pull_request_id(sessionmaker: Sessions) -> int:
    event = events.PullRequestEvent.model_validate(load_webhook("pull_request_opened"))
    async with sessionmaker() as session:
        repository_id = await ensure_repository(session, event.installation.id, event.repository)
        pr = await pull_requests.upsert_from_payload(
            session, repository_id=repository_id, payload=event.pull_request
        )
        await session.commit()
        return pr.id


@pytest.fixture
async def github_auth(private_key_pem: str) -> AsyncIterator[GitHubAppAuth]:
    async with httpx.AsyncClient(base_url=GITHUB_API_URL) as http:
        yield GitHubAppAuth(
            issuer="Iv23liTestClientId",
            private_key_pem=private_key_pem,
            http=http,
            cache=InMemoryTokenCache(),
        )


@pytest.fixture
def github() -> Iterator[respx.MockRouter]:
    """A fake GitHub that answers every call the hello review makes."""
    router = respx.mock(base_url=GITHUB_API_URL, assert_all_called=False)
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    router.post("/app/installations/4001/access_tokens", name="token").respond(
        201, json={"token": "ghs_test", "expires_at": expires}
    )
    router.post(f"{REPO}/check-runs", name="create_check").respond(201, json={"id": 77})
    router.patch(f"{REPO}/check-runs/77", name="complete_check").respond(200, json={"id": 77})
    router.get(f"{REPO}/pulls/1/files", name="files").respond(
        200,
        json=[
            {"filename": "logo.png", "status": "added", "additions": 0, "deletions": 0},
            {
                "filename": "app/main.py",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "patch": PATCH,
            },
        ],
    )
    router.post(f"{REPO}/pulls/1/reviews", name="review").respond(200, json={"id": 555})
    with router:  # active for the whole test; recorded calls stay inspectable
        yield router


async def test_posts_comment_on_first_added_line_and_completes_check(
    sessionmaker: Sessions,
    github_auth: GitHubAppAuth,
    github: respx.MockRouter,
    pull_request_id: int,
) -> None:
    result = await run_hello_review(
        sessionmaker, github_auth, pull_request_id=pull_request_id, head_sha=HEAD_SHA
    )

    assert (result.status, result.review_id, result.check_run_id) == ("posted", 555, 77)

    check = json.loads(github["create_check"].calls[0].request.content)
    assert check == {
        "name": CHECK_RUN_NAME,
        "head_sha": HEAD_SHA,
        "status": "in_progress",
        "output": {"title": "Reviewing…", "summary": "ReviewPilot is reviewing this PR."},
    }

    review = json.loads(github["review"].calls[0].request.content)
    assert review["event"] == "COMMENT"
    assert review["commit_id"] == HEAD_SHA
    [comment] = review["comments"]
    assert (comment["path"], comment["line"], comment["side"]) == ("app/main.py", 2, "RIGHT")

    completed = json.loads(github["complete_check"].calls[0].request.content)
    assert (completed["status"], completed["conclusion"]) == ("completed", "success")


async def test_stale_head_is_superseded_without_calling_github(
    sessionmaker: Sessions,
    github_auth: GitHubAppAuth,
    github: respx.MockRouter,
    pull_request_id: int,
) -> None:
    result = await run_hello_review(
        sessionmaker, github_auth, pull_request_id=pull_request_id, head_sha="f" * 40
    )
    assert result.status == "superseded"
    assert not github.calls


async def test_github_failure_completes_check_as_neutral_then_raises(
    sessionmaker: Sessions,
    github_auth: GitHubAppAuth,
    github: respx.MockRouter,
    pull_request_id: int,
) -> None:
    github["review"].respond(422, json={"message": "Unprocessable Entity"})

    with pytest.raises(GitHubError):
        await run_hello_review(
            sessionmaker, github_auth, pull_request_id=pull_request_id, head_sha=HEAD_SHA
        )

    completed = json.loads(github["complete_check"].calls[0].request.content)
    # Our failure must never block the author's merge.
    assert completed["conclusion"] == "neutral"
