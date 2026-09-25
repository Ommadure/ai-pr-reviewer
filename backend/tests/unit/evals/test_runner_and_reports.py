"""The runner end to end with a scripted LLM, the metrics, and both reports."""

import json
from pathlib import Path

import pytest

from app.evals.cli import CASES_DIR, compare_main, run_main
from app.evals.dataset import EvalCase, load_cases
from app.evals.report import compare_report, run_report
from app.evals.results import RunResult, compute_metrics, percentile
from app.evals.runner import run_eval
from app.review.llm.base import LLMError, Message
from app.review.llm.fake import FakeLLMProvider
from app.review.models import ReviewBudget
from app.review.pricing import PriceTable


def oracle(cases: list[EvalCase], *, extra_on_clean: bool = False):
    """A responder that 'finds' each case's planted bugs, and optionally nitpicks clean ones."""
    by_path = {f.path: c for c in cases for f in c.files}

    def respond(messages: list[Message]) -> str:
        prompt = messages[-1].content
        case = next(c for path, c in by_path.items() if path in prompt)
        comments = [
            {
                "path": b.path,
                "line": b.end,
                "start_line": None,
                "severity": b.severity,
                "category": b.category,
                "title": b.description[:90],
                "body": b.description,
                "suggestion": None,
                "confidence": 0.9,
            }
            for b in case.spec.planted_bugs
        ]
        if extra_on_clean and case.kind == "clean":
            file = case.files[0]
            line = next(ln.new_line for h in file.hunks for ln in h.lines if ln.type == "added")
            comments.append(comments_for_nit(file.path, line))
        return json.dumps({"comments": comments, "file_summaries": []})

    return respond


def comments_for_nit(path: str, line: int | None) -> dict[str, object]:
    return {
        "path": path,
        "line": line,
        "start_line": None,
        "severity": "low",
        "category": "maintainability",
        "title": "Consider a docstring",
        "body": "Style nit.",
        "suggestion": None,
        "confidence": 0.7,
    }


async def run(cases: list[EvalCase], llm: FakeLLMProvider, prompt: str = "v1") -> RunResult:
    return await run_eval(
        cases,
        llm,
        provider="fake",
        model="fake-model",
        prompt_version=prompt,
        budget=ReviewBudget(),
        prices=PriceTable(),
    )


@pytest.fixture(scope="module")
def cases() -> list[EvalCase]:
    return load_cases(CASES_DIR)


async def test_a_perfect_reviewer_scores_100(cases: list[EvalCase]) -> None:
    result = await run(cases, FakeLLMProvider(responder=oracle(cases)))
    m = result.metrics
    assert (m.precision, m.recall, m.f1) == (1.0, 1.0, 1.0)
    assert m.tp == m.planted_bugs == 25
    assert m.fp_per_clean_case == 0
    assert m.cases_with_errors == 0
    assert (m.severity_exact, m.severity_within_one) == (1.0, 1.0)  # the oracle copies severity
    assert result.prompt_sha and len(result.prompt_sha) == 12


async def test_nitpicks_on_clean_cases_cost_precision_not_recall(cases: list[EvalCase]) -> None:
    result = await run(cases, FakeLLMProvider(responder=oracle(cases, extra_on_clean=True)))
    m = result.metrics
    assert m.recall == 1.0
    assert m.fp == 5 and m.fp_per_clean_case == 1.0
    assert m.fp_reasons == {"unplanted": 5}
    assert m.precision == pytest.approx(25 / 30)


async def test_a_silent_reviewer_scores_zero_recall(cases: list[EvalCase]) -> None:
    result = await run(cases, FakeLLMProvider())  # empty reviews
    assert result.metrics.recall == 0.0
    assert result.metrics.precision is None  # no comments: precision is undefined, not 0
    report = run_report(result)
    assert "**missed** `py-sql-injection-01`" in report


async def test_llm_failures_are_recorded_per_case(cases: list[EvalCase]) -> None:
    subset = [c for c in cases if c.id.startswith("py-sql")]
    result = await run(subset, FakeLLMProvider(default="not json at all"))
    assert result.metrics.cases_with_errors == 1
    assert result.cases[0].errors


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 95) is None
    assert percentile([5, 1, 3], 50) == 3
    assert percentile(list(range(1, 21)), 95) == 19


async def test_compare_report_shows_deltas_and_changed_cases(cases: list[EvalCase]) -> None:
    a = await run(cases, FakeLLMProvider(responder=oracle(cases, extra_on_clean=True)))
    b = await run(cases, FakeLLMProvider(responder=oracle(cases)))
    report = compare_report(a, b)
    assert "| Precision | 83% | 100% | +17 pts |" in report
    assert "`clean-py-async-01` | 0 found, 1 FP | 0 found, 0 FP" in report
    assert compute_metrics(b.cases) == b.metrics  # metrics are recomputable from the cases


def test_cli_runs_offline_writes_results_and_compares(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--provider", "fake", "--model", "fake-model", "--cases", "sql-*"]
    assert run_main([*args, "--out-dir", str(tmp_path)]) == 0
    written = sorted(tmp_path.glob("*_fake-model.*"))
    assert [p.suffix for p in written] == [".json", ".md"]
    run = RunResult.model_validate_json(written[0].read_text())
    assert run.metrics.cases == 2 and run.cases_pattern == "sql-*"

    assert compare_main([str(written[0]), str(written[0]), "--out", str(tmp_path / "cmp.md")]) == 0
    assert "No case changed outcome." in (tmp_path / "cmp.md").read_text()
    assert "precision" in capsys.readouterr().out


def test_cli_reports_bad_selection(tmp_path: Path) -> None:
    args = ["--provider", "fake", "--model", "fake-model", "--cases", "nothing-*"]
    assert run_main([*args, "--out-dir", str(tmp_path)]) == 2


async def test_outages_are_retried_so_they_are_not_scored_as_misses(cases: list[EvalCase]) -> None:
    subset = [c for c in cases if c.id == "py-sql-injection-01"]
    outage = LLMError("Gemini 503: overloaded")
    llm = FakeLLMProvider([outage], responder=oracle(subset))  # first attempt hits an outage
    result = await run_eval(
        subset,
        llm,
        provider="fake",
        model="fake-model",
        prompt_version="v1",
        budget=ReviewBudget(),
        prices=PriceTable(),
        retry_errors=2,
        retry_wait_seconds=0,
    )
    case = result.cases[0]
    assert (case.attempts, case.errors, len(case.matched)) == (2, [], 1)
