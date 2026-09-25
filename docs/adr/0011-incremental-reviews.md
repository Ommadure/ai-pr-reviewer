# ADR 0011: Incremental reviews on push

- **Status:** Accepted
- **Date:** 2026-09-25

## Context
Re-reviewing the whole PR on every push wastes tokens and time, and it resurfaces the model's opinion of code the author didn't touch. Fingerprints already stop *exact* repeats, but a full re-review still pays for every file. There are three traps:
1. **Merges from `main`.** When the author merges `main` into their branch, `compare(last_reviewed...head)` includes changes that aren't part of the PR. GitHub **rejects** review comments on lines outside the PR's own diff (base...head), with a 422.
2. **Rewritten history.** After a force push or rebase, the last reviewed commit may no longer be an ancestor of the head, or may not exist at all. Then `last...head` doesn't mean "what's new".
3. **Resolved comments.** When an author fixes what we flagged, the comment should stop counting as open, both for the dashboard and for the helpful-rate metric.

## Decision
- **When:** a `synchronize` webhook queues a run with `mode = incremental`. Opened, reopened, ready-for-review and `/reviewpilot review` stay `full`.
- **How the worker plans the diff:**

  | Situation | Review | `mode_reason` |
  |---|---|---|
  | No `last_reviewed_sha` | full PR | `no_previous_review` |
  | `last_reviewed_sha == head` or compare says `identical` | nothing: run `skipped` (`no_new_changes`), no check run | none |
  | compare `ahead` | only the files and lines changed since the last review | none, `from_sha = last_reviewed_sha` |
  | compare `diverged` / `behind`, or the base commit is gone (404) | full PR | `history_rewritten` |

- **The intersection rule:** for an incremental review, an added line stays commentable only if the **PR's own diff** also shows it as added. Other added lines, for example from a merge of `main`, are turned into *context*: the model still sees them, but the validator won't accept comments there. Files outside the PR diff are skipped with reason `not_in_pr_diff`.
- **Addressed detection** runs during the incremental review, over the PR's open posted comments. A comment becomes:
  - `outdated` if its file was deleted or renamed away;
  - `addressed` if the new commits removed or rewrote any line of the flagged range.

  When the comment was posted on the compare base itself, the check is **by line number** against the diff's old side, which is exact. For comments from older commits, whose line numbers may have shifted, it falls back to **matching the content** of `code_snapshot`, ignoring whitespace.

## Consequences
- A typical follow-up push costs a fraction of a full review, and comments land only on what changed.
- Force pushes are handled safely, at the cost of one full review, and the run records why.
- Content matching for older comments can over-report "addressed" when an identical line is removed elsewhere in the same file. We accept that: it can only make a comment look resolved, never post a wrong one.
- `compare` returns at most 300 files. A push touching more than that is well beyond the per-run token budget anyway.
