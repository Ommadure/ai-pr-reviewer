# ReviewPilot

A GitHub App that reviews pull requests with an LLM. When a PR is opened or updated, ReviewPilot reads the diff and posts **one** review with inline comments on the exact changed lines. Each comment is validated against the diff, stripped of anything unsafe, and deduplicated across pushes. A dashboard shows every review's cost, latency and whether people found it useful. An offline eval harness measures precision, recall and fix quality on 31 labelled PRs plus a held-out set, so prompt changes are judged on evidence.

**Stack:** FastAPI · Celery + Redis · PostgreSQL · Gemini (provider-agnostic) · React + TanStack Query · deployed for $0 on an Oracle Cloud VM (Docker + Caddy), Neon, Upstash and Vercel.

## Demo

<!-- GIF: record opening a PR on the playground repo, the "ReviewPilot" check running, the
review appearing, then the dashboard (Linux: Kooha or Peek; shrink with gifski). Save it as
docs/demo.gif and add ![ReviewPilot reviewing a pull request](docs/demo.gif) here. -->

### A real review
This is ReviewPilot's first production review, on a test PR that adds a user lookup. The code has three bugs, planted on purpose:

```python
def find_user(db_path, name):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"SELECT * FROM users WHERE name = '{name}'").fetchall()
    return rows[0]
```

The check run finished in **10 seconds** as `neutral`: it flags problems but never blocks a merge. It posted one review with these inline comments (excerpts, quoted as posted):

| Line | Comment | One-click fix |
| --- | --- | --- |
| 6 | 🚨 **Critical · Security: SQL injection vulnerability in user lookup.** "User input `name` is directly interpolated into the SQL query string using f-strings… Use parameterized queries instead." | `rows = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchall()` |
| 7 | 🔴 **High · Bug: IndexError on empty user lookup.** "Accessing `rows[0]` directly will raise an `IndexError` if no user matches the given name." | none |
| 5 | 🟠 **Medium · Bug: SQLite database connection is leaked.** "The SQLite connection created via `sqlite3.connect` is never closed…" | a rewrite of the function starting `with sqlite3.connect(db_path) as conn:` **(wrong: see below)** |

The summary rated the PR **high** risk, and each comment ends with its confidence and a 👍/👎 prompt. The reactions feed the dashboard's helpfulness numbers.

**What it got wrong, and what changed because of it.** The third fix doesn't work: `sqlite3`'s `with` block commits or rolls back, but never closes the connection. That one mistake led to four changes:
1. **Eval cases** that reproduce it every time;
2. **A harness that scores fixes as well as findings**;
3. **Prompt v4**, which fixes it, including on a held-out `psycopg2` case the prompt never mentions;
4. **An engine check**, added after replaying every stored suggestion showed that about 1 in 4 wouldn't apply cleanly, for example by deleting a `raise` or duplicating a loop body.

The whole story, with numbers, is in [evals/RESULTS.md](evals/RESULTS.md).

## Architecture

```mermaid
flowchart LR
    subgraph GitHub
        GH[Pull requests<br/>check runs]
    end
    subgraph Vercel
        UI[React dashboard]
    end
    subgraph VM[Oracle Cloud VM · Docker Compose]
        C[Caddy<br/>HTTPS]
        API[FastAPI API<br/>webhooks · dashboard API]
        W[Celery worker<br/>orchestrator + the one beat]
        C --> API
    end
    GH -- signed webhook --> C
    UI -- /api/* rewrite --> C
    API -- enqueue run_id --> R[(Upstash Redis<br/>broker · locks · caches)]
    R --> W
    W -- one COMMENT review --> GH
    W -- structured JSON --> LLM[Gemini]
    API --> PG[(Neon Postgres)]
    W --> PG
```

```mermaid
sequenceDiagram
    participant GH as GitHub
    participant API as API /webhooks/github
    participant DB as Postgres
    participant Q as Redis
    participant W as Worker
    participant LLM as LLM
    GH->>API: pull_request opened / synchronize (signed)
    API->>API: verify HMAC · dedupe delivery id
    API->>DB: upsert PR · review_run (queued) · commit
    API->>Q: review_pull_request(run_id)
    API-->>GH: 202 in milliseconds
    Q->>W: task
    W->>Q: per-PR lock
    W->>GH: config from default branch · PR files · check run (in progress)
    W->>LLM: redacted, chunked diff → JSON comments
    W->>W: validate lines · sanitize · dedupe (fingerprints)
    W->>GH: ONE review with inline comments · complete check run
    W->>DB: run, LLM calls, comments, cost, latency
```

