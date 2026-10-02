"""The background worker: an asyncio loop inside the API process (ADR 0016).

    webhook ─► jobs row (same transaction) ─► wake() ─► claim ─► handler ─► done
                                                               └► Retry ─► queued, due later

It sleeps until woken (a job was enqueued, or a running job finished) or until the
next retry is due, so an idle worker sends no queries and the database can scale to
zero. Maintenance runs on timers, and exactly once, because there is one process.
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import structlog

from app.core.logging import new_context
from app.repositories import jobs
from app.repositories.jobs import ClaimedJob
from app.workers import tasks
from app.workers.runtime import WorkerContext

log = structlog.get_logger()

CONCURRENCY = 2  # jobs at a time; each review makes its own (limited) parallel LLM calls
JOB_TIME_LIMIT_SECONDS = 300  # hard stop; a review times itself out at 240 s first
MAX_ATTEMPTS = 10  # safety net: a job that keeps killing the process stops coming back
ERROR_BACKOFF_SECONDS = 30  # the database is unreachable: try again shortly
# A job 'running' longer than its time limit belongs to a process that died.
ORPHANED_AFTER = timedelta(seconds=JOB_TIME_LIMIT_SECONDS + 60)


class Worker:
    def __init__(self, ctx: WorkerContext) -> None:
        self._ctx = ctx
        self._wakeup = asyncio.Event()
        self._running: set[asyncio.Task[None]] = set()
        self._loops: list[asyncio.Task[None]] = []

    def wake(self) -> None:
        self._wakeup.set()

    def start(self) -> None:
        schedule: list[tuple[timedelta, Callable[[], Awaitable[None]]]] = [
            (timedelta(minutes=10), lambda: tasks.mark_stuck(self._ctx, ORPHANED_AFTER)),
            (timedelta(minutes=30), lambda: tasks.poll_feedback(self._ctx)),
            (timedelta(days=1), lambda: tasks.cleanup(self._ctx)),
        ]
        self._loops = [asyncio.create_task(self._claim_loop())]
        self._loops += [asyncio.create_task(self._every(i, job)) for i, job in schedule]

    async def stop(self) -> None:
        """Cancel everything. An interrupted job stays 'running' and is requeued as an
        orphan after ORPHANED_AFTER, by this process's next start."""
        tasks_ = [*self._loops, *self._running]
        for task in tasks_:
            task.cancel()
        await asyncio.gather(*tasks_, return_exceptions=True)

    async def _claim_loop(self) -> None:
        while True:
            self._wakeup.clear()
            try:
                while len(self._running) < CONCURRENCY and (job := await self._claim()):
                    task = asyncio.create_task(self._execute(job))
                    self._running.add(task)
                    task.add_done_callback(self._job_done)
                timeout = await self._seconds_until_next_due()
            except Exception:
                log.exception("worker.claim_failed")
                timeout = ERROR_BACKOFF_SECONDS
            with suppress(TimeoutError):
                async with asyncio.timeout(timeout):  # None: until woken
                    await self._wakeup.wait()

    def _job_done(self, task: asyncio.Task[None]) -> None:
        self._running.discard(task)
        self.wake()  # a slot is free, and a job waiting on this one's lock_key may run

    async def _claim(self) -> ClaimedJob | None:
        async with self._ctx.sessionmaker() as session:
            return await jobs.claim(session)

    async def _seconds_until_next_due(self) -> float | None:
        async with self._ctx.sessionmaker() as session:
            due = await jobs.next_due_at(session)
        return None if due is None else max((due - datetime.now(UTC)).total_seconds(), 0)

    async def _execute(self, job: ClaimedJob) -> None:
        # Tasks copy the loop's context; start clean so logs name only this job.
        new_context(task=job.kind, job_id=job.id, attempt=job.attempts + 1)
        status, error, retry_in = "done", None, None
        try:
            if job.attempts >= MAX_ATTEMPTS:
                raise RuntimeError(f"gave up after {job.attempts} attempts")
            async with asyncio.timeout(JOB_TIME_LIMIT_SECONDS):
                await tasks.HANDLERS[job.kind](self._ctx, job, self.wake)
        except tasks.Retry as retry:
            retry_in, error = retry.delay, str(retry)
            log.warning("job.retry_scheduled", countdown=retry.delay, error=error[:200])
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
            log.exception("job.failed")
        try:
            async with self._ctx.sessionmaker() as session:
                if retry_in is not None:
                    await jobs.retry(session, job.id, delay_seconds=retry_in, error=error or "")
                else:
                    await jobs.finish(session, job.id, status, error)
        except Exception:
            # The job stays 'running'; mark_stuck requeues it, and handlers are idempotent.
            log.exception("job.finish_failed")

    async def _every(self, interval: timedelta, job: Callable[[], Awaitable[None]]) -> None:
        while True:
            try:
                await job()
            except Exception:
                log.exception("maintenance.failed")
            self.wake()  # e.g. requeued orphans are due now
            await asyncio.sleep(interval.total_seconds())
