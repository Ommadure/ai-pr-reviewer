# ADR 0006: ReviewPilot only comments; it never approves or blocks

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
An LLM reviewer produces both false positives and false negatives. If it could approve a PR, a bug could merge on a machine's word. If it could request changes or fail a required check, one hallucinated comment would block a human's work. If *our* service is down, that mustn't stop anyone merging.

## Decision
- Reviews are always posted with `event: "COMMENT"`. The client hard-codes it, so `APPROVE` and `REQUEST_CHANGES` can't be sent by mistake.
- Check runs only ever conclude `success` or `neutral`, never `failure`. The type signature of `complete_check_run` enforces this.
  - `neutral`: the review found high or critical issues (Phase 3), or ReviewPilot itself failed.
  - `success`: everything else.
- If the pipeline crashes after creating a check run, it still completes that run as `neutral` with a friendly message, so a check never spins forever.

## Consequences
- ReviewPilot is advisory: humans stay accountable for merges.
- Teams can't make ReviewPilot a hard gate through branch protection. That's intended, and they can still rely on human approvals.
