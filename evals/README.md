# Evaluation harness

Measures the review engine on pull requests with **planted bugs**. It reports how many bugs it finds (recall), how many of its comments are real (precision), what that costs, and how long it takes. It calls the pure engine directly: no GitHub, no database.

## Dataset (`cases/`)
- **26 bug cases** and **5 clean cases**, in Python, TypeScript/TSX, JavaScript and SQL.
- **Bugs covered:** SQL injection, missing `await` (Python and TS), off-by-one (Python and JS), a hardcoded production credential, an N+1 query, an unhandled promise rejection, a mutable default argument, a check-then-act race, missing input validation, a resource leak, a swallowed exception, XSS via `dangerouslySetInnerHTML`, a stale React hook dependency, money rounding, `is` vs `==`, an `UPDATE` without `WHERE`, a `NOT NULL` column with no default, an inverted expiry check, numeric `sort()`, path traversal, `forEach(async …)`, floor division, command injection, and `with sqlite3.connect()` mistaken for a closing context manager (found in production).
- **Clean cases** are well-written changes with nothing to flag. Any comment on them is a false positive.
- **Held-out set (`heldout/`):** cases kept out of prompt tuning, to check that a prompt change learned a rule rather than memorised the example that motivated it. Run with `--cases-dir ../evals/heldout`. Don't tune a prompt against these; add new held-out cases when you use them up.

Each case is `cases/<id>/diff.patch` (a unified diff) plus `cases/<id>/case.yaml`:

```yaml
id: py-sql-injection-01
language: python
description: Product search builds SQL with an f-string
title: Add product search            # shown to the model as the PR title
planted_bugs:
  - path: app/catalog/search.py
    lines: [15, 17]
    category: security
    severity: critical
    description: "SQL injection: search term interpolated into the query"
    accept_categories: []           # optional, only for genuinely ambiguous bugs
    bad_suggestions: []             # optional regexes: a suggested fix matching one is wrong
```

Loading checks every planted bug against the diff, so a line number that isn't an added line fails loudly. It also compiles every `bad_suggestions` pattern, so a broken regex fails loudly too.

## Matching rule ([ADR 0013](../docs/adr/0013-eval-matching-rule.md))
A comment finds a planted bug when all three hold:
- the file path is the same;
- its line is within the bug's range **±2** (or its range overlaps the bug);
- its category is compatible. `bug` and `error_handling` count as the same, plus any per-case `accept_categories`.

Matching is one-to-one. Every other comment is a false positive: a `duplicate`, a `wrong_category`, or `unplanted`.

**Finding a bug isn't fixing it.** When a found bug lists `bad_suggestions` and its comment carries a `suggestion` block, the report checks the suggested code against those patterns. A match is a **known-wrong fix**. It still counts as found (detection and fix quality are separate metrics), and the report lists it under "What went wrong".

**Fixes that fit.** The engine drops a suggestion that wouldn't apply cleanly: one that repeats code outside its lines, silently deletes a line, changes the bracket structure, or contains copied diff markers. The report shows **Fixes offered on found bugs** (the one-click fixes users actually get) and **Unusable suggestions** (the ones the model wrote that had to be dropped). Example: `py-resource-leak-01` flags `with sqlite3.connect(...)` without a `close()`, which leaks the connection it claims to fix.

## Running it
Run from `backend/`, so the LLM settings load from `backend/.env`:

```bash
cd backend
uv run python ../evals/run_eval.py --prompt v1 --model gemini-3.5-flash-lite --pause 4 --retry-errors 2
uv run python ../evals/run_eval.py --prompt v2 --cases 'ts-*'      # a subset
uv run python ../evals/run_eval.py --provider fake                 # offline plumbing check
uv run python ../evals/compare.py ../evals/results/A.json ../evals/results/B.json
```

- **`--pause`** waits between cases, for free-tier rate limits.
- **`--retry-errors N`** re-runs a case whose LLM call failed, so an outage isn't scored as missed bugs.
- **Results** go to `results/<timestamp>_<prompt>_<model>.json` (every prediction, for recomputing and comparing) and a matching `.md` report. The report lists every miss and false positive with its reason.

## Where it runs
- **CI (every push):** the harness itself, with a scripted fake LLM. That covers the matching rule, metrics, reports, and dataset validity.
- **Real models:** by hand only. A small subset can also run from the manual **Eval smoke** workflow. It needs a `GEMINI_API_KEY` secret and an `LLM_MODEL` variable.

## Results
See [RESULTS.md](RESULTS.md).
