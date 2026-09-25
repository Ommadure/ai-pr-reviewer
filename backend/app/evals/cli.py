"""Command-line entry points, used by evals/run_eval.py and evals/compare.py.

Run from backend/ so LLM settings load from backend/.env, like the server:

    uv run python ../evals/run_eval.py --prompt v1
    uv run python ../evals/run_eval.py --prompt v2 --cases 'py-*' --pause 5
    uv run python ../evals/compare.py ../evals/results/A.json ../evals/results/B.json
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

import httpx

from app.core.config import ReviewSettings
from app.evals.dataset import CaseError, EvalCase, load_cases
from app.evals.report import compare_report, pct, run_report, secs, usd
from app.evals.results import CaseResult, RunResult, compute_metrics
from app.evals.runner import run_eval
from app.review.llm.factory import (
    LLMConfigurationError,
    build_provider,
    price_table,
    review_budget,
    review_model,
)
from app.review.prompt_builder import DEFAULT_PROMPT_VERSION, available_prompt_versions

REPO_ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = REPO_ROOT / "evals" / "cases"
RESULTS_DIR = REPO_ROOT / "evals" / "results"


def run_main(argv: list[str] | None = None) -> int:
    args = _run_parser().parse_args(argv)
    overrides: dict[str, object] = {}
    if args.provider:
        overrides["llm_provider"] = args.provider
    if args.model:
        overrides["llm_model"] = args.model
    settings = ReviewSettings(**overrides)  # type: ignore[arg-type]
    try:
        cases = load_cases(Path(args.cases_dir), args.cases)
        model = review_model(settings)
        result = asyncio.run(_run(settings, cases, model, args))
    except (CaseError, LLMConfigurationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result.created_at:%Y%m%dT%H%M%SZ}_{result.prompt_version}_{_slug(result.model)}"
    (out_dir / f"{stem}.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(run_report(result), encoding="utf-8")
    m = result.metrics
    print(
        f"\nprecision {pct(m.precision)} · recall {pct(m.recall)} · F1 {pct(m.f1)} · "
        f"{m.fp} FP ({_one_decimal(m.fp_per_clean_case)} per clean case) · "
        f"{usd(m.cost_usd_total)} · p50 {secs(m.latency_p50_ms)} · p95 {secs(m.latency_p95_ms)}"
    )
    print(f"wrote {out_dir / stem}.json and .md")
    return 0


async def _run(
    settings: ReviewSettings, cases: list[EvalCase], model: str, args: argparse.Namespace
) -> RunResult:
    async with httpx.AsyncClient() as http:
        llm = build_provider(settings, http)
        return await run_eval(
            cases,
            llm,
            provider=settings.llm_provider,
            model=model,
            prompt_version=args.prompt,
            budget=review_budget(settings),
            prices=price_table(settings),
            cases_pattern=args.cases,
            pause_seconds=args.pause,
            retry_errors=args.retry_errors,
            progress=_print_progress,
        )


def _print_progress(done: int, total: int, case: CaseResult) -> None:
    outcome = f"{len(case.matched)}/{len(case.planted)} found" if case.planted else "clean"
    error = f"  ERROR {case.errors[0][:60]}" if case.errors else ""
    if case.attempts > 1:
        error += f"  (attempt {case.attempts})"
    print(
        f"[{done:>2}/{total}] {case.id:<34} {outcome:<11} {len(case.false_positives)} FP  "
        f"{secs(case.latency_ms):>7}{error}",
        flush=True,
    )


def compare_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="compare.py", description="Compare two eval runs side by side."
    )
    parser.add_argument("a", help="baseline result JSON")
    parser.add_argument("b", help="candidate result JSON")
    parser.add_argument("--out", help="also write the Markdown to this file")
    args = parser.parse_args(argv)
    try:
        a, b = (_load_run(Path(path)) for path in (args.a, args.b))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = compare_report(a, b)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    return 0


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_eval.py", description="Measure the review engine on cases with planted bugs."
    )
    parser.add_argument(
        "--prompt", default=DEFAULT_PROMPT_VERSION, choices=available_prompt_versions()
    )
    parser.add_argument("--model", help="override LLM_MODEL")
    parser.add_argument("--provider", help="override LLM_PROVIDER (gemini, fake)")
    parser.add_argument("--cases", default="*", help="glob over case ids, e.g. 'py-*'")
    parser.add_argument("--cases-dir", default=str(CASES_DIR))
    parser.add_argument("--out-dir", default=str(RESULTS_DIR))
    parser.add_argument(
        "--pause", type=float, default=0.0, help="seconds between cases (free-tier rate limits)"
    )
    parser.add_argument(
        "--retry-errors",
        type=int,
        default=0,
        help="re-run a case up to N times when its LLM call fails (outages, not answers)",
    )
    return parser


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "model"


def _load_run(path: Path) -> RunResult:
    """Metrics are recomputed from the saved predictions, so runs written before a
    metric existed still compare like for like."""
    run = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
    return run.model_copy(update={"metrics": compute_metrics(run.cases)})


def _one_decimal(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"
