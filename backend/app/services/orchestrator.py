"""The review pipeline: owns every side effect (GitHub calls, DB writes).

Phase 1 version: a "hello" review that proves the whole loop works. It posts
one hardcoded comment on the first added line and a check run. Phase 3
replaces the middle with the real review engine; the shape stays the same.
"""

import re
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github.app_auth import GitHubAppAuth
from app.github.client import CheckRunOutput, PullRequestFile, ReviewCommentInput
from app.repositories import pull_requests

log = structlog.get_logger()

CHECK_RUN_NAME = "ReviewPilot"
HELLO_REVIEW_BODY = "👋 ReviewPilot is installed and connected to this repository."
HELLO_COMMENT = (
    "👋 **ReviewPilot is connected.**\n\n"
    "This is a placeholder comment on the first added line of this pull request. "
    "Real AI reviews are coming soon.\n\n"
    "<sub>ReviewPilot · hello loop</sub>"
)
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class HelloReviewResult:
    status: Literal["posted", "no_added_lines", "superseded", "skipped"]
    review_id: int | None = None
    check_run_id: int | None = None


async def run_hello_review(
    sessionmaker: async_sessionmaker[AsyncSession],
    github_auth: GitHubAppAuth,
    *,
    pull_request_id: int,
    head_sha: str,
) -> HelloReviewResult:
    async with sessionmaker() as session:
        pr = await pull_requests.get_with_repository(session, pull_request_id)
    if pr is None or pr.state != "open":
        return HelloReviewResult("skipped")
    if pr.head_sha != head_sha:
        # A newer commit arrived after this job was queued; its own job will run.
        return HelloReviewResult("superseded")

    owner, repo = pr.repository.owner_and_name
    github = github_auth.installation_client(pr.repository.installation.github_installation_id)
    logger = log.bind(repo=pr.repository.full_name, pr=pr.number, head_sha=head_sha)

    check_run_id = await github.create_check_run(
        owner,
        repo,
        name=CHECK_RUN_NAME,
        head_sha=head_sha,
        output=CheckRunOutput(title="Reviewing…", summary="ReviewPilot is reviewing this PR."),
    )
    try:
        files = await github.list_pull_request_files(owner, repo, pr.number)
        target = first_commentable_line(files)
        if target is None:
            await github.complete_check_run(
                owner,
                repo,
                check_run_id,
                conclusion="success",
                output=CheckRunOutput(
                    title="Nothing to comment on",
                    summary="This pull request has no added lines with a text diff.",
                ),
            )
            return HelloReviewResult("no_added_lines", check_run_id=check_run_id)

        path, line = target
        review_id = await github.create_review(
            owner,
            repo,
            pr.number,
            commit_id=head_sha,  # pins the comment to the exact commit we looked at
            body=HELLO_REVIEW_BODY,
            comments=[ReviewCommentInput(path=path, line=line, body=HELLO_COMMENT)],
        )
        await github.complete_check_run(
            owner,
            repo,
            check_run_id,
            conclusion="success",
            output=CheckRunOutput(
                title="Hello from ReviewPilot",
                summary=f"Posted a placeholder comment on `{path}` line {line}.",
            ),
        )
        logger.info("review.hello_posted", review_id=review_id, path=path, line=line)
        return HelloReviewResult("posted", review_id=review_id, check_run_id=check_run_id)
    except Exception:
        # Never leave the check spinning, and never block a merge because *we* failed.
        with suppress(Exception):
            await github.complete_check_run(
                owner,
                repo,
                check_run_id,
                conclusion="neutral",
                output=CheckRunOutput(
                    title="ReviewPilot couldn't finish",
                    summary="An internal error occurred. This check never blocks merging.",
                ),
            )
        raise


def first_commentable_line(files: Sequence[PullRequestFile]) -> tuple[str, int] | None:
    """(path, new-file line number) of the first added line in the first file that has one.

    A deliberately tiny diff reader for Phase 1; the full parser comes in Phase 2.
    """
    for file in files:
        if file.status == "removed" or not file.patch:
            continue
        line = _first_added_line(file.patch)
        if line is not None:
            return file.filename, line
    return None


def _first_added_line(patch: str) -> int | None:
    new_line: int | None = None
    for raw in patch.splitlines():
        header = HUNK_HEADER.match(raw)
        if header:
            new_line = int(header.group(1))
        elif new_line is None:
            continue
        elif raw.startswith("+"):
            return new_line
        elif raw.startswith(" "):
            new_line += 1  # context lines exist in the new file too
        # "-" lines exist only in the old file; "\ No newline..." is metadata.
    return None
