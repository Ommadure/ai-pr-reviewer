"""Incremental reviews: only commits since the last review, with safe fallbacks."""

import pytest
from sqlalchemy import update

from app.github.app_auth import GitHubAppAuth
from app.models import PullRequest, ReviewCommentRecord, ReviewRun
from app.repositories import review_runs
from app.review.fingerprint import fingerprint
from app.services.orchestrator import execute_review_run
from tests.integration.review_fakes import (
    BASE,
    HEAD,
    FakeGitHub,
    Sessions,
    all_rows,
    get_run,
    llm_comment,
    make_deps,
    review_responder,
)

PREVIOUS = "e" * 40  # the head we reviewed last time
# Since PREVIOUS the author rewrote line 13 (old 13 → new 13) and added line 14; they
# also merged main, which touched a file that isn't part of the PR at all.
COMPARE_FILES = [
    {
        "filename": "app/users.py",
        "status": "modified",
        "additions": 2,
        "deletions": 1,
        "patch": "@@ -12,2 +12,3 @@\n     q = 1\n-    row = old()\n+    row = db.execute(query).fetchone()\n+    return row\n",  # noqa: E501
    },
    {
        "filename": "docs/from_main.md",
        "status": "modified",
        "additions": 1,
        "deletions": 0,
        "patch": "@@ -1 +1,2 @@\n x\n+y\n",
    },
]


@pytest.fixture
async def incremental_run(sessionmaker: Sessions, run_id: int) -> int:
    """A PR already reviewed at PREVIOUS, with one open posted comment on old line 13."""
    async with sessionmaker() as session:
        await session.execute(update(PullRequest).values(last_reviewed_sha=PREVIOUS))
        await session.execute(
            update(ReviewRun)
            .where(ReviewRun.id == run_id)
            .values(status="completed", head_sha=PREVIOUS)
        )
        session.add(
            ReviewCommentRecord(
                review_run_id=run_id,
                pull_request_id=1,
                path="app/users.py",
                line=13,
                severity="high",
                category="bug",
                title="old() can return None",
                body="",
                fingerprint=fingerprint("app/users.py", "bug", "row = old()"),
                code_snapshot="    row = old()",
                posted=True,
                github_comment_id=4242,
            )
        )
        run = await review_runs.create_queued(
            session,
            pull_request_id=1,
            trigger="synchronize",
            mode="incremental",
            base_sha=BASE,
            head_sha=HEAD,
        )
        await session.commit()
        return run.id


async def test_only_new_lines_are_reviewed_and_fixed_comments_are_addressed(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, incremental_run: int
) -> None:
    github.compare = {"status": "ahead", "ahead_by": 2, "files": COMPARE_FILES}
    llm = review_responder([llm_comment(14, "Returns raw row"), llm_comment(12, "Old line")])

    outcome = await execute_review_run(make_deps(sessionmaker, github_auth, llm), incremental_run)

    assert outcome.status == "completed"
    run = await get_run(sessionmaker, incremental_run)
    assert (run.mode, run.from_sha, run.mode_reason) == ("incremental", PREVIOUS, None)
    assert {"path": "docs/from_main.md", "reason": "not_in_pr_diff"} in run.files_skipped
    # Line 14 is new: commented. Line 12 wasn't changed since the last review: dropped.
    assert [c["line"] for c in github.last_review["comments"]] == [14]
    assert github.router["compare"].calls[0].request.url.path.endswith(f"{PREVIOUS}...{HEAD}")
    # The author rewrote the line the old comment was about.
    old = [
        r for r in await all_rows(sessionmaker, ReviewCommentRecord) if r.github_comment_id == 4242
    ]
    assert old[0].status == "addressed"


async def test_no_previous_review_falls_back_to_full(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    async with sessionmaker() as session:
        await session.execute(update(ReviewRun).values(mode="incremental"))
        await session.commit()

    await execute_review_run(make_deps(sessionmaker, github_auth, review_responder([])), run_id)

    run = await get_run(sessionmaker, run_id)
    assert (run.mode, run.mode_reason) == ("full", "no_previous_review")
    assert github.router["compare"].call_count == 0


@pytest.mark.parametrize("status", ["diverged", "behind"])
async def test_rewritten_history_falls_back_to_full(
    sessionmaker: Sessions,
    github_auth: GitHubAppAuth,
    github: FakeGitHub,
    incremental_run: int,
    status: str,
) -> None:
    github.compare = {"status": status, "files": []}  # a force push or rebase
    llm = review_responder([llm_comment(12, "SQL injection")])

    await execute_review_run(make_deps(sessionmaker, github_auth, llm), incremental_run)

    run = await get_run(sessionmaker, incremental_run)
    assert (run.mode, run.mode_reason, run.from_sha) == ("full", "history_rewritten", None)
    assert [c["line"] for c in github.last_review["comments"]] == [12]  # whole PR reviewed


async def test_nothing_new_since_last_review_is_skipped(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, incremental_run: int
) -> None:
    github.compare = {"status": "identical", "files": []}
    outcome = await execute_review_run(
        make_deps(sessionmaker, github_auth, review_responder([])), incremental_run
    )
    assert (outcome.status, outcome.reason) == ("skipped", "no_new_changes")
    assert github.router["create_check"].call_count == 0


async def test_summary_is_stored_for_the_summary_command(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    await execute_review_run(make_deps(sessionmaker, github_auth, review_responder([])), run_id)
    run = await get_run(sessionmaker, run_id)
    assert run.summary is not None and run.summary["overview"] == "Changes the user lookup."
