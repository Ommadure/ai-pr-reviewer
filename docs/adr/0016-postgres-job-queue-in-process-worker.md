# ADR 0016: Postgres is the job queue, and the worker runs inside the API process

- **Status:** Accepted. It supersedes [ADR 0004](0004-asyncio-run-inside-celery.md), the Celery and Redis parts of [ADR 0002](0002-queue-vs-inline-processing.md), and the Upstash and Redis-budget parts of [ADR 0014](0014-deployment-topology.md).
- **Date:** 2026-09-27

## Context
- **The load is tiny.** ReviewPilot reviews a few PRs a day. Until now that took five moving parts: Celery, a beat scheduler, a Redis broker (Upstash), a worker container, and Flower for local debugging.
- **Upstash bills per command, and Celery is chatty.** ADR 0014 spent real effort keeping an idle worker under the free quota: a 30 s poll interval, turning off gossip, mingle and heartbeat, and no task events. That's tuning the tooling, not the product.
- **Celery tasks are synchronous.** Our code is async, so every task ran `asyncio.run` with a fresh engine, Redis client and HTTP clients (ADR 0004). None of that could be pooled.
- **Enqueuing could be lost.** The webhook committed its rows first, then published to Redis. A Redis hiccup in between left a run queued forever (until `mark_stuck_runs` failed it) and returned 503 to GitHub.
- **Postgres already knew everything.** `review_runs` records every run's status; Redis only carried a copy of "run 42 is due".
- **Production is one VM with one API container** (ADR 0015).

## Decision
- **A `jobs` table is the queue.**
  - A webhook inserts its job in the **same transaction** as the run it creates, so a job can't be lost between "saved" and "enqueued".
  - The worker claims a job with `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)`, so two claimers never get the same job.
  - Retries set `run_after` in the future (15 s, 30 s, 60 s, …, or GitHub's `retry_after`) and count `attempts`.
- **"One review per PR at a time" is a property of the queue.**
  - Review jobs carry `lock_key = "pr:<id>"`. A claim skips jobs whose key is already running.
  - A unique partial index (`lock_key WHERE status = 'running'`) is the backstop if two processes claim at the same instant.
  - The Redis lock, `ReviewBusy`, and the lock-wait retries are gone.
- **The worker is an asyncio task inside the API process** (`app/workers/worker.py`), started and stopped in FastAPI's lifespan.
  - It runs at most 2 jobs at a time on the API's event loop and shares the API's database pool. `asyncio.run` per task is gone.
  - It sleeps until woken. A route that enqueues calls `wake()`, a finished job wakes it (a slot is free, and the next job for that PR may run), and it also wakes when the next retry is due. **An idle worker sends no queries**, so Neon can still scale to zero.
- **Time limits come from asyncio.** The orchestrator stops a review after 240 s inside its own error handling, so the run is recorded as `timeout` and its check run is closed. The worker stops any job after 300 s.
- **Crash recovery comes from timestamps.** A job still `running` 6 minutes after it started belongs to a dead process (the hard limit is 5), so maintenance puts it back in the queue. That does what Celery's `acks_late` did. Handlers were already idempotent: a run that has already finished is skipped.
- **Scheduled jobs are timers in the same process**: stuck runs and orphaned jobs every 10 minutes, reaction polling every 30, cleanup daily. With one process, they run exactly once; that used to be the "only one beat" rule.
- **Redis's other jobs moved too:**
  - The manual-review rate limit (5 per PR per hour) counts `review_runs` rows with a manual trigger in the last hour. The rows already existed.
  - GitHub installation tokens are cached in memory. A restart costs one token request.
  - `/ready` checks only the database.

## Consequences
- **Removed:** Celery, kombu, the `redis` client, Upstash, the worker container, Flower, the beat container, `render.yaml` and its keep-warm ping. That's 2 direct dependencies (about 200 lines of `uv.lock`), one external account, and one container on the VM.
- **One process does both jobs.** A review that exhausts memory takes webhooks down with it. The API container's limit went from 384 MB to 512 MB, which is still less than the old API plus worker (896 MB).
- **CPU-bound steps now share the event loop.** Diff parsing, redaction and `difflib` all run on it. They take milliseconds on PRs within the review budget, but a pathological diff could delay API responses by that much.
- **Scaling out needs care.**
  - Running several API processes (`uvicorn --workers N`, or more containers) keeps the queue correct: `SKIP LOCKED` and the unique index hold across processes.
  - But each process would run the scheduled jobs, and wake only itself. If that ever matters, move the worker into its own container (same code, a small entry point) and add `LISTEN/NOTIFY` for wakeups.
- **Delay after a restart.** A deploy that interrupts a review leaves its job `running` until the orphan check, up to about 16 minutes. The review then restarts from the beginning.
- **Interview note:** "Why not Celery?" At this scale a broker is a second source of truth, and it has a real failure mode: committed but never enqueued. The database we already trust can be the queue. Celery becomes worth it with many workers, many queues and heavy throughput, or when the team already runs it.
