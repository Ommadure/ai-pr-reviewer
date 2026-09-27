"""jobs access: the Postgres job queue (ADR 0016).

Enqueue inside the caller's transaction, so a job commits together with the rows it
works on. Claim with FOR UPDATE SKIP LOCKED, so two claimers never take the same job.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Job

REVIEW, COMMAND = "review", "command"


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int  # failed attempts before this one


def enqueue_review(session: AsyncSession, *, run_id: int, pull_request_id: int) -> None:
    # One review per PR at a time: its lock_key is the PR.
    session.add(Job(kind=REVIEW, payload={"run_id": run_id}, lock_key=f"pr:{pull_request_id}"))


def enqueue_command(session: AsyncSession, payload: dict[str, Any]) -> None:
    session.add(Job(kind=COMMAND, payload=payload))


async def claim(session: AsyncSession) -> ClaimedJob | None:
    """Mark the next due job 'running' and return it, or None if none can run now.

    A job whose lock_key is already running is passed over; it becomes claimable when
    that job finishes. Commits (or rolls back) the session.
    """
    running = aliased(Job)
    next_id = (
        select(Job.id)
        .where(
            Job.status == "queued",
            Job.run_after <= func.now(),
            or_(
                Job.lock_key.is_(None),
                ~exists().where(running.status == "running", running.lock_key == Job.lock_key),
            ),
        )
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == next_id)
        .values(status="running", started_at=func.now(), updated_at=func.now())
        .returning(Job.id, Job.kind, Job.payload, Job.attempts)
    )
    try:
        row = (await session.execute(stmt)).one_or_none()
        await session.commit()
    except IntegrityError:
        # Another process claimed a job with the same lock_key in the same instant.
        await session.rollback()
        return None
    return ClaimedJob(row.id, row.kind, row.payload, row.attempts) if row else None


async def finish(session: AsyncSession, job_id: int, status: str, error: str | None = None) -> None:
    """status: done | failed."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=status,
            attempts=Job.attempts + (1 if status == "failed" else 0),
            last_error=error,
            updated_at=func.now(),
        )
    )
    await session.commit()


async def retry(session: AsyncSession, job_id: int, *, delay_seconds: float, error: str) -> None:
    """Back to the queue, due in delay_seconds; counts as a failed attempt."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status="queued",
            run_after=func.now() + timedelta(seconds=delay_seconds),
            attempts=Job.attempts + 1,
            last_error=error[:2000],
            updated_at=func.now(),
        )
    )
    await session.commit()


async def requeue_orphans(session: AsyncSession, *, started_before: datetime) -> int:
    """Jobs left 'running' by a process that died (crash, OOM, deploy) run again."""
    result = await session.execute(
        update(Job)
        .where(Job.status == "running", Job.started_at < started_before)
        .values(
            status="queued",
            attempts=Job.attempts + 1,
            last_error="requeued: the worker stopped during this job",
            updated_at=func.now(),
        )
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


async def next_due_at(session: AsyncSession) -> datetime | None:
    """When the earliest *future* queued job becomes due (retries wait in the queue)."""
    due: datetime | None = await session.scalar(
        select(func.min(Job.run_after)).where(Job.status == "queued", Job.run_after > func.now())
    )
    return due


async def delete_finished_before(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(
        delete(Job).where(Job.status.in_(("done", "failed")), Job.updated_at < cutoff)
    )
    return result.rowcount or 0  # type: ignore[attr-defined]
