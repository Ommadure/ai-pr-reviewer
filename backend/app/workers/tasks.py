"""Celery tasks: thin synchronous wrappers around async service code."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from celery import Task

from app.github.client import GitHubError, GitHubRateLimited
from app.repositories import deliveries, review_runs
from app.services.commands import CommandJob, handle_command
from app.services.feedback import poll_feedback as poll_feedback_service
from app.services.orchestrator import (
    RetryableReviewError,
    ReviewBusy,
    RunOutcome,
    execute_review_run,
)
from app.workers.celery_app import celery_app
from app.workers.runtime import worker_context

log = structlog.get_logger()

LOCK_RETRY_SECONDS = 20
MAX_LOCK_RETRIES = 30  # ~10 minutes of waiting for an earlier review of the same PR
RUNNING_TIMEOUT = timedelta(minutes=15)
QUEUED_TIMEOUT = timedelta(minutes=30)
DELIVERY_RETENTION = timedelta(days=30)


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="app.workers.tasks.review_pull_request", bind=True, max_retries=5)
def review_pull_request(self: Task, run_id: int) -> str:
    logger = log.bind(run_id=run_id, attempt=self.request.retries + 1)
    final_attempt = self.request.retries >= self.max_retries
    try:
        outcome = asyncio.run(_review(run_id, final_attempt=final_attempt))
    except ReviewBusy as exc:
        # Another review of this PR is in progress; wait our turn (doesn't use up
        # the retry budget meant for real failures).
        logger.info("review.waiting_for_lock")
        raise self.retry(
            exc=exc, countdown=LOCK_RETRY_SECONDS, max_retries=MAX_LOCK_RETRIES
        ) from exc
    except RetryableReviewError as exc:
        countdown = exc.retry_after or 15 * 2**self.request.retries  # 15s, 30s, 60s, ...
        logger.warning("review.retry_scheduled", countdown=countdown, error=str(exc)[:200])
        raise self.retry(exc=exc, countdown=countdown) from exc
    logger.info("review.task_finished", status=outcome.status, reason=outcome.reason)
    return outcome.status


async def _review(run_id: int, *, final_attempt: bool) -> RunOutcome:
    async with worker_context() as ctx:
        return await execute_review_run(ctx.review_deps(), run_id, final_attempt=final_attempt)


@celery_app.task(name="app.workers.tasks.mark_stuck_runs")
def mark_stuck_runs() -> int:
    """Runs a crashed or hard-killed worker left behind become 'failed' (beat: every 10 min)."""
    return asyncio.run(_mark_stuck_runs())


async def _mark_stuck_runs() -> int:
    now = datetime.now(UTC)
    async with worker_context() as ctx, ctx.sessionmaker() as session:
        count = await review_runs.fail_stuck_runs(
            session, started_before=now - RUNNING_TIMEOUT, queued_before=now - QUEUED_TIMEOUT
        )
        await session.commit()
    if count:
        log.warning("maintenance.stuck_runs_failed", count=count)
    return count


@celery_app.task(name="app.workers.tasks.cleanup_webhook_deliveries")
def cleanup_webhook_deliveries() -> int:
    """Delete webhook_deliveries older than 30 days (beat: daily)."""
    return asyncio.run(_cleanup_webhook_deliveries())


async def _cleanup_webhook_deliveries() -> int:
    cutoff = datetime.now(UTC) - DELIVERY_RETENTION
    async with worker_context() as ctx, ctx.sessionmaker() as session:
        count = await deliveries.delete_received_before(session, cutoff)
        await session.commit()
    log.info("maintenance.deliveries_deleted", count=count)
    return count


@celery_app.task(name="app.workers.tasks.handle_pr_command", bind=True, max_retries=3)
def handle_pr_command(self: Task, job: dict[str, Any]) -> str:
    command = CommandJob(**job)
    try:
        outcome = asyncio.run(_handle_command(command))
    except GitHubRateLimited as exc:
        raise self.retry(exc=exc, countdown=exc.retry_after) from exc
    except GitHubError as exc:
        if exc.status_code < 500:
            raise
        raise self.retry(exc=exc, countdown=15 * 2**self.request.retries) from exc
    log.info("command.finished", command=command.command, outcome=outcome, pr=command.pr_number)
    return outcome


async def _handle_command(job: CommandJob) -> str:
    async with worker_context() as ctx:
        deps = ctx.command_deps(enqueue_review=lambda run_id: review_pull_request.delay(run_id))
        return await handle_command(deps, job)


@celery_app.task(name="app.workers.tasks.poll_feedback")
def poll_feedback() -> int:
    """Refresh 👍/👎 counts on posted comments (beat: every 30 min)."""
    return asyncio.run(_poll_feedback())


async def _poll_feedback() -> int:
    async with worker_context() as ctx:
        result = await poll_feedback_service(
            ctx.sessionmaker, ctx.github_auth, now=datetime.now(UTC)
        )
    return result.checked
