"""`/reviewpilot <command>` in PR conversation comments.

| Command   | Effect                                  | Who            |
|-----------|-----------------------------------------|----------------|
| review    | full re-review of the current head      | write access   |
| summary   | re-post the latest PR summary           | anyone         |
| pause     | stop automatic reviews on this PR       | write access   |
| resume    | turn automatic reviews back on          | write access   |
| help      | list commands                           | anyone         |

Every command gets an immediate 👀 reaction and a reply with the outcome.
Manual reviews are rate limited (5 per PR per hour) because each one costs LLM calls.
"""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import bind_context
from app.github import events
from app.github.app_auth import GitHubAppAuth
from app.github.client import GitHubClient, GitHubError
from app.models import PullRequest
from app.repositories import jobs, pull_requests, review_runs
from app.review.models import PRSummaryOutput
from app.services import review_report

log = structlog.get_logger()

COMMAND_PREFIX = "/reviewpilot"
COMMANDS = {"review", "summary", "pause", "resume", "help"}
PRIVILEGED = {"review", "pause", "resume"}
WRITE_ROLES = {"admin", "maintain", "write"}
MANUAL_REVIEWS_PER_HOUR = 5


def parse_command(body: str) -> str | None:
    """The command name if the comment's first line is a /reviewpilot command, else None."""
    first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    tokens = first_line.split()
    if not tokens or tokens[0].lower() != COMMAND_PREFIX:
        return None
    return tokens[1].lower() if len(tokens) > 1 else "help"


@dataclass(frozen=True)
class CommandJob:
    installation_id: int  # GitHub's id
    repository_id: int  # ours
    repo_full_name: str
    pr_number: int
    comment_id: int
    author: str
    command: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class CommandDeps:
    sessionmaker: async_sessionmaker[AsyncSession]
    github_auth: GitHubAppAuth
    wake_worker: Callable[[], None] = lambda: None  # a queued job starts now, not on a poll
    docs_url: str = ""


def help_text(docs_url: str = "") -> str:
    rows = "\n".join(
        [
            "| Command | What it does |",
            "|---|---|",
            "| `/reviewpilot review` | Full re-review of the latest commit "
            f"(write access, at most {MANUAL_REVIEWS_PER_HOUR} per hour) |",
            "| `/reviewpilot summary` | Re-post the latest PR summary |",
            "| `/reviewpilot pause` | Stop automatic reviews on this PR (write access) |",
            "| `/reviewpilot resume` | Turn automatic reviews back on (write access) |",
            "| `/reviewpilot help` | Show this message |",
        ]
    )
    config = "Configure ReviewPilot per repository with `.reviewpilot.yml` on the default branch"
    config += f" ([reference]({docs_url}))." if docs_url else "."
    return f"**ReviewPilot commands**\n\n{rows}\n\n{config}"


async def handle_command(deps: CommandDeps, job: CommandJob) -> str:
    owner, name = job.repo_full_name.split("/", 1)
    github = deps.github_auth.installation_client(job.installation_id)
    bind_context(repo=job.repo_full_name, pr=job.pr_number, command=job.command)
    logger = log

    async def reply(body: str) -> None:
        await github.create_issue_comment(owner, name, job.pr_number, body)

    # Acknowledge first, so the author knows we saw it even if the rest takes a while.
    with suppress(GitHubError):
        await github.add_issue_comment_reaction(owner, name, job.comment_id, "eyes")

    if job.command not in COMMANDS:
        await reply(f"Unknown command `{_code(job.command)}`.\n\n{help_text(deps.docs_url)}")
        return "unknown"
    if job.command == "help":
        await reply(help_text(deps.docs_url))
        return "help"

    if job.command in PRIVILEGED:
        role = await github.get_collaborator_role(owner, name, job.author)
        if role not in WRITE_ROLES:
            logger.info("command.forbidden", role=role)
            await reply(
                f"Only collaborators with write access can use `/reviewpilot {job.command}`."
            )
            return "forbidden"

    # A review needs the live PR: our copy can be stale (a missed webhook), and replying
    # "Starting a full review" on a merged PR, then silently skipping it, misleads.
    # Checked before the rate limit, so a mistaken command doesn't use up the quota.
    pr = await (
        _fetch_pull_request(deps, github, job)
        if job.command == "review"
        else _ensure_pull_request(deps, github, job)
    )
    match job.command:
        case "review":
            if pr.state != "open":
                await reply(
                    "This PR is already merged, so there's nothing left to review."
                    if pr.state == "merged"
                    else "This PR is closed. Reopen it, then run `/reviewpilot review` again."
                )
                return "pr_closed"
            async with deps.sessionmaker() as session:
                if await review_runs.manual_runs_last_hour(session, pr.id) >= (
                    MANUAL_REVIEWS_PER_HOUR
                ):
                    await reply(
                        f"This PR has had {MANUAL_REVIEWS_PER_HOUR} manual reviews in the last "
                        "hour. Please try again later."
                    )
                    return "rate_limited"
                run = await review_runs.create_queued(
                    session,
                    pull_request_id=pr.id,
                    trigger="command",
                    mode="full",
                    base_sha=pr.base_sha,
                    head_sha=pr.head_sha,
                )
                jobs.enqueue_review(session, run_id=run.id, pull_request_id=pr.id)
                await session.commit()
            deps.wake_worker()
            await reply(f"🔍 Starting a full review of `{pr.head_sha[:7]}`.")
            return "review_queued"
        case "summary":
            async with deps.sessionmaker() as session:
                latest = await review_runs.latest_summary(session, pr.id)
            if latest is None or latest.summary is None:
                await reply("There's no completed review with a summary for this PR yet.")
                return "no_summary"
            summary = PRSummaryOutput.model_validate(latest.summary)
            await reply(review_report.summary_markdown(summary, head_sha=latest.head_sha))
            return "summary"
        case "pause" | "resume":
            paused = job.command == "pause"
            await _set_paused(deps, pr.id, paused)
            await reply(
                "⏸️ Automatic reviews are paused for this PR. `/reviewpilot resume` turns them "
                "back on; `/reviewpilot review` still works."
                if paused
                else "▶️ Automatic reviews are back on for this PR."
            )
            return job.command
    return "noop"  # pragma: no cover - every command is handled above


async def _ensure_pull_request(
    deps: CommandDeps, github: GitHubClient, job: CommandJob
) -> PullRequest:
    """We may not have seen this PR yet (e.g. the App was installed after it opened)."""
    async with deps.sessionmaker() as session:
        pr = await pull_requests.get_by_number(session, job.repository_id, job.pr_number)
    return pr if pr is not None else await _fetch_pull_request(deps, github, job)


async def _fetch_pull_request(
    deps: CommandDeps, github: GitHubClient, job: CommandJob
) -> PullRequest:
    """The PR as GitHub has it now (state, head), saved over our copy."""
    owner, name = job.repo_full_name.split("/", 1)
    raw = (await github.request("GET", f"/repos/{owner}/{name}/pulls/{job.pr_number}")).json()
    async with deps.sessionmaker() as session:
        pr = await pull_requests.upsert_from_payload(
            session, repository_id=job.repository_id, payload=events.PullRequest.model_validate(raw)
        )
        await session.commit()
        return pr


async def _set_paused(deps: CommandDeps, pull_request_id: int, paused: bool) -> None:
    async with deps.sessionmaker() as session:
        await session.execute(
            update(PullRequest)
            .where(PullRequest.id == pull_request_id)
            .values(paused=paused, updated_at=func.now())
        )
        await session.commit()


def _code(text: str) -> str:
    return text.replace("`", "")[:40]
