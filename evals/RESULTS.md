# Evaluation results

All runs used the dataset in `cases/` (25 bug cases and 5 clean), the matching rule in [ADR 0013](../docs/adr/0013-eval-matching-rule.md), temperature 0.1, and the Gemini free tier, run on 2026-09-25. The time in each row links to that run's full report, which lists every miss and false positive.

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

## What we shipped, and why
**Prompt v3 is now the default** (`DEFAULT_PROMPT_VERSION = "v3"`).
- **Why it wins:** it matched v1 and v2 on detection (24/25, no false positives on clean code), had the best severity agreement, and guards against a known hallucination class.
- **What it costs:** about 24% more input tokens, because the system prompt is longer. That's free on this tier and small on a paid one.

**On the model**, the evidence points at **gemini-3.5-flash-lite** for this workload:
- **Against gemini-3.1-flash-lite:** the same detection except the N+1 category, no false positives on clean code (against 0.4 per clean case), and about 3× faster.
- **Against gemini-3.5-flash:** it could not be measured, so that comparison is still open.

If you want to switch, change `LLM_MODEL` in `backend/.env`. I didn't edit it, because that file holds your secrets.

## Known failure: `sqlite3`'s context manager (2026-09-27)
Found in production, on the first real review (reviewpilot-playground PR #4). The reviewer flagged a leaked SQLite connection, and suggested `with sqlite3.connect(...) as conn:` as the fix. That block commits or rolls back but **never closes** the connection. It's now two cases (ADR 0013, amendment):
- **`py-resource-leak-01`** gained `bad_suggestions`, so the wrong fix is scored;
- **`py-sqlite-with-leak-01`** is new: the code already uses the `with` block and still leaks.

Prompt v3 on gemini-3.5-flash-lite, 3 runs of just these two cases:

| | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| `py-resource-leak-01` | found, **wrong fix** | found, **wrong fix** | found, **wrong fix** |
| `py-sqlite-with-leak-01` | **missed** | **missed** | **missed** |

It happens every time, so it's a real blind spot, not noise. It's the first case v3 reliably fails, which gives a future prompt version something concrete to beat. The full-dataset rows above predate both changes. To reproduce:

```bash
uv run python ../evals/run_eval.py --prompt v3 --model gemini-3.5-flash-lite --cases 'py-*leak*'
```

## How to read these numbers (honestly)
- **The dataset is at its ceiling for detection.** Every configuration found at least 24 of 25 bugs, so precision and recall barely separate prompts here. The differences show up in severity, category and false-positive quality. The next step is a harder tier: longer diffs with distracting but correct code, and several bugs per case.
- **Noise is about ±5 points on severity agreement.** The two v1 runs differ by 9 points. v3's severity gain is directional, not proven.
- **We tuned on the test set.** v2 and v3 were written after reading the failures on these same cases. The rules are phrased generically, but a held-out split is the proper check.
- **Some false positives are real findings.** gemini-3.1-flash-lite flagged a *public* profile endpoint that returns users' email. That's a privacy leak the dataset didn't plant, and the strict rule scores it as a false positive (ADR 0013).
- **The first run also found an engine bug.** `prompt_builder.fill()` crashed on any diff containing `{{`, such as JSX `style={{…}}`. It's fixed, with regression tests.