## How it works
- **Webhook ingestion.** Verify the HMAC on the raw body, dedupe by delivery id, record, enqueue, return `202`. Nothing slow happens in the request ([ADR 0002](docs/adr/0002-queue-vs-inline-processing.md)).
- **Orchestrator.** One Celery task per run holds a per-PR lock, checks the head didn't move, and owns every side effect: GitHub calls, DB writes, the check run ([ADR 0010](docs/adr/0010-review-run-lifecycle.md)).
- **Pure engine.** Filter → redact secrets → prioritise → chunk to a token budget → LLM → validate → rank. No HTTP or DB inside, so the CLI and the eval harness run the exact same code ([ADR 0007](docs/adr/0007-pure-review-engine.md)).
- **Validation.** A comment survives only if its lines are real added lines in this diff. Output is sanitised (no @mentions, images, HTML or foreign links), and only ever posted as `COMMENT`, never approve or block ([ADR 0006](docs/adr/0006-comment-only-reviews.md)).
- **Dedupe and incremental reviews.** Fingerprints of path, category and code (not line numbers) stop repeats across pushes. A new push reviews only what changed since the last review, falling back to a full review after a force-push ([ADR 0008](docs/adr/0008-comment-fingerprints.md), [ADR 0011](docs/adr/0011-incremental-reviews.md)).

## Production metrics

From the private test repository `Ommadure/reviewpilot-playground`, via the dashboard. So far these come from the local deployment; this table will be refreshed from production.

| Reviews run | Avg cost per review | p95 latency | Helpful rate |
| --- | --- | --- | --- |
| 3 | ₹0 (Gemini free tier) | 58.7 s | 100% (2 of 2 posted comments: 👍 or fixed) |

## Security

The full checklist, with code and test evidence for every item, is in [docs/security.md](docs/security.md). The short version:
- **Webhooks:** verified with a constant-time HMAC.
- **Least-privilege App:** it only ever comments.
- **Secrets:** env-only, never logged. Users' GitHub tokens are Fernet-encrypted.
- **Dashboard sessions:** `HttpOnly; Secure; SameSite=Lax` cookies. Tenancy is enforced in the data layer and tested on every endpoint.
- **Before and after the LLM:** secrets are redacted before any LLM call, and prompt-injection defences run on both sides of it.
- **Caps:** per-run token and cost caps, plus rate-limited, permission-checked slash commands.
- **Dependencies:** audited in CI every week.

## Deploying

Everything runs on free tiers:
- **Backend:** one Oracle Cloud Always Free VM running the API, the worker with its one beat, and Caddy for HTTPS (`deploy/oracle/`).
- **Data:** Neon Postgres and Upstash Redis, both over TLS.
- **Dashboard:** Vercel (`frontend/vercel.json`).

The step-by-step guide is in [docs/deploy.md](docs/deploy.md). The reasoning is in [ADR 0015](docs/adr/0015-oracle-always-free-vm.md) (the VM) and [ADR 0014](docs/adr/0014-deployment-topology.md) (regions, the measured Redis command budget, the Vercel rewrite). A Render alternative with a paid worker lives in `render.yaml`.

## Local setup

