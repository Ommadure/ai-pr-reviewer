"""Shared fakes for review-pipeline integration tests: a scripted GitHub and LLM."""

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github.app_auth import GitHubAppAuth
from app.models import ReviewRun
from app.review.llm.base import Message
from app.review.llm.fake import FakeLLMProvider
from app.services.orchestrator import ReviewDeps

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


def llm_comment(line: int, title: str, **extra: Any) -> dict[str, Any]:
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


class FakeGitHub:
    """respx routes for every call the orchestrator makes, with sensible defaults."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.next_comment_id = 9000
        self.config_yaml: str | None = None
        self.pr = {
            "id": 2001,
            "number": 1,
            "title": "Add user lookup",
            "body": "Looks users up by id.",
            "state": "open",
            "draft": False,
            "head": {"sha": HEAD, "ref": "feature"},
            "base": {"sha": BASE, "ref": "main"},
            "user": {"login": "octocat", "type": "User"},
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
        # What compare(last_reviewed...head) returns, for incremental reviews.
        self.compare: dict[str, Any] = {"status": "ahead", "ahead_by": 1, "files": []}
        router.get(path__regex=rf"{REPO}/compare/.+", name="compare").mock(
            side_effect=lambda _: httpx.Response(200, json=self.compare)
        )
        # Slash-command endpoints.
        self.role = "write"
        self.replies: list[str] = []
        router.post(path__regex=rf"{REPO}/issues/comments/\d+/reactions", name="reaction").respond(
            201, json={"content": "eyes"}
        )
        router.get(path__regex=rf"{REPO}/collaborators/[^/]+/permission", name="permission").mock(
            side_effect=lambda _: httpx.Response(
                200, json={"permission": "write", "role_name": self.role}
            )
        )
        router.post(f"{REPO}/issues/1/comments", name="reply").mock(side_effect=self._reply)

    def _reply(self, request: httpx.Request) -> httpx.Response:
        self.replies.append(json.loads(request.content)["body"])
        return httpx.Response(201, json={"id": 1})

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


def make_deps(
    sessions: Sessions, auth: GitHubAppAuth, llm: FakeLLMProvider, **kw: Any
) -> ReviewDeps:
    return ReviewDeps(
        sessionmaker=sessions,
        github_auth=auth,
        llm=llm,
        model="fake-model",
        **kw,
    )


async def get_run(sessions: Sessions, run_id: int) -> ReviewRun:
    async with sessions() as session:
        run = await session.get(ReviewRun, run_id)
        assert run is not None
        return run


async def all_rows(sessions: Sessions, model: Any) -> list[Any]:
    async with sessions() as session:
        return list((await session.scalars(select(model).order_by(model.id))).all())
