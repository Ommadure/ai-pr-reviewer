# ReviewPilot

A GitHub App that reviews pull requests with an LLM. It posts validated inline comments on the exact changed lines, and a React dashboard tracks review history, cost, and how useful the comments are.

> 🚧 In progress: **Phase 4**: incremental reviews, slash commands, feedback. The full README (demo, eval results, metrics) comes in Phase 7.

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
  - [0009 Config from the default branch](docs/adr/0009-config-from-default-branch.md)
  - [0010 Review-run lifecycle and idempotency](docs/adr/0010-review-run-lifecycle.md)
  - [0011 Incremental reviews](docs/adr/0011-incremental-reviews.md)
