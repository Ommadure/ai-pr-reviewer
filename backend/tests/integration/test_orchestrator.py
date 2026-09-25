"""The full review pipeline: real Postgres, mocked GitHub (respx), scripted LLM."""

import base64
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github import events
from app.github.app_auth import GitHubAppAuth
from app.github.client import GITHUB_API_URL
from app.models import LLMCall, PullRequest, RepoConfigRecord, ReviewCommentRecord, ReviewRun
from app.repositories import pull_requests, review_runs
from app.review.llm.base import Completion, LLMError, Message
from app.review.llm.fake import FakeLLMProvider
from app.services.installations import ensure_repository
from app.services.orchestrator import (
    RetryableReviewError,
    ReviewBusy,
    ReviewDeps,
    execute_review_run,
)
from tests.helpers import InMemoryTokenCache, load_webhook

Sessions = async_sessionmaker[AsyncSession]
REPO = "/repos/octocat/playground"
HEAD = "a" * 40
BASE = "b" * 40
USERS_PATCH = (
    "@@ -10,5 +11,6 @@ def get_user(user_id):\n"
    "     db = get_db()\n"
    '-    query = "SELECT * FROM users WHERE id = ?"\n'
    "-    return db.execute(query, (user_id,)).fetchone()\n"
    '+    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
    "+    row = db.execute(query).fetchone()\n"
    "+    return row\n"
    " \n"
    " \n"
)
SUMMARY = json.dumps(
    {"overview": "Changes the user lookup.", "risk_level": "high", "key_changes": [], "notes": []}
)


def _llm_comment(line: int, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "path": "app/users.py",
        "line": line,
        "severity": "critical",
        "category": "security",
        "title": title,
        "body": f"{title}: explanation.",
        "confidence": 0.95,
        **extra,
    }


def review_responder(comments: list[dict[str, Any]]) -> FakeLLMProvider:
    return FakeLLMProvider(responder=respond_with(comments))


def respond_with(comments: list[dict[str, Any]]) -> Callable[[list[Message]], str]:
    def respond(messages: list[Message]) -> str:
        if "<review_notes>" in messages[-1].content:
            return SUMMARY
        return json.dumps(
            {
                "comments": comments,
                "file_summaries": [{"path": "app/users.py", "summary": "Builds SQL by hand."}],
            }
        )

    return respond


class InMemoryPRLock:
    def __init__(self) -> None:
        self.held: set[int] = set()

    @asynccontextmanager
    async def hold(self, pull_request_id: int) -> AsyncIterator[bool]:
        if pull_request_id in self.held:
            yield False
            return
        self.held.add(pull_request_id)
        try:
            yield True
        finally:
            self.held.discard(pull_request_id)


# ---- fixtures ----


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


