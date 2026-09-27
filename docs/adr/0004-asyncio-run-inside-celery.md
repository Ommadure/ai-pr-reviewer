# ADR 0004: Run async code inside Celery tasks with `asyncio.run`

- **Status:** Superseded by [ADR 0016](0016-postgres-job-queue-in-process-worker.md): the worker now runs on the API's event loop.
- **Date:** 2026-09-24

## Context
The API is async (FastAPI, SQLAlchemy async with asyncpg, httpx.AsyncClient). The review pipeline reuses that async code: the GitHub client, the repositories layer, and later the LLM providers with concurrent calls. Celery 5, however, runs **synchronous** task functions.

## Decision
- Each task is a thin sync wrapper that calls `asyncio.run(async_body(...))`. This creates a fresh event loop per task and closes it at the end.
- Async resources are **created and closed inside that loop, per task** (`app/workers/runtime.py`):
  - a SQLAlchemy engine with `NullPool`,
  - a Redis client,
  - an `httpx.AsyncClient`.
- The API's cached engine and Redis client are never used in workers. Connection pools are bound to the event loop that created them, and reusing one from another loop fails with "attached to a different loop" errors.

## Consequences
- The same async service code runs in the API, the workers, tests, and later the CLI.
- Workers can run LLM calls concurrently with `asyncio.gather` plus a semaphore (Phase 2).
- **Cost:** each task opens fresh DB, Redis, and HTTP connections, adding a few milliseconds. That's negligible next to a review that takes tens of seconds.
- `NullPool` means one Postgres connection per task while it runs. With `--concurrency=2` that's two connections per worker, which is easy to reason about.

## Alternatives considered
- **Write the worker path synchronously** (sync SQLAlchemy, `httpx.Client`): we'd maintain two versions of every client, and couldn't make concurrent LLM calls easily.
- **One long-lived event loop per worker process** (e.g. started in `worker_process_init`): fewer connections, but subtle lifecycle bugs around forked processes. Worth revisiting only if connection setup shows up in profiles.
- **An async-native queue (arq, Taskiq):** a clean fit, but see ADR 0002 for why we chose Celery.
