# ADR 0010: Review-run lifecycle and idempotency

- **Status:** Accepted. The Redis lock and Celery redelivery are replaced by the job queue's `lock_key` and orphan requeue ([ADR 0016](0016-postgres-job-queue-in-process-worker.md)); the idempotency rules stand.
- **Date:** 2026-09-25

## Context
Celery runs tasks **at least once** (ADR 0002: `acks_late`, `reject_on_worker_lost`). The same review task can run twice after a worker crash or a broker redelivery. Pushes can also land while a review is running, and GitHub can reject a review. Double-posting, commenting on stale code, or leaving a check run spinning would all make the bot untrustworthy.

## Decision
**1. A run row exists before the task does.**
The webhook handler creates a `review_runs` row with status `queued` in the same transaction as the PR upsert, then enqueues `review_pull_request(run_id)` after commit. Consequences:
- Queued work is visible in the dashboard.
- Closing a PR marks its queued runs `skipped`.
- A duplicate task is recognised: if the run isn't `queued` or `running`, the task does nothing.

**2. Five layers against double-posting and stale posting.**

| Layer | Stops |
|---|---|
| Redis lock `lock:pr:{id}` (TTL 360 s, longer than the task's 300 s hard limit) | two reviews of one PR running concurrently. A busy task retries every 20 s, and those retries don't use up its failure-retry budget. |
| Run status re-checked **under the lock** | a redelivered task redoing a run that's already finished |
| Stale check at start (DB head, then GitHub's live head) | reviewing a commit that's no longer the head |
| Stale check **before posting** | posting comments on code that was just replaced. The results are still saved, with status `superseded`. |
| Fingerprints (ADR 0008) + partial unique index `(pull_request_id, fingerprint) WHERE posted` | the same problem being posted twice across runs. The database enforces it even if application logic slips. |

**3. Posting.**
- **One** `COMMENT` review carries the summary and all inline comments: one notification for the author, and one atomic write.
- If GitHub answers **422**, the whole batch was rejected because of one bad comment position. We then post comments **one by one**, record each rejected one with `drop_reason = github_rejected`, and post the summary as its own review.

**4. Failure handling.**
- **Transient errors** (GitHub rate limit, 5xx, network): the run returns to `queued` with its check run left open, and the task retries the whole run, reusing the stored `check_run_id`. Rate-limit retries wait exactly as long as GitHub says.
- **Permanent errors, or the final attempt:**
  - the run is marked `failed` with an `error_code`;
  - its check run completes as `neutral` with a friendly message, never `failure`;
  - any partial results (LLM usage) are still saved.
- **Beat `mark_stuck_runs`** (every 10 minutes) marks as `failed` (`timeout`):
  - runs `running` for more than 15 minutes (a hard-killed worker);
  - runs `queued` for more than 30 minutes (a lost enqueue).

## Consequences
- Re-running any task is safe, which is what makes at-least-once delivery acceptable.
- **One narrow gap:** a crash *between* GitHub accepting the review and our database commit. On retry, the run's comments are re-posted, because their fingerprints aren't recorded as posted yet. It's rare, visible, and harmless beyond duplication. Closing it fully would need a two-phase record ("posting" status plus reconciliation against GitHub's review list), which we defer.
