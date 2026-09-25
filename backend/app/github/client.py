"""Thin, typed wrapper over the GitHub REST API.

Handles the cross-cutting concerns once so callers don't have to:
auth header, API version pinning, pagination, rate limits, retries.
Reference: https://docs.github.com/en/rest/using-the-rest-api
"""

import asyncio
import base64
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

import httpx
import structlog
from pydantic import BaseModel

log = structlog.get_logger()

GITHUB_API_URL = "https://api.github.com"
DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "ReviewPilot",
}
# GitHub's docs: with no retry-after/reset hint, wait at least a minute.
SECONDARY_RATE_LIMIT_DEFAULT_WAIT = 60.0

TokenGetter = Callable[[], Awaitable[str]]
# Never "failure": ReviewPilot advises, humans decide (ADR 0006).
Conclusion = Literal["success", "neutral", "skipped"]
Sleep = Callable[[float], Awaitable[None]]


class GitHubError(Exception):
    def __init__(self, status_code: int, message: str, request_id: str | None) -> None:
        super().__init__(f"GitHub API {status_code}: {message} (request id {request_id})")
        self.status_code = status_code
        self.message = message
        self.request_id = request_id

    def __reduce__(self) -> tuple[Any, ...]:
        # Celery pickles exceptions to store/report them. The default pickling
        # replays self.args (one string) into __init__, which needs three.
        return (type(self), (self.status_code, self.message, self.request_id))

    @classmethod
    def from_response(cls, response: httpx.Response) -> "GitHubError":
        status_code, message, request_id = _error_details(response)
        return cls(status_code, message, request_id)


class GitHubRateLimited(GitHubError):
    """Raised when we must wait longer than is reasonable inside one request.

    The Celery task catches it and retries after `retry_after` seconds.
    """

    def __init__(
        self, status_code: int, message: str, request_id: str | None, retry_after: float
    ) -> None:
        super().__init__(status_code, message, request_id)
        self.retry_after = retry_after

    def __reduce__(self) -> tuple[Any, ...]:
        return (
            type(self),
            (self.status_code, self.message, self.request_id, self.retry_after),
        )


def _error_details(response: httpx.Response) -> tuple[int, str, str | None]:
    try:
        message = str(response.json().get("message", response.text))
    except ValueError:
        message = response.text
    return response.status_code, message[:500], response.headers.get("x-github-request-id")


def create_http_client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=GITHUB_API_URL, headers=DEFAULT_HEADERS, timeout=timeout)


# ---- response / request models (only the fields we use) ----


class InstallationToken(BaseModel):
    token: str
    expires_at: datetime


class PullRequestFile(BaseModel):
    filename: str
    status: str  # added | removed | modified | renamed | copied | changed | unchanged
    additions: int
    deletions: int
    # Missing for binary files and for diffs GitHub considers too large.
    patch: str | None = None
    previous_filename: str | None = None


class ReviewCommentInput(BaseModel):
    path: str
    line: int
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    start_line: int | None = None
    start_side: Literal["LEFT", "RIGHT"] | None = None
    body: str


class CheckRunOutput(BaseModel):
    title: str
    summary: str


class PullRequestInfo(BaseModel):
    """The live PR from GitHub (webhook payloads can be stale by the time we run)."""

    number: int
    title: str
    body: str | None = None
    state: str
    draft: bool = False
    head_sha: str
    head_ref: str
    base_sha: str
    base_ref: str
    author: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PullRequestInfo":
        return cls(
            number=data["number"],
            title=data["title"],
            body=data.get("body"),
            state=data["state"],
            draft=bool(data.get("draft")),
            head_sha=data["head"]["sha"],
            head_ref=data["head"]["ref"],
            base_sha=data["base"]["sha"],
            base_ref=data["base"]["ref"],
            author=data["user"]["login"],
        )


class PostedComment(BaseModel):
    id: int
    path: str
    body: str
    line: int | None = None


class GitHubClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        get_token: TokenGetter,
        *,
        on_unauthorized: Callable[[], Awaitable[None]] | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        max_retries: int = 3,
        max_inline_wait: float = SECONDARY_RATE_LIMIT_DEFAULT_WAIT,
    ) -> None:
        self._http = http
        self._get_token = get_token
        self._on_unauthorized = on_unauthorized
        self._sleep = sleep
        self._clock = clock
        self._max_retries = max_retries
        self._max_inline_wait = max_inline_wait

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        attempt = 0
        refreshed_token = False
        while True:
            token = await self._get_token()
            try:
                response = await self._http.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.TransportError:
                if attempt >= self._max_retries:
                    raise
                await self._sleep(self._backoff(attempt))
                attempt += 1
                continue

            status = response.status_code
            if status == 401 and self._on_unauthorized and not refreshed_token:
                # Cached token may have been revoked early: drop it and retry once.
                await self._on_unauthorized()
                refreshed_token = True
                continue
            if status >= 500 and attempt < self._max_retries:
                await self._sleep(self._backoff(attempt))
                attempt += 1
                continue
            if status in (403, 429):
                wait = self._rate_limit_wait(response)
                if wait is not None:
                    if wait <= self._max_inline_wait and attempt < self._max_retries:
                        await self._sleep(wait)
                        attempt += 1
                        continue
                    raise GitHubRateLimited(*_error_details(response), retry_after=wait)
            if response.is_error:
                error = GitHubError.from_response(response)
                log.warning(
                    "github.request_failed",
                    method=method,
                    url=str(response.request.url),
                    status=status,
                    github_request_id=error.request_id,
                )
                raise error
            return response

    async def paginate(self, url: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Follow `Link: <...>; rel="next"` headers until the last page."""
        items: list[Any] = []
        next_url: str | None = url
        next_params: dict[str, Any] | None = {"per_page": 100, **(params or {})}
        while next_url:
            response = await self.request("GET", next_url, params=next_params)
            items.extend(response.json())
            next_url = response.links.get("next", {}).get("url")
            next_params = None  # the next URL already carries the query string
        return items

    def _rate_limit_wait(self, response: httpx.Response) -> float | None:
        """Seconds to wait if this 403/429 is a rate limit, else None (a real 403)."""
        headers = response.headers
        if "retry-after" in headers:
            return float(headers["retry-after"])  # secondary rate limit
        if headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in headers:
            return max(float(headers["x-ratelimit-reset"]) - self._clock(), 1.0)  # primary
        if "rate limit" in response.text.lower():
            return SECONDARY_RATE_LIMIT_DEFAULT_WAIT
        return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        # Exponential backoff with jitter: 0.5s, 1s, 2s, ... plus up to 0.5s random,
        # so many workers retrying at once don't hit GitHub in lockstep.
        return 0.5 * 2.0**attempt + random.uniform(0, 0.5)  # noqa: S311 (not crypto)

    # ---- typed endpoints ----

    async def create_installation_token(self, installation_id: int) -> InstallationToken:
        """Requires the App JWT as the token. POST /app/installations/{id}/access_tokens."""
        response = await self.request("POST", f"/app/installations/{installation_id}/access_tokens")
        return InstallationToken.model_validate(response.json())

    async def list_pull_request_files(
        self, owner: str, repo: str, number: int
    ) -> list[PullRequestFile]:
        items = await self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/files")
        return [PullRequestFile.model_validate(item) for item in items]

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        *,
        name: str,
        head_sha: str,
        status: Literal["queued", "in_progress"] = "in_progress",
        output: CheckRunOutput | None = None,
    ) -> int:
        payload: dict[str, Any] = {"name": name, "head_sha": head_sha, "status": status}
        if output:
            payload["output"] = output.model_dump()
        response = await self.request("POST", f"/repos/{owner}/{repo}/check-runs", json=payload)
        return int(response.json()["id"])

    async def complete_check_run(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        *,
        conclusion: Conclusion,
        output: CheckRunOutput,
    ) -> None:
        # ReviewPilot never concludes "failure": it advises, humans decide.
        await self.request(
            "PATCH",
            f"/repos/{owner}/{repo}/check-runs/{check_run_id}",
            json={"status": "completed", "conclusion": conclusion, "output": output.model_dump()},
        )

    async def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[ReviewCommentInput],
    ) -> int:
        # event is always COMMENT: never APPROVE or REQUEST_CHANGES.
        payload = {
            "commit_id": commit_id,
            "body": body,
            "event": "COMMENT",
            "comments": [c.model_dump(exclude_none=True) for c in comments],
        }
        response = await self.request(
            "POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews", json=payload
        )
        return int(response.json()["id"])

    async def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequestInfo:
        response = await self.request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        return PullRequestInfo.from_api(response.json())

    async def get_branch_head_sha(self, owner: str, repo: str, branch: str) -> str:
        response = await self.request("GET", f"/repos/{owner}/{repo}/branches/{branch}")
        return str(response.json()["commit"]["sha"])

    async def get_file_text(
        self, owner: str, repo: str, path: str, *, ref: str, max_bytes: int
    ) -> str | None:
        """A file's text at `ref`, or None if it doesn't exist (or isn't a file)."""
        try:
            response = await self.request(
                "GET", f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref}
            )
        except GitHubError as exc:
            if exc.status_code == 404:
                return None
            raise
        data = response.json()
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        if int(data.get("size", 0)) > max_bytes:
            raise ValueError(f"{path} is larger than {max_bytes} bytes")
        # Content is base64 with embedded newlines; b64decode ignores them.
        return base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")

    async def create_review_comment(
        self, owner: str, repo: str, number: int, *, commit_id: str, comment: ReviewCommentInput
    ) -> int:
        """One inline comment outside a review: the fallback when a batch review is rejected."""
        payload = {"commit_id": commit_id, **comment.model_dump(exclude_none=True)}
        response = await self.request(
            "POST", f"/repos/{owner}/{repo}/pulls/{number}/comments", json=payload
        )
        return int(response.json()["id"])

    async def list_review_comments(
        self, owner: str, repo: str, number: int, review_id: int
    ) -> list[PostedComment]:
        items = await self.paginate(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews/{review_id}/comments"
        )
        return [PostedComment.model_validate(item) for item in items]

    async def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> int:
        response = await self.request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json={"body": body}
        )
        return int(response.json()["id"])
