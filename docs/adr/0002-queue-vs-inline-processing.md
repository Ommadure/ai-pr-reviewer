# ADR 0002: Process reviews on a job queue, not inside the webhook request

- **Status:** Accepted. The Celery and Redis implementation is superseded by [ADR 0016](0016-postgres-job-queue-in-process-worker.md); the decision to queue stands.
- **Date:** 2026-09-24

## Context
GitHub expects a webhook response within **10 seconds**. If it doesn't get one, it marks the delivery as failed. A review takes much longer: fetch the diff, make several LLM calls, validate, then post. That's commonly 20–120 seconds. Reviews can also fail for transient reasons (GitHub rate limits, LLM 429s, network errors) and should be retried without the webhook being resent.

## Decision
- The webhook handler does only fast, deterministic work: verify the HMAC signature, dedupe by `X-GitHub-Delivery`, record the delivery, enqueue a task, and return `202 Accepted`.
- The review runs in a **Celery worker**, with **Redis** as the broker.
- Celery is configured for at-least-once execution:
  - `task_acks_late=True`: the message is acknowledged only after the task finishes, so a crash mid-review means redelivery.
  - `task_reject_on_worker_lost=True`: the task is requeued if the worker process is killed.
  - `worker_prefetch_multiplier=1`: long tasks aren't hoarded by one worker.
  - Soft and hard time limits of 240 s and 300 s.
- At-least-once means a task **can run twice**, so tasks must be idempotent. That's guaranteed by a per-PR Redis lock, a stale-head check, run status, and comment fingerprints (Phase 3).
- Separate queues (`reviews`, `default`, `feedback`) stop slow reviews from delaying cheap tasks like slash-command replies.
- **Exactly one** Celery beat process runs scheduled jobs. Two would run every job twice.

## Consequences
- Webhook latency stays in the milliseconds whatever the review costs.
- Retries, backoff, and crash recovery come from Celery instead of hand-written code.
- The cost is two more processes (worker, beat) and Redis to operate, plus the discipline of writing idempotent tasks.

## Alternatives considered
- **FastAPI `BackgroundTasks`**: runs in the web process. The work is lost on restart or deploy, there are no retries, and web and review load can't scale separately.
- **RQ / Dramatiq / arq**: simpler, and arq is async-native. Celery was chosen for its mature `acks_late` semantics, beat scheduling, Flower monitoring, and because it's what most Python job postings list.
- **Cloud queue (SQS + Lambda)**: good at scale, but adds vendor lock-in and makes local development harder.