class FakeGitHub:
    """respx routes for every call the orchestrator makes, with sensible defaults."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.next_comment_id = 9000
        self.config_yaml: str | None = None
        self.pr = {
            "number": 1,
            "title": "Add user lookup",
            "body": "Looks users up by id.",
            "state": "open",
            "draft": False,
            "head": {"sha": HEAD, "ref": "feature"},
            "base": {"sha": BASE, "ref": "main"},
            "user": {"login": "octocat"},
        }
        expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        router.post("/app/installations/4001/access_tokens").respond(
            201, json={"token": "ghs_test", "expires_at": expires}
        )
        router.get(f"{REPO}/branches/main", name="branch").respond(
            200, json={"commit": {"sha": "d" * 40}}
        )
        router.get(f"{REPO}/contents/.reviewpilot.yml", name="config").mock(
            side_effect=self._config
        )
        router.get(f"{REPO}/pulls/1", name="pr").mock(
            side_effect=lambda _: httpx.Response(200, json=self.pr)
        )
        router.get(f"{REPO}/pulls/1/files", name="files").respond(
            200,
            json=[
                {
                    "filename": "app/users.py",
                    "status": "modified",
                    "additions": 3,
                    "deletions": 2,
                    "patch": USERS_PATCH,
                },
                {
                    "filename": "package-lock.json",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 1,
                    "patch": "@@ -1 +1 @@\n-a\n+b",
                },
            ],
        )
        router.post(f"{REPO}/check-runs", name="create_check").respond(201, json={"id": 77})
        router.patch(f"{REPO}/check-runs/77", name="complete_check").respond(200, json={})
        router.post(f"{REPO}/pulls/1/reviews", name="review").mock(side_effect=self._create_review)
        router.get(f"{REPO}/pulls/1/reviews/555/comments", name="review_comments").mock(
            side_effect=self._review_comments
        )
        router.post(f"{REPO}/pulls/1/comments", name="single_comment").mock(
            side_effect=self._single_comment
        )
        self.last_review: dict[str, Any] = {}

    def _config(self, _: httpx.Request) -> httpx.Response:
        if self.config_yaml is None:
            return httpx.Response(404, json={"message": "Not Found"})
        raw = self.config_yaml.encode()
        content = base64.encodebytes(raw).decode()  # GitHub wraps base64 in newlines
        return httpx.Response(
            200, json={"type": "file", "encoding": "base64", "size": len(raw), "content": content}
        )

    def _create_review(self, request: httpx.Request) -> httpx.Response:
        self.last_review = json.loads(request.content)
        return httpx.Response(200, json={"id": 555})

    def _review_comments(self, _: httpx.Request) -> httpx.Response:
        posted = []
        for c in self.last_review.get("comments", []):
            self.next_comment_id += 1
            posted.append(
                {
                    "id": self.next_comment_id,
                    "path": c["path"],
                    "line": c["line"],
                    "body": c["body"],
                }
            )
        return httpx.Response(200, json=posted)

    def _single_comment(self, request: httpx.Request) -> httpx.Response:
        self.next_comment_id += 1
        return httpx.Response(201, json={"id": self.next_comment_id})

    def body_of(self, name: str, index: int = 0) -> Any:
        return json.loads(self.router[name].calls[index].request.content)


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    with respx.mock(base_url=GITHUB_API_URL, assert_all_called=False) as router:
        yield FakeGitHub(router)


def _deps(sessions: Sessions, auth: GitHubAppAuth, llm: FakeLLMProvider, **kw: Any) -> ReviewDeps:
    return ReviewDeps(
        sessionmaker=sessions,
        github_auth=auth,
        llm=llm,
        model="fake-model",
        lock=InMemoryPRLock(),
        **kw,
    )


async def _run(sessions: Sessions, run_id: int) -> ReviewRun:
    async with sessions() as session:
        run = await session.get(ReviewRun, run_id)
        assert run is not None
        return run


async def _rows(sessions: Sessions, model: Any) -> list[Any]:
    async with sessions() as session:
        return list((await session.scalars(select(model).order_by(model.id))).all())


# ---- tests ----


async def test_happy_path_posts_one_review_and_records_everything(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    llm = review_responder([_llm_comment(12, "SQL injection"), _llm_comment(99, "Hallucinated")])

    outcome = await execute_review_run(_deps(sessionmaker, github_auth, llm), run_id)

    assert (outcome.status, outcome.posted) == ("completed", 1)
    # One COMMENT review on the exact head commit, with the one valid inline comment.
    review = github.last_review
    assert (review["event"], review["commit_id"]) == ("COMMENT", HEAD)
    [comment] = review["comments"]
    assert (comment["path"], comment["line"], comment["side"]) == ("app/users.py", 12, "RIGHT")
    assert comment["body"].startswith("**🚨 Critical · Security: SQL injection**")
    assert "### 🤖 ReviewPilot review" in review["body"]
    assert "Changes the user lookup." in review["body"]
    # Check run: in progress, then neutral (a critical finding) with a summary.
    completed = github.body_of("complete_check")
    assert (completed["conclusion"], completed["output"]["title"]) == (
        "neutral",
        "1 issue: 1 critical",
    )
    assert "package-lock.json" in completed["output"]["summary"]  # skipped files are listed

    run = await _run(sessionmaker, run_id)
    assert (run.status, run.check_run_id, run.github_review_id) == ("completed", 77, 555)
    assert (run.comments_posted, run.comments_dropped_invalid_line) == (1, 1)
    assert run.input_tokens > 0 and run.latency_ms is not None and run.finished_at is not None
    assert (run.prompt_version, run.model) == ("v1", "fake-model")

    rows = await _rows(sessionmaker, ReviewCommentRecord)
    assert [(r.line, r.posted, r.drop_reason) for r in rows] == [
        (12, True, None),
        (99, False, "invalid_line"),
    ]
    assert rows[0].github_comment_id is not None and rows[0].fingerprint
    assert [c.purpose for c in await _rows(sessionmaker, LLMCall)] == ["file_review", "summary"]
    [config] = await _rows(sessionmaker, RepoConfigRecord)
    assert (config.commit_sha, config.raw_yaml) == ("d" * 40, None)  # cached: no file in repo
    [pr] = await _rows(sessionmaker, PullRequest)
    assert pr.last_reviewed_sha == HEAD


async def test_running_the_same_task_twice_posts_once(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    deps = _deps(sessionmaker, github_auth, review_responder([_llm_comment(12, "SQL injection")]))
    await execute_review_run(deps, run_id)
    second = await execute_review_run(deps, run_id)  # e.g. Celery redelivery (acks_late)

    assert (second.status, second.reason) == ("completed", "already_finished")
    assert github.router["review"].call_count == 1


async def test_a_new_run_never_reposts_old_comments(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    deps = _deps(sessionmaker, github_auth, review_responder([_llm_comment(12, "SQL injection")]))
    await execute_review_run(deps, run_id)
    async with sessionmaker() as session:
        again = await review_runs.create_queued(
            session, pull_request_id=1, trigger="command", mode="full", base_sha=BASE, head_sha=HEAD
        )
        await session.commit()

    outcome = await execute_review_run(deps, again.id)

    assert (outcome.status, outcome.posted) == ("completed", 0)
    assert github.router["review"].call_count == 1  # nothing new, and post_when_clean is off
    rows = [
        r for r in await _rows(sessionmaker, ReviewCommentRecord) if r.review_run_id == again.id
    ]
    assert [r.drop_reason for r in rows] == ["already_posted"]


async def test_stale_head_is_superseded_before_any_github_call(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    async with sessionmaker() as session:
        await session.execute(update(PullRequest).values(head_sha="c" * 40))
        await session.commit()

    outcome = await execute_review_run(
        _deps(sessionmaker, github_auth, review_responder([])), run_id
    )

    assert (outcome.status, outcome.reason) == ("superseded", "newer_commit")
    assert not github.router.calls


async def test_head_moving_during_the_review_blocks_posting(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    class PushDuringReview(FakeLLMProvider):
        async def complete(
            self, messages: list[Message], *, model: str, temperature: float
        ) -> Completion:
            async with sessionmaker() as session:  # a synchronize webhook lands mid-review
                await session.execute(update(PullRequest).values(head_sha="c" * 40))
                await session.commit()
            return await super().complete(messages, model=model, temperature=temperature)

    llm = PushDuringReview(responder=respond_with([_llm_comment(12, "SQL injection")]))
    outcome = await execute_review_run(_deps(sessionmaker, github_auth, llm), run_id)

    assert outcome.status == "superseded"
    assert github.router["review"].call_count == 0
    assert github.body_of("complete_check")["output"]["title"] == "Superseded"
    [row] = await _rows(sessionmaker, ReviewCommentRecord)
    assert (row.posted, row.drop_reason) == (False, "not_posted")


async def test_rejected_batch_falls_back_to_single_comments(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    github.router["review"].mock(
        side_effect=[
            httpx.Response(422, json={"message": "Unprocessable Entity"}),
            httpx.Response(200, json={"id": 556}),  # the summary, posted alone
        ]
    )
    github.router["single_comment"].mock(
        side_effect=[
            httpx.Response(201, json={"id": 7001}),
            httpx.Response(422, json={"message": "line must be part of the diff"}),
        ]
    )
    llm = review_responder(
        [_llm_comment(12, "SQL injection"), _llm_comment(13, "No error handling")]
    )

    outcome = await execute_review_run(_deps(sessionmaker, github_auth, llm), run_id)

    assert (outcome.status, outcome.posted) == ("completed", 1)
    assert github.body_of("review", 1)["comments"] == []  # summary review has no inline comments
    rows = await _rows(sessionmaker, ReviewCommentRecord)
    assert sorted((r.line, r.posted, r.github_comment_id, r.drop_reason) for r in rows) == [
        (12, True, 7001, None),
        (13, False, None, "github_rejected"),
    ]
    assert (await _run(sessionmaker, run_id)).github_review_id == 556


async def test_invalid_config_uses_defaults_and_says_so(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    github.config_yaml = "min_severity: apocalyptic\n"
    llm = review_responder([_llm_comment(12, "SQL injection")])

    outcome = await execute_review_run(_deps(sessionmaker, github_auth, llm), run_id)

    assert outcome.status == "completed"
    assert ".reviewpilot.yml problems" in github.last_review["body"]
    assert "min_severity" in github.body_of("complete_check")["output"]["summary"]


async def test_disabled_in_config_is_skipped_without_a_check_run(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    github.config_yaml = "enabled: false\n"
    outcome = await execute_review_run(
        _deps(sessionmaker, github_auth, review_responder([])), run_id
    )
    assert (outcome.status, outcome.reason) == ("skipped", "disabled_in_config")
    assert github.router["create_check"].call_count == 0


async def test_drafts_are_skipped_unless_configured(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    github.pr["draft"] = True
    outcome = await execute_review_run(
        _deps(sessionmaker, github_auth, review_responder([])), run_id
    )
    assert (outcome.status, outcome.reason) == ("skipped", "draft")


async def test_llm_outage_fails_the_run_without_blocking_the_merge(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    llm = FakeLLMProvider(responder=lambda _: LLMError("Gemini 503: high demand"))
    outcome = await execute_review_run(_deps(sessionmaker, github_auth, llm), run_id)

    assert (outcome.status, outcome.reason) == ("failed", "llm_unavailable")
    completed = github.body_of("complete_check")
    assert (completed["conclusion"], completed["output"]["title"]) == (
        "neutral",
        "ReviewPilot couldn't finish",
    )
    run = await _run(sessionmaker, run_id)
    assert (run.status, run.error_code) == ("failed", "llm_unavailable")
    assert [c.status for c in await _rows(sessionmaker, LLMCall)] == ["error"]


async def test_rate_limit_requeues_the_run_until_the_last_attempt(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    reset = str(int(datetime.now(UTC).timestamp()) + 600)
    github.router["files"].respond(
        403,
        json={"message": "API rate limit exceeded"},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset},
    )
    deps = _deps(sessionmaker, github_auth, review_responder([]))

    with pytest.raises(RetryableReviewError) as exc_info:
        await execute_review_run(deps, run_id, final_attempt=False)
    assert exc_info.value.retry_after is not None and exc_info.value.retry_after > 500
    run = await _run(sessionmaker, run_id)
    assert (run.status, run.check_run_id) == ("queued", 77)  # check run kept for the retry
    assert github.router["complete_check"].call_count == 0

    outcome = await execute_review_run(deps, run_id, final_attempt=True)
    assert (outcome.status, outcome.reason) == ("failed", "github_403")
    assert github.router["create_check"].call_count == 1  # reused, not recreated
    assert github.body_of("complete_check")["conclusion"] == "neutral"


async def test_busy_pr_raises_so_the_task_waits(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    lock = InMemoryPRLock()
    lock.held.add(1)
    deps = ReviewDeps(
        sessionmaker=sessionmaker,
        github_auth=github_auth,
        llm=review_responder([]),
        model="fake-model",
        lock=lock,
    )
    with pytest.raises(ReviewBusy):
        await execute_review_run(deps, run_id)
    assert (await _run(sessionmaker, run_id)).status == "queued"


async def test_stuck_runs_are_failed(sessionmaker: Sessions, run_id: int) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    async with sessionmaker() as session:
        await session.execute(update(ReviewRun).values(status="running", started_at=long_ago))
        count = await review_runs.fail_stuck_runs(
            session,
            started_before=datetime.now(UTC) - timedelta(minutes=15),
            queued_before=datetime.now(UTC) - timedelta(minutes=30),
        )
        await session.commit()
    assert count == 1
    run = await _run(sessionmaker, run_id)
    assert (run.status, run.error_code) == ("failed", "timeout")
