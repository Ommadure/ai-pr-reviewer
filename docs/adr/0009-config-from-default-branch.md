# ADR 0009: Read `.reviewpilot.yml` from the default branch only

- **Status:** Accepted
- **Date:** 2026-09-25

## Context
`.reviewpilot.yml` controls ReviewPilot per repository. It can switch the reviewer off (`enabled`), raise thresholds (`min_severity`, `min_confidence`), narrow the scope (`focus`, `ignore_paths`), and inject instructions into the prompt (`custom_rules`).

If we read it from the **PR's branch**, any PR author could change those settings for their own PR:
- ignore the file that contains the bug;
- disable the reviewer;
- add a `custom_rules` entry that steers the model.

## Decision
- **Where:** the config is read at the **head commit of the repository's default branch**. The orchestrator first asks GitHub for that branch's head commit, then fetches `.reviewpilot.yml` at exactly that commit.
- **Caching:** the result is cached in `repo_configs`, keyed by `(repository_id, commit_sha)`. An unchanged default branch costs one lightweight API call (the branch lookup). The cache also doubles as an audit trail of which config applied to which review.
- **Defensive parsing** (from Phase 2):
  - `yaml.safe_load` only;
  - a 20 KB size limit;
  - at most 20 custom rules of up to 300 characters each;
  - unknown keys become warnings;
  - an invalid file **falls back to defaults** and the problem is shown in the review summary and the check run. A broken config never breaks reviews.
- **Webhook handler:** it never calls GitHub, so the `review_drafts` decision there uses the **last cached** config. The worker re-checks with the fresh config before reviewing.

## Consequences
- A config change only takes effect once it's merged, which is the same trust model as CI config on protected branches.
- Repository maintainers, the people who can merge to the default branch, control the reviewer. PR authors don't.
- To trial a config change, merge it (or test it with the CLI's `--config` flag).
