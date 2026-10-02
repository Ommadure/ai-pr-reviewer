"""Slash commands against real Postgres and a mocked GitHub."""

import pytest
from sqlalchemy import delete, update

from app.github.app_auth import GitHubAppAuth
from app.models import PullRequest, ReviewRun
from app.services.commands import CommandDeps, CommandJob, handle_command
from tests.helpers import queued_jobs
from tests.integration.review_fakes import FakeGitHub, Sessions, all_rows


@pytest.fixture
def wakes() -> list[None]:
    return []


@pytest.fixture
def deps(sessionmaker: Sessions, github_auth: GitHubAppAuth, wakes: list[None]) -> CommandDeps:
    return CommandDeps(
        sessionmaker=sessionmaker,
        github_auth=github_auth,
        wake_worker=lambda: wakes.append(None),
        docs_url="https://example.test/docs",
    )


async def _queued_run_ids(sessionmaker: Sessions) -> list[int]:
    return [job.payload["run_id"] for job in await queued_jobs(sessionmaker, "review")]


def _job(command: str, *, repository_id: int = 1, author: str = "octocat") -> CommandJob:
    return CommandJob(
        installation_id=4001,
        repository_id=repository_id,
        repo_full_name="octocat/playground",
        pr_number=1,
        comment_id=5001,
        author=author,
        command=command,
    )


async def test_review_command_queues_a_full_review(
    deps: CommandDeps, github: FakeGitHub, run_id: int, wakes: list[None], sessionmaker: Sessions
) -> None:
    outcome = await handle_command(deps, _job("review"))

    assert outcome == "review_queued"
    assert github.router["reaction"].call_count == 1  # 👀 first
    runs = await all_rows(sessionmaker, ReviewRun)
    new = runs[-1]
    assert (new.trigger, new.mode, new.status) == ("command", "full", "queued")
    assert await _queued_run_ids(sessionmaker) == [new.id]
    assert len(wakes) == 1  # the worker starts it now, not on a poll
    assert github.replies == ["🔍 Starting a full review of `aaaaaaa`."]


@pytest.mark.parametrize(
    ("merged", "message"),
    [
        (True, "This PR is already merged, so there's nothing left to review."),
        (False, "This PR is closed. Reopen it, then run `/reviewpilot review` again."),
    ],
)
async def test_review_command_on_a_closed_pr_says_so_and_queues_nothing(
    deps: CommandDeps,
    github: FakeGitHub,
    run_id: int,
    sessionmaker: Sessions,
    merged: bool,
    message: str,
) -> None:
    github.pr |= {"state": "closed", "merged": merged}

    assert await handle_command(deps, _job("review")) == "pr_closed"
    assert github.replies == [message]
    assert await _queued_run_ids(sessionmaker) == []
    assert len(await all_rows(sessionmaker, ReviewRun)) == 1  # quota untouched
    [pr] = await all_rows(sessionmaker, PullRequest)
    assert pr.state == ("merged" if merged else "closed")  # our copy is refreshed too


async def test_review_command_uses_the_live_pr_not_our_copy(
    deps: CommandDeps, github: FakeGitHub, run_id: int, sessionmaker: Sessions
) -> None:
    # A missed webhook left us thinking the PR is merged, on an old head.
    async with sessionmaker() as session:
        await session.execute(update(PullRequest).values(state="merged", head_sha="b" * 40))
        await session.commit()

    assert await handle_command(deps, _job("review")) == "review_queued"
    [pr] = await all_rows(sessionmaker, PullRequest)
    assert (pr.state, pr.head_sha) == ("open", "a" * 40)
    assert (await all_rows(sessionmaker, ReviewRun))[-1].head_sha == "a" * 40


async def test_privileged_commands_need_write_access(
    deps: CommandDeps, github: FakeGitHub, run_id: int, sessionmaker: Sessions
) -> None:
    github.role = "read"
    assert await handle_command(deps, _job("review", author="drive-by")) == "forbidden"
    assert await _queued_run_ids(sessionmaker) == []
    assert "write access" in github.replies[0]
    assert "@" not in github.replies[0]  # the bot never pings anyone


async def test_manual_reviews_are_rate_limited(
    deps: CommandDeps, github: FakeGitHub, run_id: int, sessionmaker: Sessions
) -> None:
    outcomes = [await handle_command(deps, _job("review")) for _ in range(6)]
    assert outcomes == ["review_queued"] * 5 + ["rate_limited"]
    assert len(await _queued_run_ids(sessionmaker)) == 5


async def test_pause_and_resume(
    deps: CommandDeps, github: FakeGitHub, run_id: int, sessionmaker: Sessions
) -> None:
    await handle_command(deps, _job("pause"))
    [pr] = await all_rows(sessionmaker, PullRequest)
    assert pr.paused is True
    await handle_command(deps, _job("resume"))
    [pr] = await all_rows(sessionmaker, PullRequest)
    assert pr.paused is False
    assert github.replies[0].startswith("⏸️") and github.replies[1].startswith("▶️")


async def test_summary_reposts_the_latest_stored_summary(
    deps: CommandDeps, github: FakeGitHub, run_id: int, sessionmaker: Sessions
) -> None:
    assert await handle_command(deps, _job("summary")) == "no_summary"

    async with sessionmaker() as session:
        await session.execute(
            update(ReviewRun).values(
                status="completed",
                summary={"overview": "Adds user lookup.", "risk_level": "medium",
                         "key_changes": ["new endpoint"], "notes": []},
            )
        )  # fmt: skip
        await session.commit()
    assert await handle_command(deps, _job("summary")) == "summary"
    reply = github.replies[-1]
    assert "Adds user lookup." in reply and "**Risk:** medium" in reply


async def test_help_and_unknown_commands(
    deps: CommandDeps, github: FakeGitHub, run_id: int
) -> None:
    assert await handle_command(deps, _job("help")) == "help"
    assert await handle_command(deps, _job("dance")) == "unknown"
    assert "`/reviewpilot review`" in github.replies[0]
    assert github.replies[1].startswith("Unknown command `dance`")
    assert github.router["permission"].call_count == 0  # no permission needed for these


async def test_pr_we_never_saw_is_fetched_from_github(
    deps: CommandDeps, github: FakeGitHub, sessionmaker: Sessions, run_id: int
) -> None:
    async with sessionmaker() as session:  # simulate "App installed after the PR opened"
        await session.execute(delete(ReviewRun))
        await session.execute(delete(PullRequest))
        await session.commit()

    assert await handle_command(deps, _job("pause")) == "pause"
    [pr] = await all_rows(sessionmaker, PullRequest)
    assert (pr.number, pr.paused, pr.head_sha) == (1, True, "a" * 40)
