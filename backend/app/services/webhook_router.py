"""Decide what each webhook event means for us.

Only fast DB work happens here (upserts). Anything slow becomes a row in the jobs
table, in the same transaction, for the worker to pick up (ADR 0016).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.github import events
from app.repositories import (
    installations,
    jobs,
    pull_requests,
    repo_configs,
    repos,
    review_runs,
)
from app.services.commands import CommandJob, parse_command
from app.services.installations import ensure_repository, sync_installation

DeliveryStatus = Literal["processed", "queued", "ignored"]
REVIEW_TRIGGER_ACTIONS = {"opened", "reopened", "ready_for_review", "synchronize"}


@dataclass
class RouteOutcome:
    status: DeliveryStatus
    ignore_reason: str | None = None


def processed() -> RouteOutcome:
    return RouteOutcome("processed")


def ignored(reason: str) -> RouteOutcome:
    return RouteOutcome("ignored", ignore_reason=reason)


Handler = Callable[[AsyncSession, dict[str, Any]], Awaitable[RouteOutcome]]


async def route_event(
    session: AsyncSession, event: str, payload: dict[str, Any], *, bot_login: str
) -> RouteOutcome:
    if event == "ping":
        return processed()
    handler = HANDLERS.get(event)
    if handler is None:
        return ignored("unhandled_event")
    if event in BOT_GUARDED_EVENTS and _sent_by_bot(payload, bot_login):
        # Our own review/comment would otherwise trigger us again: an infinite loop.
        return ignored("bot_sender")
    return await handler(session, payload)


def _sent_by_bot(payload: dict[str, Any], bot_login: str) -> bool:
    sender = payload.get("sender") or {}
    return sender.get("type") == "Bot" or sender.get("login") == bot_login


# ---- installation events ----


async def handle_installation(session: AsyncSession, payload: dict[str, Any]) -> RouteOutcome:
    event = events.InstallationEvent.model_validate(payload)
    github_id = event.installation.id
    match event.action:
        case "created" | "new_permissions_accepted":
            await sync_installation(session, event.installation, event.repositories)
        case "deleted":
            await installations.mark_deleted(session, github_id)
        case "suspend":
            await installations.set_suspended(session, github_id, datetime.now(UTC))
        case "unsuspend":
            await installations.set_suspended(session, github_id, None)
        case _:
            return ignored("unhandled_action")
    return processed()


async def handle_installation_repositories(
    session: AsyncSession, payload: dict[str, Any]
) -> RouteOutcome:
    event = events.InstallationRepositoriesEvent.model_validate(payload)
    if event.action == "added":
        await sync_installation(session, event.installation, event.repositories_added)
    elif event.action == "removed":
        await repos.mark_removed(session, [r.id for r in event.repositories_removed])
    else:
        return ignored("unhandled_action")
    return processed()


# ---- pull request events ----


async def handle_pull_request(session: AsyncSession, payload: dict[str, Any]) -> RouteOutcome:
    event = events.PullRequestEvent.model_validate(payload)
    if event.action not in REVIEW_TRIGGER_ACTIONS | {"edited", "closed"}:
        return ignored("unhandled_action")

    # Always record the latest PR state, even when we won't review.
    repository_id = await ensure_repository(session, event.installation.id, event.repository)
    pr = await pull_requests.upsert_from_payload(
        session, repository_id=repository_id, payload=event.pull_request
    )

    if event.action == "closed":
        # Nobody needs a review of a closed PR: drop anything still waiting.
        await review_runs.skip_queued_for_pr(session, pr.id, "pr_closed")
        return processed()
    if event.action == "edited":
        return processed()

    repository = await repos.get(session, repository_id)
    installation = await installations.get_by_github_id(session, event.installation.id)
    if repository is not None and not repository.enabled:
        return ignored("repo_disabled")
    if installation is not None and installation.suspended_at is not None:
        return ignored("installation_suspended")
    if pr.paused:
        return ignored("pr_paused")
    if pr.draft and not await _drafts_allowed(session, repository_id):
        return ignored("draft")

    run = await review_runs.create_queued(
        session,
        pull_request_id=pr.id,
        trigger=event.action,
        # A push reviews only the new commits; the worker falls back to a full review
        # (and records why) if there's no previous review or history was rewritten.
        mode="incremental" if event.action == "synchronize" else "full",
        base_sha=pr.base_sha,
        head_sha=pr.head_sha,
    )
    jobs.enqueue_review(session, run_id=run.id, pull_request_id=pr.id)
    return RouteOutcome("queued")


async def _drafts_allowed(session: AsyncSession, repository_id: int) -> bool:
    """Uses the last cached .reviewpilot.yml: the webhook handler never calls GitHub.

    The worker re-checks against the freshly loaded config before reviewing.
    """
    cached = await repo_configs.latest(session, repository_id)
    return bool(cached and cached.parsed.get("review_drafts"))


# ---- PR conversation comments ----


async def handle_issue_comment(session: AsyncSession, payload: dict[str, Any]) -> RouteOutcome:
    event = events.IssueCommentEvent.model_validate(payload)
    if event.action != "created" or event.issue.pull_request is None:
        return ignored("not_a_pr_comment")
    command = parse_command(event.comment.body)
    if command is None:
        return ignored("not_a_command")
    # Only record and queue here: permission checks and replies need GitHub calls,
    # which never happen inside the webhook request.
    repository_id = await ensure_repository(session, event.installation.id, event.repository)
    job = CommandJob(
        installation_id=event.installation.id,
        repository_id=repository_id,
        repo_full_name=event.repository.full_name,
        pr_number=event.issue.number,
        comment_id=event.comment.id,
        author=event.comment.user.login,
        command=command,
    )
    jobs.enqueue_command(session, job.to_dict())
    return RouteOutcome("queued")


HANDLERS: dict[str, Handler] = {
    "installation": handle_installation,
    "installation_repositories": handle_installation_repositories,
    "pull_request": handle_pull_request,
    "issue_comment": handle_issue_comment,
}
# Installation events are always sent by a human (un)installing the App.
BOT_GUARDED_EVENTS = {"pull_request", "issue_comment"}
