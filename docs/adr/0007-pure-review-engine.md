# ADR 0007: A pure, GitHub-independent review engine

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
The review logic has many parts: diff parsing, filtering, secret redaction, prioritisation, chunking, prompting, LLM calls, validation, dedupe, and summarising. Three different callers need it:
1. **the Celery orchestrator**, which reviews real PRs (Phase 3);
2. **a CLI**, for developing and demoing against local `.patch` files;
3. **the evaluation harness**, which scores precision and recall on planted-bug cases (Phase 6).

If the engine called GitHub or the database itself, every one of these callers, and every test, would have to mock HTTP and SQL.

## Decision
`app/review/` is a pure module:
- **Inputs:**
  - `list[FileDiff]`, the parsed diff;
  - `RepoConfig`;
  - `PRContext`, the title and description;
  - an injected `LLMProvider`;
  - a `ReviewBudget` and a `PriceTable`;
  - the fingerprints already posted on this PR.
- **Output:** a `ReviewResult` holding:
  - the kept comments;
  - the dropped comments, each with a reason;
  - the summary;
  - the skipped files, each with a reason;
  - one record per LLM call (tokens, cost, latency);
  - any errors.
- **No HTTP and no DB inside.** The only I/O is through the injected provider. Callers do all side effects: fetching the diff, posting reviews, saving rows.
- **Partial failure is data, not an exception.** A chunk whose LLM call fails, even after one repair attempt, is recorded in `errors` and `skipped_files`, and the rest of the review completes.

## Consequences
- Unit tests run the whole pipeline in about a second with `FakeLLMProvider`, which can script invalid JSON, hallucinated lines and outages. The engine has over 90% coverage.
- `python -m app.review.cli change.patch` reviews anything you can `git diff`.
- The eval harness can compare prompt versions and models without GitHub.
- **Cost:** the orchestrator has to translate between GitHub's shapes and the engine's. `parse_patch()` already accepts GitHub's per-file `patch` field directly.
