"""What each job kind does, and the periodic maintenance jobs (ADR 0016)."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog

from app.github.client import GitHubError, GitHubRateLimited
from app.repositories import deliveries, jobs, review_runs
from app.repositories.jobs import ClaimedJob
from app.services.commands import CommandJob, handle_command
from app.services.feedback import poll_feedback as poll_feedback_service
from app.services.orchestrator import RetryableReviewError, execute_review_run
from app.workers.runtime import WorkerContext

log = structlog.get_logger()

REVIEW_MAX_RETRIES = 5
COMMAND_MAX_RETRIES = 3
RUNNING_TIMEOUT = timedelta(minutes=15)
QUEUED_TIMEOUT = timedelta(minutes=30)
RETENTION = timedelta(days=30)  # webhook deliveries and finished jobs


class Retry(Exception):
    """Put the job back in the queue, due in `delay` seconds."""

    def __init__(self, delay: float, reason: str) -> None:
        super().__init__(reason)
        self.delay = delay


def backoff(attempts: int) -> float:
    return float(15 * 2**attempts)  # 15 s, 30 s, 60 s, ...


async def run_review(ctx: WorkerContext, job: ClaimedJob, wake: Callable[[], None]) -> None:
    final_attempt = job.attempts >= REVIEW_MAX_RETRIES
    try:
        outcome = await execute_review_run(
            ctx.review_deps(), job.payload["run_id"], final_attempt=final_attempt
        )
    except RetryableReviewError as exc:
        raise Retry(exc.retry_after or backoff(job.attempts), str(exc)) from exc
    log.info("review.job_finished", status=outcome.status, reason=outcome.reason)


async def run_command(ctx: WorkerContext, job: ClaimedJob, wake: Callable[[], None]) -> None:
    command = CommandJob(**job.payload)
    try:
        outcome = await handle_command(ctx.command_deps(wake), command)
    except GitHubError as exc:
        transient = isinstance(exc, GitHubRateLimited) or exc.status_code >= 500
        if not transient or job.attempts >= COMMAND_MAX_RETRIES:
            raise
        delay = exc.retry_after if isinstance(exc, GitHubRateLimited) else backoff(job.attempts)
        raise Retry(delay, str(exc)) from exc
    log.info("command.finished", command=command.command, outcome=outcome, pr=command.pr_number)


HANDLERS: dict[str, Callable[[WorkerContext, ClaimedJob, Callable[[], None]], Awaitable[None]]] = {
    jobs.REVIEW: run_review,
    jobs.COMMAND: run_command,
}


# ---- periodic ----


async def mark_stuck(ctx: WorkerContext, orphaned_after: timedelta) -> None:
    """Jobs a dead process left 'running' go back to the queue; review runs nothing
    will ever finish become 'failed'."""
    now = datetime.now(UTC)
    async with ctx.sessionmaker() as session:
        requeued = await jobs.requeue_orphans(session, started_before=now - orphaned_after)
        failed = await review_runs.fail_stuck_runs(
            session, started_before=now - RUNNING_TIMEOUT, queued_before=now - QUEUED_TIMEOUT
        )
        await session.commit()
    if requeued or failed:
        log.warning("maintenance.stuck", jobs_requeued=requeued, runs_failed=failed)


async def cleanup(ctx: WorkerContext) -> None:
    cutoff = datetime.now(UTC) - RETENTION
    async with ctx.sessionmaker() as session:
        deleted_deliveries = await deliveries.delete_received_before(session, cutoff)
        deleted_jobs = await jobs.delete_finished_before(session, cutoff)
        await session.commit()
    log.info("maintenance.cleanup", deliveries=deleted_deliveries, jobs=deleted_jobs)


async def poll_feedback(ctx: WorkerContext) -> None:
    """Refresh 👍/👎 counts on posted comments."""
    await poll_feedback_service(ctx.sessionmaker, ctx.github_auth, now=datetime.now(UTC))
