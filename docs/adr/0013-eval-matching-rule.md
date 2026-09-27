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

## Amendment (2026-09-27): known-wrong fixes
**Context.** In production, the reviewer correctly flagged a leaked SQLite connection, then suggested `with sqlite3.connect(...) as conn:`. That block only commits or rolls back; it never closes the connection, so the suggested fix still leaks. The matching rule scored the comment as a perfect hit, because it looks only at path, line and category, never at the suggestion. A one-click `suggestion` that is wrong is worse than no suggestion, because it looks authoritative.

**Decision.**
- A planted bug may list **`bad_suggestions`**: regexes over the suggested code that mark a known-wrong fix. They're compiled at load time, so a broken pattern fails loudly.
- For each *matched* comment with a `suggestion`, the harness reports whether it's a known-wrong fix. The metrics add `fixes_checked` and `bad_fixes`, and the report adds "Known-wrong fixes" and a **wrong fix** line per case.
- **Detection metrics are unchanged.** A wrong fix still found the bug, and mixing the two would hide which skill regressed.
- Patterns describe the *wrong* fix, not the right one, because there are many correct fixes (`contextlib.closing`, `try/finally`, an explicit `close()`) and one well-known wrong one.

**Consequences.**
- This is a denylist. It catches known mistakes, not every bad fix, so a clean result means "none of the known-wrong fixes", not "the fix is correct". Proving a fix correct would mean running it, which is out of scope.
- Older result files have no suggestions recorded. They load unchanged, and show "-" for the new metric.
- New case `py-sqlite-with-leak-01` tests the same misconception from the detection side: the code already uses the `with` block, so only a reviewer who knows it doesn't close will flag the leak.

## Amendment 2 (2026-09-27): fixes that don't fit their lines
**Context.** A GitHub suggestion replaces exactly `start_line..line`. Replaying every stored suggestion over its case's code showed about 1 in 4 (prompts v3–v5) would break the file when applied: the whole function written over its `def` line, a loop and its body over the `for` line, or a fixed `if` over the `if` and the `raise` under it, deleting the `raise`. The text of each fix was right, but where it was applied wasn't, so the known-wrong-fix denylist couldn't see it. Some suggestions also copied the diff view's `+ 16 | ` markers.

**Decision.**
- The engine drops such suggestions before posting (`validator.clean_suggestion`, `validator.misfit_reason`; #26). The comment still posts.
- The harness records `suggestion_dropped` per prediction, and reports:
  - **Fixes offered on found bugs:** found bugs whose comment kept a one-click fix, which is what users actually get;
  - **Unusable suggestions:** suggestions the model wrote that the validator had to drop.
- A prompt is judged on both: more fixes offered, fewer unusable, and no known-wrong fixes.

**Consequences.**
- Placement is now measured for every suggestion, not just the denylisted cases. Correctness still isn't proven: a suggestion that fits its lines can still be logically wrong, which the denylists catch only for known mistakes.
- Older result files predate the field and show 0 dropped.