Prerequisites: Docker with Compose v2.24+, [uv](https://docs.astral.sh/uv/), and Node ≥ 22.22.2.

```bash
cp backend/.env.example backend/.env      # fill in GitHub App values (docs/github-app-setup.md)
docker compose up --build                 # postgres, redis, api, worker, beat, flower
curl localhost:8000/api/v1/ready          # {"status":"ok",...}
cd frontend && npm install && npm run dev # http://localhost:5173
```

Backend checks, run from `backend/`:
```bash
uv sync && uv run ruff check . && uv run mypy app tests && uv run pytest   # needs `docker compose up -d postgres`
```

## Review a diff from the terminal

The review engine runs without GitHub or a database:

```bash
cd backend
git -C .. diff main > /tmp/change.patch
uv run python -m app.review.cli /tmp/change.patch                    # uses LLM_* from backend/.env
uv run python -m app.review.cli /tmp/change.patch --provider fake    # offline, no API key
```

## Dashboard

Sign in with GitHub to see only the installations you can access on GitHub (ADR 0012). It has:
- **Overview:** reviews, comments, helpful rate, cost (₹ with `USD_TO_INR`) and p95 latency.
- **Repositories:** turn automatic reviews on or off, and see each repo's config status.
- **Pull requests:** every review run on a timeline, plus **Review again**.
- **Run detail:** summary, comments grouped by file with links to GitHub, the comments validation dropped (with reasons), skipped files, and every LLM call with its tokens, cost and time.
- **Analytics:** 7, 30 or 90 days, per repository.

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173, /api proxied to :8000
```

After changing the API, regenerate the typed client:
```bash
cd backend && uv run python scripts/export_openapi.py && cd ../frontend && npm run gen:api
```

## Slash commands

Comment on a pull request:

| Command | What it does | Who |
|---|---|---|
| `/reviewpilot review` | Full re-review of the latest commit (max 5 per PR per hour) | write, maintain, admin |
| `/reviewpilot summary` | Re-post the latest PR summary | anyone |
| `/reviewpilot pause` | Stop automatic reviews on this PR | write, maintain, admin |
| `/reviewpilot resume` | Turn automatic reviews back on | write, maintain, admin |
| `/reviewpilot help` | List the commands | anyone |

ReviewPilot reacts with 👀 immediately and replies with the outcome.

## Configuration

Put `.reviewpilot.yml` on the repository's **default branch** (it's never read from PR branches; see [ADR 0009](docs/adr/0009-config-from-default-branch.md)). All keys are optional:

```yaml
version: 1
enabled: true                # false = skip this repository
review_drafts: false
post_when_clean: false       # post a summary even when there are no comments
comment_on_context_lines: false
min_severity: low            # critical | high | medium | low | info
min_confidence: 0.6
max_comments: 15
focus: []                    # e.g. [bug, security]; empty = every category
ignore_paths: ["docs/**", "**/*.generated.ts"]
custom_rules:                # up to 20 rules, 300 characters each
  - "We use Pydantic v2; flag v1-style validators."
summary_language: en
```

## How usefulness is measured

ReviewPilot polls 👍/👎 reactions on its comments every 30 minutes (bot reactions don't count) and notices when an author changes the code a comment flagged.

- **Posted:** comments actually posted on GitHub (dropped comments don't count).
- **Helpful:** posted comments with at least one 👍, *or* whose flagged code the author later changed ("addressed").
- **Negative:** posted comments with at least one 👎.
- **Helpful rate** = helpful ÷ posted. **Negative rate** = negative ÷ posted.

A comment with mixed reactions counts in both, so the two rates don't add up to 100%. Both are also reported by category and by severity.

## Evaluation

An offline harness measures the review engine on 31 pull requests (26 with planted bugs, 5 clean) in Python, TypeScript, JavaScript and SQL, plus a small held-out set. It reports precision, recall, F1, severity agreement, known-wrong suggested fixes, cost and latency. The full runs, what changed between prompt versions, and the reasoning are in [evals/RESULTS.md](evals/RESULTS.md). How to run it is in [evals/README.md](evals/README.md).

Current dataset (26 bugs + 5 clean), two runs each:

| Prompt · model | Precision | Recall | FP per clean PR | Severity exact | Known-wrong fixes | p50 latency |
| --- | --- | --- | --- | --- | --- | --- |
| v3 · gemini-3.5-flash-lite | 96% | 92% | 0.0 | 71–79% | 1 per run | 1.9 s |
| **v4 · gemini-3.5-flash-lite (shipped)** | 100% | 96–100% | 0.0 | 81–84% | 0 | 1.9 s |

v4 exists because of a production review: the reviewer "fixed" a leaked SQLite connection with `with sqlite3.connect(...)`, which never closes it. The harness now scores suggested fixes as well as findings, and v4 closes that gap, including on a held-out `psycopg2` case the prompt never mentions. The trade-off: v4 offers fewer one-click suggestions (about 37% of found bugs, against 52%). Every run cost $0 on the free tier. Earlier prompt versions on the Phase 6 dataset are in RESULTS.md.

```bash
cd backend
uv run python ../evals/run_eval.py --prompt v4 --model gemini-3.5-flash-lite --pause 4 --retry-errors 2
uv run python ../evals/run_eval.py --prompt v4 --cases-dir ../evals/heldout
uv run python ../evals/compare.py ../evals/results/A.json ../evals/results/B.json
```

## Docs
- [GitHub App setup](docs/github-app-setup.md)
- [Deploying](docs/deploy.md)
- [Security review](docs/security.md)
- [Architecture](docs/architecture.md)
- ADRs:
  - [0001 Record decisions](docs/adr/0001-record-architecture-decisions.md)
  - [0002 Queue vs inline processing](docs/adr/0002-queue-vs-inline-processing.md)
  - [0003 Gemini first, provider-agnostic LLM](docs/adr/0003-gemini-first-provider-agnostic-llm.md)
  - [0004 `asyncio.run` inside Celery](docs/adr/0004-asyncio-run-inside-celery.md)
  - [0005 GitHub App over OAuth App](docs/adr/0005-github-app-over-oauth-app.md)
  - [0006 Comment-only reviews](docs/adr/0006-comment-only-reviews.md)
  - [0007 Pure review engine](docs/adr/0007-pure-review-engine.md)
  - [0008 Comment fingerprints](docs/adr/0008-comment-fingerprints.md)
  - [0009 Config from the default branch](docs/adr/0009-config-from-default-branch.md)
  - [0010 Review-run lifecycle and idempotency](docs/adr/0010-review-run-lifecycle.md)
  - [0011 Incremental reviews](docs/adr/0011-incremental-reviews.md)
  - [0012 Dashboard login, sessions and tenancy](docs/adr/0012-dashboard-auth-and-tenancy.md)
  - [0013 Eval matching rule](docs/adr/0013-eval-matching-rule.md)
  - [0014 Deployment topology](docs/adr/0014-deployment-topology.md)
  - [0015 Oracle Cloud Always Free VM](docs/adr/0015-oracle-always-free-vm.md)
