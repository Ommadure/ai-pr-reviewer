"""Run the pure engine over eval cases and score each one (no GitHub, no database)."""

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.config.repo_config import parse_repo_config
from app.evals.dataset import EvalCase
from app.evals.matching import EQUIVALENT_CATEGORIES, LINE_TOLERANCE, Prediction, match_case
from app.evals.results import CaseResult, RunResult, compute_metrics
from app.review import prompt_builder
from app.review.engine import TEMPERATURE, run_review
from app.review.llm.base import LLMProvider
from app.review.models import PRContext, ReviewBudget
from app.review.pricing import PriceTable

Progress = Callable[[int, int, CaseResult], None]


def prompt_sha(version: str) -> str:
    """Hash of every file in the prompt version, so results say exactly which text ran."""
    directory = prompt_builder.PROMPTS_DIR / version
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.md")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


async def evaluate_case(
    case: EvalCase,
    llm: LLMProvider,
    *,
    model: str,
    prompt_version: str,
    budget: ReviewBudget,
    prices: PriceTable,
) -> CaseResult:
    # Every case is reviewed with the default repository config, like a repo with no
    # .reviewpilot.yml, so results measure the prompt and model, not per-case tuning.
    config = parse_repo_config(None).config
    pr = PRContext(
        title=case.spec.title or case.spec.description, description=case.spec.pr_description
    )
    result = await run_review(
        case.files,
        config,
        pr,
        llm,
        model=model,
        budget=budget,
        prices=prices,
        prompt_version=prompt_version,
        summarize=False,
    )
    predictions = [Prediction.from_comment(c) for c in result.comments]
    return CaseResult.from_match(
        match_case(predictions, case.spec.planted_bugs),
        id=case.id,
        kind=case.kind,
        language=case.spec.language,
        planted=case.spec.planted_bugs,
        predictions=predictions,
        dropped_by_validator=len(result.dropped),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        latency_ms=sum(call.latency_ms for call in result.llm_calls),
        llm_calls=len(result.llm_calls),
        errors=result.errors,
    )


async def run_eval(
    cases: Sequence[EvalCase],
    llm: LLMProvider,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    budget: ReviewBudget,
    prices: PriceTable,
    cases_pattern: str = "*",
    pause_seconds: float = 0.0,
    retry_errors: int = 0,
    retry_wait_seconds: float = 60.0,
    progress: Progress | None = None,
) -> RunResult:
    """Cases run one at a time: free-tier rate limits are per minute, and sequential
    runs keep latency numbers comparable (no queueing behind other cases).

    A case whose LLM call failed (an outage, not a wrong answer) is re-run up to
    `retry_errors` times after a cooldown, so a provider's bad minute isn't scored as
    missed bugs. If it still fails, the errors stay in the result and are reported.
    """
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        if index and pause_seconds:
            await asyncio.sleep(pause_seconds)
        result = await evaluate_case(
            case, llm, model=model, prompt_version=prompt_version, budget=budget, prices=prices
        )
        for attempt in range(retry_errors):
            if not result.errors:
                break
            await asyncio.sleep(retry_wait_seconds * (attempt + 1))
            result = await evaluate_case(
                case, llm, model=model, prompt_version=prompt_version, budget=budget, prices=prices
            )
            result.attempts = attempt + 2
        results.append(result)
        if progress:
            progress(index + 1, len(cases), result)
    return RunResult(
        created_at=datetime.now(UTC),
        prompt_version=prompt_version,
        prompt_sha=prompt_sha(prompt_version),
        provider=provider,
        model=model,
        temperature=TEMPERATURE,
        cases_pattern=cases_pattern,
        line_tolerance=LINE_TOLERANCE,
        category_equivalences=[sorted(group) for group in EQUIVALENT_CATEGORIES],
        cases=results,
        metrics=compute_metrics(results),
    )
