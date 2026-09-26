"""Per-run cost cap (security checklist: per-run token and cost caps)."""

import pytest

from app.config.repo_config import RepoConfig
from app.review.diff_parser import parse_unified_diff
from app.review.engine import CostGuard, run_review
from app.review.llm.fake import FakeLLMProvider
from app.review.models import PRContext, ReviewBudget
from app.review.pricing import PriceTable


def new_file(path: str, lines: int) -> str:
    body = "".join(f"+value_{i} = {i}\n" for i in range(lines))
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{lines} @@\n{body}"
    )


# ~1.3K tokens each: with ~1.6K of prompt overhead, a 3.5K chunk holds one file, not two.
FILES = parse_unified_diff(new_file("a.py", 200) + new_file("b.py", 200))
# $1 per input token, output free: a call costs exactly its prompt's token count.
PRICES = PriceTable.from_config({"priced-model": (1_000_000.0, 0.0)})


async def review(cap: float | None, llm: FakeLLMProvider) -> tuple[list[str], float]:
    result = await run_review(
        FILES,
        RepoConfig(),
        PRContext(title="t"),
        llm,
        model="priced-model",
        prices=PRICES,
        # small chunks: one file per LLM call; two calls may run at once
        budget=ReviewBudget(max_chunk_tokens=3_500, max_concurrent_llm_calls=2, max_cost_usd=cap),
        summarize=False,
    )
    return [s.reason for s in result.skipped_files], result.cost_usd


async def test_without_a_cap_every_chunk_is_reviewed() -> None:
    reasons, cost = await review(None, FakeLLMProvider())
    assert reasons == []
    assert cost > 0


async def test_a_call_that_would_cross_the_cap_is_never_made() -> None:
    _, full_cost = await review(None, FakeLLMProvider())
    llm = FakeLLMProvider()
    cap = full_cost * 0.75  # room for one of the two (equal-sized) calls, not both
    reasons, cost = await review(cap, llm)
    assert reasons == ["cost_cap"]  # one file skipped, and says why
    assert len(llm.calls) == 1  # even though both calls were in flight together
    assert cost <= cap


def test_guard_reserves_worst_case_then_settles_to_actual() -> None:
    guard = CostGuard(1.0)
    assert guard.reserve(0.6)
    assert not guard.reserve(0.6)  # 1.2 would cross the cap
    guard.settle(0.6, 0.1)  # the call was cheaper than its worst case
    assert guard.reserve(0.6)
    assert guard.committed == pytest.approx(0.7)
    assert CostGuard(None).reserve(1e9)  # no cap: anything goes
