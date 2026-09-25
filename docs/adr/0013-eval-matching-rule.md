# ADR 0013: How the evaluation harness decides a bug was found

- **Status:** Accepted
- **Date:** 2026-09-25

## Context
Prompt and model changes have to be judged on evidence, not on a few hand-picked PRs. The harness (`evals/`, logic in `backend/app/evals/`) runs the pure review engine (ADR 0007) over cases with planted bugs and scores the comments. The score is only as trustworthy as the rule deciding whether a comment "found" a planted bug. Too strict, and correct comments one line off count as misses. Too loose, and vague comments near a bug count as hits.

## Decision
**Dataset.** Each case lives in `evals/cases/<id>/` as a unified diff (`diff.patch`) and a `case.yaml` listing its planted bugs: path, line range, category, severity and description. There are 25 bug cases across Python, TypeScript/TSX, JavaScript and SQL, plus 5 clean cases. Loading a case checks that every planted bug sits on a line the PR adds, so a typo in a line number fails loudly instead of quietly lowering recall. A test also checks the shipped dataset.

**Matching rule.** A comment matches a planted bug when:
1. the file path is the same;
2. the comment's line is within the bug's range ±2, or the comment's own range (`start_line..line`) overlaps the bug. The tolerance allows for a comment anchored on the neighbouring line, such as the `if` after a missing `await`. Two lines is small enough that a comment on unrelated nearby code still doesn't count.
3. the category is compatible. That means equal, listed in the case's `accept_categories`, or in the same equivalence group. There is one group today: `bug` ≡ `error_handling`, because a swallowed exception is fairly called either. `accept_categories` is used sparingly, only for genuinely ambiguous bugs. For example, a check-then-act race in a wallet may be filed as `bug` or `security`.

**One-to-one.** Closest pairs match first. Each bug can be found once and each comment can find one bug. Every other comment is a false positive, tagged with a reason:
- `duplicate`: a second comment on a bug that was already found. It's noise for the reader.
- `wrong_category`: near an unfound bug, but filed under an incompatible category.
- `unplanted`: not near any planted bug. On clean cases, every comment is this.

**Metrics.**
- Precision = TP / comments. Recall = TP / planted bugs. F1 combines the two.
- Per category: recall is counted over planted bugs, and precision over comments filed under that category.
- False positives per clean case.
- Cost and p50/p95 of each case's total LLM time.

**Reproducibility.**
- Every run records its prompt version, a hash of the prompt files (`prompt_sha`), the model, the temperature and every prediction. Metrics can be recomputed and two runs compared case by case (`evals/compare.py`).
- Cases run one at a time with the default repo config, so results measure the prompt and model, not queueing or per-repo tuning.
- The PR-summary call is skipped: it isn't scored, and it would halve free-tier throughput.

**Cost control.** Real-model evals are run by hand. CI runs the harness only with the fake provider (unit tests). A live smoke subset runs only from a manually triggered workflow.

## Consequences
- **Precision is conservative.** A real issue that wasn't planted counts as a false positive. The report lists each false positive with its reason, so a human can see when the model was right and the dataset incomplete. If that keeps happening, the right fix is to add the bug to the case, not to loosen the rule.
- **Small dataset, rough numbers.** With 25 bugs, one bug is worth 4 recall points, so differences of a point or two between runs are noise. Temperature is 0.1, not 0, and providers aren't fully deterministic, so a conclusion should rest on repeated or large differences.
- **Evals find engine bugs too.** The first run showed that `prompt_builder.fill()` crashed on any diff containing `{{` (JSX `style={{…}}`). It now fills placeholders in one pass over the template, and never re-scans inserted text.
