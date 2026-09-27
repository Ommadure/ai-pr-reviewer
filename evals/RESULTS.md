# Evaluation results

The rows below used the Phase 6 dataset in `cases/` (25 bug cases and 5 clean; v4's section uses the current 26 + 5), the matching rule in [ADR 0013](../docs/adr/0013-eval-matching-rule.md), temperature 0.1, and the Gemini free tier, run on 2026-09-25. The time in each row links to that run's full report, which lists every miss and false positive.

| Run | Prompt | Model | Precision | Recall | F1 | Found | FP (on clean) | Severity exact | Tokens in / out | p50 | p95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [17:05](results/20260925T170554Z_v1_gemini-3.5-flash-lite.md) | v1 | gemini-3.5-flash-lite | 96% | 96% | 96% | 24/25 | 1 (0) | 62% | 38,448 / 6,589 | 2.8 s | 12.3 s* |
| [17:20](results/20260925T172004Z_v1_gemini-3.5-flash-lite.md) | v1 (repeat) | gemini-3.5-flash-lite | 96% | 96% | 96% | 24/25 | 1 (0) | 71% | 36,941 / 6,113 | 1.7 s | 2.1 s |
| [17:13](results/20260925T171337Z_v2_gemini-3.5-flash-lite.md) | v2 | gemini-3.5-flash-lite | 92% | 96% | 94% | 24/25 | 2 (0) | 75% | 47,392 / 6,564 | 2.1 s | 10.5 s* |
| [17:22](results/20260925T172255Z_v2_gemini-3.5-flash-lite.md) | v2 (repeat) | gemini-3.5-flash-lite | 96% | 96% | 96% | 24/25 | 1 (0) | 67% | 45,611 / 6,210 | 1.8 s | 2.3 s |
| [17:17](results/20260925T171715Z_v3_gemini-3.5-flash-lite.md) | **v3 (shipped)** | gemini-3.5-flash-lite | 96% | 96% | 96% | 24/25 | 1 (0) | 75% | 47,831 / 6,213 | 1.9 s | 2.3 s |
| [17:31](results/20260925T173112Z_v3_gemini-3.1-flash-lite.md) | v3 | gemini-3.1-flash-lite | 89% | **100%** | 94% | 25/25 | 3 (2) | 60% | 47,831 / 6,732 | 5.9 s | 41.6 s |

\* Gemini was overloaded during the first two runs, so their p95 includes 503 retries. Compare latency using the repeat runs.

**Cost** is $0 in every row because these models are on the free tier and `LLM_PRICING` has no entry for them. On a paid tier, cost is the token columns multiplied by the price per million tokens.

**`gemini-3.5-flash` couldn't be measured that day.** It is the model in `.env`, but every attempt got a 503 ("high demand") and then a 429 (quota). The harness is ready for it:

```bash
uv run python ../evals/run_eval.py --prompt v3 --model gemini-3.5-flash --pause 4 --retry-errors 2
```

## What changed between versions, and why
- **v1**
  - **What it is:** the Phase 2 prompt. It lists the severities with one example each, and the categories only inside the JSON shape.
  - **Result:** it found 24 of 25 bugs, with no false positives on clean code. Its weaknesses:
    - **Severity inflation:** only 62–71% of the bugs it found got the planted severity, and most misses were `medium` bugs called `high`.
    - **Category drift:** the N+1 query was filed as `bug`, a resource leak as `error_handling`, and missing validation as `bug`.
- **v2** (built from v1's failures)
  - **Severity rule:** a decision question ("in normal use, how often does it go wrong, and how badly?"), with `medium` defined as "needs a particular input, state, ordering or amount of data". When torn, pick the lower level.
  - **Category rubric:** pick the category by consequence, not by fix.
  - **Result:** severity agreement 67–75%.
  - **New failure:** on one run, v2 claimed "NameError: `os` is not imported". `import os` was on line 1, just outside the diff's context. The model invented a problem in code it could not see.
- **v3** (built from v2's failures)
  - **Out-of-diff rule:** you see only part of each file, so never claim something is undefined, not imported or unused unless the diff removes it.
  - **Explicit N+1 wording:** a query inside a loop is `performance` even when the fix is a code change.
  - **Result:** severity agreement 75%, the cleanest run, and no hallucinated-import comment.

## Known failure: `sqlite3`'s context manager (2026-09-27)
Found in production, on the first real review (reviewpilot-playground PR #4). The reviewer flagged a leaked SQLite connection, and suggested `with sqlite3.connect(...) as conn:` as the fix. That block commits or rolls back but **never closes** the connection. It's now two cases (ADR 0013, amendment):
- **`py-resource-leak-01`** gained `bad_suggestions`, so the wrong fix is scored;
- **`py-sqlite-with-leak-01`** is new: the code already uses the `with` block and still leaks.

Prompt v3 on gemini-3.5-flash-lite, 3 runs of just these two cases:

| | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| `py-resource-leak-01` | found, **wrong fix** | found, **wrong fix** | found, **wrong fix** |
| `py-sqlite-with-leak-01` | **missed** | **missed** | **missed** |

It happens every time, so it's a real blind spot, not noise. **Prompt v4 fixes it** (above). The full-dataset rows above predate both changes. To reproduce:

```bash
uv run python ../evals/run_eval.py --prompt v3 --model gemini-3.5-flash-lite --cases 'py-*leak*'
```

## Prompt v4 (2026-09-27): context-manager leaks and fix correctness
v4 is v3 plus two rules in `system.md`. Nothing else changed.
- **Resource leaks:** check what a `with` block does on exit instead of assuming it cleans up. The example: `with sqlite3.connect(...)` commits or rolls back but never closes.
- **Suggestions:** a suggestion must remove the cause described in the comment, not just its appearance:
  - a leak fix must release the resource on every path, using `contextlib.closing`, `try`/`finally` or a documented closing context manager, and never `with sqlite3.connect(...)`;
  - an injection fix must bind or escape the value;
  - if unsure, leave `suggestion` null and describe the fix in the body.

**Full dataset** (31 cases: 26 with bugs, 5 clean), gemini-3.5-flash-lite, two runs of each prompt:

| Run | Prompt | Precision | Recall | Found | FP | Severity exact | Known-wrong fixes | Suggestions on found bugs | Tokens in |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [03:28](results/20260927T032817Z_v3_gemini-3.5-flash-lite.md) | v3 | 96% | 92% | 24/26 | 1 | 71% | 1 | 13/24 | 49,385 |
| [03:39](results/20260927T033928Z_v3_gemini-3.5-flash-lite.md) | v3 | 96% | 92% | 24/26 | 1 | 79% | 1 | 12/24 | 49,385 |
| [03:36](results/20260927T033630Z_v4_gemini-3.5-flash-lite.md) | **v4** | 100% | 96% | 25/26 | 0 | 84% | 0 | 10/25 | 58,884 |
| [03:42](results/20260927T034228Z_v4_gemini-3.5-flash-lite.md) | **v4** | 100% | 100% | 26/26 | 0 | 81% | 0 | 9/26 | 56,825 |

p50 latency was 1.9 s for both, and cost was $0 (free tier).

**The target cases and the held-out set** ([`heldout/`](heldout/), run with `--cases-dir ../evals/heldout`):

| Case | v3 | v4 |
| --- | --- | --- |
| `py-resource-leak-01` | found, **wrong fix** 3/3 | found, no wrong fix 5/5 |
| `py-sqlite-with-leak-01` | **missed** 3/3 | found 5/5 |
| `py-psycopg2-with-leak-01` (held out) | "found" 3/3, but as a *missing commit*, which is false; the fix was wrong every time | the real bug ("not closed") 3/3, no wrong fixes |
| `clean-psycopg3-with-01` (control) | 1 FP per run ("missing commit", false) | 1 FP per run ("not closed", false) |

**How I got to the final text** (four drafts, each measured):
1. **"If a context manager's exit isn't a documented close, treat the resource as still open."** Found both sqlite cases, but it's why v4 calls psycopg 3 "not closed".
2. **A hedge instead: "if you don't know the library, say nothing".** Worse: it missed `py-sqlite-with-leak-01` again, even though the prompt states the sqlite fact. Hedging suppressed the knowledge the prompt was teaching.
3. **Neutral wording, neither assume nor hedge.** Both found, and the correct bug on psycopg2. But 1 wrong fix in 7 chances: without being told otherwise, the model still reaches for a `with` block as "the fix".
4. **Plus one named counter-example** in the suggestion rules (shipped). 0 wrong fixes in every run since.

**What v4 costs, honestly:**
- **Fewer suggestions:** about 37% of found bugs get one, against about 52% on v3. The rule says "if unsure, no suggestion", and the model applies it beyond leaks. A missing one-click fix is a smaller harm than a wrong one, but it's a real loss of convenience. The next thing to measure is suggestion *correctness* across all categories, not only the known-wrong ones.
- **About 17% more input tokens** per review.
- **One unexplained miss:** `ts-unhandled-promise-01` got no comment in one of the two v4 runs. It was found in the other run, and in every earlier run of every prompt, so it's probably noise, but it's worth watching.
- **psycopg 3 is a model knowledge gap:** both prompts post one false claim on it. The v4 draft that tried to fix it with wording made everything else worse (step 2).
- **The target cases were written from this very failure,** and the held-out control was seen by one draft. The psycopg2 bug case was never in the prompt, and that is the real evidence v4 learned the rule and not just the example.

## What we shipped, and why
**Prompt v4 is now the default** (`DEFAULT_PROMPT_VERSION = "v4"`, 2026-09-27). It stayed the default after the v5 experiment below. Across both runs it matched or beat v3 on every detection metric: 25–26/26 found against 24/26, no false positives on the dataset against 1, and severity-exact 81–84% against 71–79%. It also posted **no known-wrong fixes**. The costs are listed above. v3 shipped after the Phase 6 eval, for the reasons below.

**Prompt v3** (2026-09-25):
- **Why it won then:** it matched v1 and v2 on detection (24/25, no false positives on clean code), had the best severity agreement, and guards against a known hallucination class.
- **What it costs:** about 24% more input tokens, because the system prompt is longer. That's free on this tier and small on a paid one.

**On the model**, the evidence points at **gemini-3.5-flash-lite** for this workload:
- **Against gemini-3.1-flash-lite:** the same detection except the N+1 category, no false positives on clean code (against 0.4 per clean case), and about 3× faster.
- **Against gemini-3.5-flash:** it could not be measured, so that comparison is still open.

If you want to switch, change `LLM_MODEL` in `backend/.env`. I didn't edit it, because that file holds your secrets.

## Prompt v5 (2026-09-27): more one-click fixes? Not shipped
**Goal:** offer confident one-click fixes again for easy local cases (v4 had become cautious), without wrong ones.

**What v5 changes** (on top of v4, `system.md` only):
- **Suggest whenever the fix is local**, with a list of common local fixes: bound parameters, argument lists, rendering as text, a missing `await`, operators and bounds, a sort comparator, a mutable default;
- **"The suggestion replaces exactly `start_line`..`line`":** every line in the range, nothing outside it;
- **no diff-view markers**;
- the v4 correctness rules, kept.

**What changed in the harness first,** so v5 could be judged on its fixes and not just its findings:
- **wrong-fix denylists for 10 more cases.** Each pattern must match the buggy code and pass at least one correct fix; the path-traversal pattern treats a bare `".." in filename` check as wrong, because an absolute path bypasses it;
- **new metrics:** "Fixes offered on found bugs" and "Unusable suggestions";
- **two new held-out cases** that the prompt never mentions: `yaml.load` → `safe_load`, and `indexOf` used as a boolean.

The biggest finding came before any prompt change. Replaying every stored suggestion over its case's code showed that **about 1 in 4, on every prompt version, would break the file when applied**, for example by deleting a `raise` or duplicating a loop body. That was fixed in the engine (#26), where it protects every prompt, and it's now measured (ADR 0013, amendment 2).

**Full dataset** (31 cases), gemini-3.5-flash-lite, with the engine's suggestion checks:

| Run | Prompt | Found | FP | Known-wrong fixes | Fixes offered | Unusable suggestions | Severity exact | Tokens in |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [10:49](results/20260927T104955Z_v4_gemini-3.5-flash-lite.md) | **v4** | 26/26 | 0 | 0/3 | 6/26 | 2/8 | 81% | 56,825 |
| [10:46](results/20260927T104653Z_v5_gemini-3.5-flash-lite.md) | v5 | 25/26 | 0 | 0/4 | 8/25 | 3/11 | 76% | 66,714 |
| [10:53](results/20260927T105344Z_v5_gemini-3.5-flash-lite.md) | v5 | 25/26 | 1 | 0/4 | 8/25 | 4/12 | 72% | 69,100 |

**Held-out set** (4 cases), three runs of each prompt:

| | v4 | v5 |
| --- | --- | --- |
| Found | 3/3 every run | 3/3 every run |
| Fixes offered | 1, 1, 0 of 3 | **2, 2, 2** of 3 |
| Unusable suggestions | 1/2, 2/3, 2/2 | **1/3, 0/2, 0/2** |
| False positives | 1 per run (the psycopg 3 control) | the same |

v5's surviving held-out fixes were all correct: `yaml.safe_load(uploaded)` and `user.roles.includes(ADMIN)`. Its SQL-injection fix binds both parameters.

**Why v5 isn't shipped:**
- **It lost the sqlite blind-spot fix.** v5 missed `py-sqlite-with-leak-01` in both full runs, which is the case v4 was written to catch (5 of 5 in v4's own runs). The leak rule is still in the prompt, so this is most likely dilution: a longer prompt with more emphasis on suggestions.
- **The exact-range rule didn't work.** Unusable suggestions were 27–33% with v5, against 25% with v4. The model doesn't reliably follow a placement instruction, which is why the engine check (#26) is the real protection.
- **The rest doesn't add up.** It offers 2 more fixes out of about 25 found bugs, and does better on the held-out set. Against that, severity-exact drops 5–9 points and it uses about 20% more input tokens. It isn't a clear win.

v5 stays in the repo as a measured experiment (`--prompt v5`). v4 remains the default.

**Next time:**
- Keep v4's detection text as it is, and change **only** the suggestion list. Then measure whether the sqlite miss comes back.
- Consider a separate, cheaper follow-up call that writes suggestions for comments that don't have one. That keeps detection and fixing apart.

## How to read these numbers (honestly)
- **The dataset is at its ceiling for detection.** Every configuration found at least 24 of 25 bugs, so precision and recall barely separate prompts here. The differences show up in severity, category and false-positive quality. The next step is a harder tier: longer diffs with distracting but correct code, and several bugs per case.
- **Noise is about ±5 points on severity agreement.** The two v1 runs differ by 9 points. v3's severity gain is directional, not proven.
- **We tuned on the test set.** v2 and v3 were written after reading the failures on these same cases. The rules are phrased generically, but a held-out split is the proper check.
- **Some false positives are real findings.** gemini-3.1-flash-lite flagged a *public* profile endpoint that returns users' email. That's a privacy leak the dataset didn't plant, and the strict rule scores it as a false positive (ADR 0013).
- **The first run also found an engine bug.** `prompt_builder.fill()` crashed on any diff containing `{{`, such as JSX `style={{…}}`. It's fixed, with regression tests.
