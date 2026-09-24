# ADR 0008: Comment fingerprints for dedupe

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
A PR gets reviewed many times: when it's opened, on every push, and on `/reviewpilot review`. Without dedupe, the same issue gets posted again and again, which is the fastest way to get a bot uninstalled. We need an identity for "the same problem", and two obvious candidates are both unstable:
- **Line numbers shift** whenever code is added above the problem.
- **The LLM's wording varies** between runs even at low temperature, including the title, body and severity.

## Decision
```
fingerprint = sha256(path + "\0" + category + "\0" + normalize(code of lines start_line..line))
normalize   = drop blank lines, collapse all whitespace inside each line
```
- **Path:** the same code in two files is two problems.
- **Category:** a `bug` and a `security` issue on the same line are different comments.
- **The code under the comment**, normalised: re-indenting or reformatting doesn't create a "new" problem, but changing the code does. If the author edits the flagged line, a new finding is legitimately new.
- **Never** line numbers or LLM text.

How fingerprints are used:
- **Within one run:** duplicates (for example from overlapping chunks) are dropped. The highest-ranked comment survives.
- **Across runs:** the orchestrator passes the fingerprints of comments already posted on the PR. Matches are dropped as `already_posted`. In Phase 3 a partial unique index `(pull_request_id, fingerprint) WHERE posted` enforces the same rule in the database.
- **Secret-scanner findings** use the category `security`, so an LLM comment about the same leaked key on the same line dedupes against them.

## Consequences
- Pushing unrelated commits never repeats old comments. This is verified by unit tests with shifted lines.
- If the same bad pattern is copied to a *different* line in the same file, it gets the same fingerprint when the code text is identical. We accept that: the first comment already made the point.
- The stored `code_snapshot` (the raw target lines) is also used in Phase 4 to detect whether the author later changed the flagged code ("addressed").
