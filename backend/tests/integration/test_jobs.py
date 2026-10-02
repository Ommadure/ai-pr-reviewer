"""The Postgres job queue and the in-process worker, against real Postgres (ADR 0016)."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.github.app_auth import GitHubAppAuth
from app.models import Job
from app.repositories import jobs
from app.repositories.jobs import ClaimedJob
from app.workers import tasks
from app.workers.runtime import WorkerContext
from app.workers.worker import Worker
from tests.integration.review_fakes import Sessions


async def _claim(sessionmaker: Sessions) -> ClaimedJob | None:
    async with sessionmaker() as session:
        return await jobs.claim(session)


async def _enqueue_reviews(sessionmaker: Sessions, *pr_ids: int) -> None:
    async with sessionmaker() as session:
        for index, pr_id in enumerate(pr_ids):
            jobs.enqueue_review(session, run_id=100 + index, pull_request_id=pr_id)
        await session.commit()


async def _job(sessionmaker: Sessions, job_id: int) -> Job:
    async with sessionmaker() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    return job


async def test_jobs_are_claimed_oldest_first_and_only_once(sessionmaker: Sessions) -> None:
    async with sessionmaker() as session:
        jobs.enqueue_command(session, {"command": "help", "n": 1})
        jobs.enqueue_command(session, {"command": "help", "n": 2})
        await session.commit()

    first, second = await _claim(sessionmaker), await _claim(sessionmaker)

    assert first is not None and second is not None
    assert (first.payload["n"], second.payload["n"]) == (1, 2)
    assert await _claim(sessionmaker) is None
    assert (await _job(sessionmaker, first.id)).status == "running"


async def test_one_review_per_pull_request_at_a_time(sessionmaker: Sessions) -> None:
    await _enqueue_reviews(sessionmaker, 1, 1, 2)  # two pushes to PR 1, one to PR 2

    pr1 = await _claim(sessionmaker)
    pr2 = await _claim(sessionmaker)  # PR 1's second job is passed over, not blocking

    assert pr1 is not None and pr2 is not None
    assert (pr1.payload["run_id"], pr2.payload["run_id"]) == (100, 102)
    assert await _claim(sessionmaker) is None
    async with sessionmaker() as session:
        await jobs.finish(session, pr1.id, "done")
    after = await _claim(sessionmaker)
    assert after is not None and after.payload["run_id"] == 101


async def test_the_database_refuses_two_running_jobs_with_one_lock_key(
    sessionmaker: Sessions,
) -> None:
    # The backstop for two processes claiming in the same instant.
    await _enqueue_reviews(sessionmaker, 1, 1)
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(update(Job).values(status="running"))


async def test_a_retry_waits_in_the_queue_and_counts_an_attempt(sessionmaker: Sessions) -> None:
    await _enqueue_reviews(sessionmaker, 1)
    job = await _claim(sessionmaker)
    assert job is not None
    async with sessionmaker() as session:
        await jobs.retry(session, job.id, delay_seconds=60, error="GitHub 502")

    assert await _claim(sessionmaker) is None  # not due yet
    async with sessionmaker() as session:
        due = await jobs.next_due_at(session)
    assert due is not None and 50 < (due - datetime.now(UTC)).total_seconds() <= 60
    stored = await _job(sessionmaker, job.id)
    assert (stored.status, stored.attempts, stored.last_error) == ("queued", 1, "GitHub 502")


async def test_jobs_a_dead_process_left_running_are_requeued(sessionmaker: Sessions) -> None:
    await _enqueue_reviews(sessionmaker, 1)
    job = await _claim(sessionmaker)
    assert job is not None
    async with sessionmaker() as session:
        # Started just now: still owned by a live worker.
        minute_ago = datetime.now(UTC) - timedelta(minutes=1)
        assert await jobs.requeue_orphans(session, started_before=minute_ago) == 0
        later = datetime.now(UTC) + timedelta(minutes=10)
        assert await jobs.requeue_orphans(session, started_before=later) == 1
        await session.commit()

    again = await _claim(sessionmaker)
    assert again is not None and (again.id, again.attempts) == (job.id, 1)


async def test_finished_jobs_are_deleted_after_retention(sessionmaker: Sessions) -> None:
    await _enqueue_reviews(sessionmaker, 1, 2)
    job = await _claim(sessionmaker)
    assert job is not None
    async with sessionmaker() as session:
        await jobs.finish(session, job.id, "done")
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        assert await jobs.delete_finished_before(session, tomorrow) == 1  # the queued one stays
        await session.commit()
        assert len((await session.scalars(select(Job))).all()) == 1


async def test_the_worker_runs_retries_and_fails_jobs(
    sessionmaker: Sessions,
    settings: Settings,
    github_auth: GitHubAppAuth,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def succeed(ctx: WorkerContext, job: ClaimedJob, wake: Callable[[], None]) -> None:
        return None

    async def flaky(ctx: WorkerContext, job: ClaimedJob, wake: Callable[[], None]) -> None:
        raise tasks.Retry(600, "GitHub 502")

    async def broken(ctx: WorkerContext, job: ClaimedJob, wake: Callable[[], None]) -> None:
        raise ValueError("bad payload")

    async def nothing(*args: object) -> None:
        return None

    monkeypatch.setattr(tasks, "HANDLERS", {"ok": succeed, "flaky": flaky, "broken": broken})
    monkeypatch.setattr(tasks, "poll_feedback", nothing)  # maintenance would call GitHub
    async with sessionmaker() as session:
        for kind in ("ok", "flaky", "broken"):
            session.add(Job(kind=kind, payload={}))
        await session.commit()

    async with httpx.AsyncClient() as llm_http:
        worker = Worker(WorkerContext(settings, sessionmaker, github_auth, llm_http))
        worker.start()
        try:
            expected = [("done", 0), ("queued", 1), ("failed", 1)]
            async with asyncio.timeout(10):
                # The worker's progress is database state, which no Event can signal.
                while await _states(sessionmaker) != expected:  # noqa: ASYNC110
                    await asyncio.sleep(0.05)
        finally:
            await worker.stop()

    ok, flaky_job, broken_job = await _all_jobs(sessionmaker)
    assert (flaky_job.attempts, flaky_job.last_error) == (1, "GitHub 502")
    assert broken_job.last_error == "ValueError: bad payload"
    assert ok.last_error is None


async def _all_jobs(sessionmaker: Sessions) -> list[Job]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(Job).order_by(Job.id))).all())


async def _states(sessionmaker: Sessions) -> list[tuple[str, int]]:
    return [(job.status, job.attempts) for job in await _all_jobs(sessionmaker)]
