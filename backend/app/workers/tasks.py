"""Celery tasks: thin synchronous wrappers around async service code."""

import asyncio

import httpx
import structlog
from celery import Task

from app.github.client import GitHubRateLimited
from app.services.orchestrator import HelloReviewResult, run_hello_review
from app.workers.celery_app import celery_app
from app.workers.runtime import worker_context

log = structlog.get_logger()


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="app.workers.tasks.review_pull_request", bind=True, max_retries=5)
def review_pull_request(
    self: Task, pull_request_id: int, head_sha: str, trigger: str, mode: str = "full"
) -> str:
    logger = log.bind(pull_request_id=pull_request_id, head_sha=head_sha, trigger=trigger)
    try:
        result = asyncio.run(_review(pull_request_id, head_sha))
    except GitHubRateLimited as exc:
        # Wait exactly as long as GitHub told us to, not a guessed backoff.
        logger.warning("review.rate_limited", retry_after=exc.retry_after)
        raise self.retry(exc=exc, countdown=exc.retry_after) from exc
    except httpx.TransportError as exc:
        countdown = 10 * 2.0**self.request.retries  # 10s, 20s, 40s, ...
        logger.warning("review.network_error", countdown=countdown)
        raise self.retry(exc=exc, countdown=countdown) from exc
    logger.info("review.finished", status=result.status)
    return result.status


async def _review(pull_request_id: int, head_sha: str) -> HelloReviewResult:
    async with worker_context() as ctx:
        return await run_hello_review(
            ctx.sessionmaker,
            ctx.github_auth,
            pull_request_id=pull_request_id,
            head_sha=head_sha,
        )
