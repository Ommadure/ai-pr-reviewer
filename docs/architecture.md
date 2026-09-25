# Architecture

> Stub. It grows each phase. The full design lives in the spec (`reviewpilot_ai_pr_reviewer_prompt.md`, §3).

```mermaid
flowchart LR
    GH[GitHub] -- webhook --> API[FastAPI api]
    UI[React dashboard] -- /api/* --> API
    API -- enqueue --> R[(Redis<br/>broker · locks · token cache)]
    R --> W[Celery worker<br/>orchestrator]
    B[Celery beat<br/>exactly one] --> R
    W -- REST --> GH
    W -- structured review --> LLM[LLM provider<br/>Gemini → open-source]
    W --> PG[(PostgreSQL)]
    API --> PG
```

## Principles
1. **The webhook handler does almost nothing.** It verifies, dedupes, records, enqueues, and returns `202` ([ADR 0002](adr/0002-queue-vs-inline-processing.md)).
2. **The review engine (`app/review/`) is pure.** No HTTP, no DB. It's testable, usable from a CLI, and usable by the eval harness.
3. **The orchestrator owns all side effects.** GitHub calls, DB writes, check runs.
4. **ReviewPilot only ever comments.** It never approves or blocks a merge.
5. **All PR content is untrusted data.**
6. **Config is read from the default branch only.**

## Webhook → review flow (Phase 3)

```mermaid
sequenceDiagram
    participant GH as GitHub
    participant API as FastAPI /webhooks/github
    participant DB as Postgres
    participant Q as Redis (broker + locks)
    participant W as Celery worker
    participant LLM as LLM provider
    GH->>API: pull_request opened / synchronize (signed)
    API->>API: verify HMAC · dedupe delivery id
    API->>DB: upsert PR · INSERT review_runs (queued) · COMMIT
    API->>Q: review_pull_request(run_id)
    API-->>GH: 202 (milliseconds)
    Q->>W: task
    W->>Q: lock:pr:{id}
    W->>DB: run still queued? head unchanged? → running
    W->>GH: default-branch head → .reviewpilot.yml (cached per commit)
    W->>GH: live PR (stale/draft check) · create check run (in progress)
    W->>GH: list PR files
    W->>LLM: run_review(): chunks → comments → summary
    W->>DB: head moved meanwhile? → superseded, don't post
    W->>GH: ONE review (COMMENT) with inline comments (422 → one by one)
    W->>GH: complete check run (success / neutral)
    W->>DB: run metrics · llm_calls · review_comments · last_reviewed_sha
    W->>Q: unlock
```

## Review engine (Phase 2)

```mermaid
flowchart LR
    D[FileDiffs] --> F[filters<br/>lockfiles, binaries,<br/>generated, ignore_paths]
    F --> R[redaction<br/>secrets → REDACTED]
    R -- secrets on + lines --> S[secret comments]
    R --> P[prioritizer<br/>risky paths first]
    P --> C[chunker<br/>token budgets]
    C --> L[LLM per chunk<br/>JSON + 1 repair]
    L --> V[validator<br/>real lines only]
    S --> X[dedupe · rank · cap<br/>fingerprints]
    V --> X
    L -- file summaries --> M[LLM summary]
    X --> O[ReviewResult]
    M --> O
```

## Components
| Component | Where | Status |
|---|---|---|
| API (health, readiness) | `backend/app/main.py`, `app/api/v1/routes/health.py` | ✅ Phase 0 |
| Settings (fail-fast) | `backend/app/core/config.py` | ✅ Phase 0 |
| Celery app + reliability config | `backend/app/workers/celery_app.py` | ✅ Phase 0 |
| Webhook endpoint (verify, dedupe, route) | `app/api/v1/routes/webhooks.py`, `app/services/webhook_router.py` | ✅ Phase 1 |
| GitHub App auth + token cache | `app/github/app_auth.py` | ✅ Phase 1 |
| GitHub REST client (retries, rate limits, pagination) | `app/github/client.py` | ✅ Phase 1 |
| Tables: installations, repositories, pull_requests, webhook_deliveries | `app/models/`, `alembic/versions/` | ✅ Phase 1 |
| Review pipeline (lock, stale checks, config, posting, 422 fallback, persistence) | `app/services/orchestrator.py` | ✅ Phase 3 |
| Config from default branch, cached per commit | `app/services/config_loader.py` | ✅ Phase 3 |
| Review history tables: review_runs, llm_calls, review_comments, repo_configs | `app/models/review.py` | ✅ Phase 3 |
| Beat: mark_stuck_runs (10 min), cleanup_webhook_deliveries (daily) | `app/workers/` | ✅ Phase 3 |
| Incremental reviews on push, force-push fallback, PR-diff intersection | `app/review/incremental.py`, orchestrator `_plan_diff` | ✅ Phase 4 |
| Addressed / outdated detection | `app/review/incremental.py` | ✅ Phase 4 |
| Slash commands (permissions, rate limit, 👀 + reply) | `app/services/commands.py` | ✅ Phase 4 |
| Reaction polling (beat, 30 min) and usefulness metrics | `app/services/feedback.py`, `app/services/analytics.py` | ✅ Phase 4 |
| Review engine (parse, filter, redact, prioritise, chunk, LLM, validate, dedupe, summarise) | `app/review/`, `app/config/repo_config.py` | ✅ Phase 2 |
| Gemini provider + fake provider | `app/review/llm/` | ✅ Phase 2 |
| CLI | `python -m app.review.cli change.patch` | ✅ Phase 2 |
| Dashboard | `frontend/` | placeholder page |
