"""review_runs / llm_calls / review_comments access."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import LLMCall, PullRequest, Repository, ReviewCommentRecord, ReviewRun
from app.review.incremental import CommentStatus, OpenComment
from app.review.models import LLMCallRecord

ACTIVE_STATUSES = ("queued", "running")


def to_money(value: float) -> Decimal:
    return Decimal(str(round(value, 6)))


async def create_queued(
    session: AsyncSession,
    *,
    pull_request_id: int,
    trigger: str,
    mode: str,
    base_sha: str,
    head_sha: str,
) -> ReviewRun:
    run = ReviewRun(
        pull_request_id=pull_request_id,
        trigger=trigger,
        mode=mode,
        base_sha=base_sha,
        head_sha=head_sha,
        status="queued",
    )
    session.add(run)
    await session.flush()  # assigns run.id without committing
    return run


async def get_with_context(session: AsyncSession, run_id: int) -> ReviewRun | None:
    """Run + PR + repository + installation, loaded in one query."""
    result = await session.scalars(
        select(ReviewRun)
        .where(ReviewRun.id == run_id)
        .options(
            joinedload(ReviewRun.pull_request)
            .joinedload(PullRequest.repository)
            .joinedload(Repository.installation)
        )
    )
    return result.one_or_none()


async def skip_queued_for_pr(session: AsyncSession, pull_request_id: int, reason: str) -> int:
    result = await session.execute(
        update(ReviewRun)
        .where(ReviewRun.pull_request_id == pull_request_id, ReviewRun.status == "queued")
        .values(status="skipped", skip_reason=reason, finished_at=func.now(), updated_at=func.now())
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


async def fail_stuck_runs(
    session: AsyncSession, *, started_before: datetime, queued_before: datetime
) -> int:
    """Runs no task will ever finish: 'running' past any time limit (the worker died
    hard), or 'queued' for far too long (the enqueue itself was lost)."""
    result = await session.execute(
        update(ReviewRun)
        .where(
            or_(
                and_(ReviewRun.status == "running", ReviewRun.started_at < started_before),
                and_(ReviewRun.status == "queued", ReviewRun.created_at < queued_before),
            )
        )
        .values(
            status="failed",
            error_code="timeout",
            error_message="Marked failed by mark_stuck_runs: no progress within the time limit.",
            finished_at=func.now(),
            updated_at=func.now(),
        )
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


async def posted_fingerprints(session: AsyncSession, pull_request_id: int) -> set[str]:
    result = await session.scalars(
        select(ReviewCommentRecord.fingerprint).where(
            ReviewCommentRecord.pull_request_id == pull_request_id,
            ReviewCommentRecord.posted.is_(True),
            ReviewCommentRecord.fingerprint.is_not(None),
        )
    )
    return {fingerprint for fingerprint in result if fingerprint}


def llm_call_rows(run_id: int, records: Sequence[LLMCallRecord]) -> list[LLMCall]:
    return [
        LLMCall(
            review_run_id=run_id,
            purpose=record.purpose,
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_usd=to_money(record.cost_usd),
            latency_ms=record.latency_ms,
            status=record.status,
            error=record.error,
        )
        for record in records
    ]


async def open_posted_comments(session: AsyncSession, pull_request_id: int) -> list[OpenComment]:
    rows = await session.execute(
        select(
            ReviewCommentRecord.id,
            ReviewCommentRecord.path,
            ReviewCommentRecord.line,
            ReviewCommentRecord.start_line,
            ReviewCommentRecord.code_snapshot,
            ReviewRun.head_sha,
        )
        .join(ReviewRun, ReviewRun.id == ReviewCommentRecord.review_run_id)
        .where(
            ReviewCommentRecord.pull_request_id == pull_request_id,
            ReviewCommentRecord.posted.is_(True),
            ReviewCommentRecord.status == "open",
        )
    )
    return [
        OpenComment(
            row.id, row.path, row.line, row.start_line, row.code_snapshot or "", row.head_sha
        )
        for row in rows
    ]


async def set_comment_statuses(session: AsyncSession, changes: dict[int, CommentStatus]) -> None:
    for comment_id, status in changes.items():
        await session.execute(
            update(ReviewCommentRecord)
            .where(ReviewCommentRecord.id == comment_id)
            .values(status=status, updated_at=func.now())
        )


async def latest_summary(session: AsyncSession, pull_request_id: int) -> ReviewRun | None:
    """The most recent completed run that produced a PR summary."""
    result = await session.scalars(
        select(ReviewRun)
        .where(
            ReviewRun.pull_request_id == pull_request_id,
            ReviewRun.status == "completed",
            ReviewRun.summary.is_not(None),
        )
        .order_by(ReviewRun.finished_at.desc())
        .limit(1)
    )
    return result.one_or_none()
