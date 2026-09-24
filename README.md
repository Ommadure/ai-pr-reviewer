# ReviewPilot

A GitHub App that reviews pull requests with an LLM. It posts validated inline comments on the exact changed lines, and a React dashboard tracks review history, cost, and how useful the comments are.

> 🚧 In progress: **Phase 1** (webhook → hello review loop, awaiting the real-PR check) and **Phase 2** (review engine). The full README (demo, eval results, metrics) comes in Phase 7.

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

## Docs
- [GitHub App setup](docs/github-app-setup.md)
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
