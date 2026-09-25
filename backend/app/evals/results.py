"""What an eval run records, and the metrics computed from it.

A run file (evals/results/*.json) holds every case's predictions and match outcome,
not just the totals, so any metric can be recomputed later and two runs can be
compared case by case.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.evals.dataset import PlantedBug
from app.evals.matching import CaseMatch, FalsePositiveReason, Prediction
from app.review.models import SEVERITY_RANK


class CaseResult(BaseModel):
    id: str
    kind: Literal["bug", "clean"]
    language: str
    planted: list[PlantedBug]
    predictions: list[Prediction]
    matched: list[tuple[int, int]] = Field(default_factory=list)
    false_positives: list[tuple[int, FalsePositiveReason]] = Field(default_factory=list)
    missed: list[int] = Field(default_factory=list)
    dropped_by_validator: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0  # total LLM time for this case (retries included)
    llm_calls: int = 0
    attempts: int = 1  # >1 when an LLM outage made the runner re-run the case
    errors: list[str] = Field(default_factory=list)

    @classmethod
    def from_match(cls, match: CaseMatch, **fields: object) -> "CaseResult":
        outcome = {
            "matched": match.matched,
            "false_positives": match.false_positives,
            "missed": match.missed,
        }
        return cls.model_validate(fields | outcome)


class CategoryMetrics(BaseModel):
    planted: int = 0  # bugs of this category in the dataset
    found: int = 0
    predicted: int = 0  # comments filed under this category
    correct: int = 0  # ...that matched a bug
    recall: float | None = None
    precision: float | None = None
    f1: float | None = None


class Metrics(BaseModel):
    cases: int
    bug_cases: int
    clean_cases: int
    planted_bugs: int
    comments: int
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None
    fp_per_clean_case: float | None
    avg_comments_per_case: float
    fp_reasons: dict[str, int]
    per_category: dict[str, CategoryMetrics]
    # Of the bugs found, how often the comment's severity equals the planted one, and
    # how often it is at most one level off (critical/high/medium/low/info).
    severity_exact: float | None = None
    severity_within_one: float | None = None
    cost_usd_total: float
    cost_usd_avg: float
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    input_tokens: int
    output_tokens: int
    cases_with_errors: int


class RunResult(BaseModel):
    created_at: datetime
    prompt_version: str
    prompt_sha: str  # hash of the prompt files, so an edited "v1" is detectable
    provider: str
    model: str
    temperature: float
    cases_pattern: str
    line_tolerance: int
    category_equivalences: list[list[str]]
    cases: list[CaseResult]
    metrics: Metrics


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def f1_score(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def percentile(values: Sequence[int], pct: float) -> int | None:
    """Nearest-rank percentile: always an observed value, no interpolation."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def compute_metrics(cases: Sequence[CaseResult]) -> Metrics:
    tp = sum(len(c.matched) for c in cases)
    fp = sum(len(c.false_positives) for c in cases)
    fn = sum(len(c.missed) for c in cases)
    comments = sum(len(c.predictions) for c in cases)
    planted = sum(len(c.planted) for c in cases)
    clean = [c for c in cases if c.kind == "clean"]
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)

    per_category: defaultdict[str, CategoryMetrics] = defaultdict(CategoryMetrics)
    for case in cases:
        found = {b for _, b in case.matched}
        correct = {p for p, _ in case.matched}
        for b, bug in enumerate(case.planted):
            per_category[bug.category].planted += 1
            per_category[bug.category].found += b in found
        for p, prediction in enumerate(case.predictions):
            per_category[prediction.category].predicted += 1
            per_category[prediction.category].correct += p in correct
    for metrics in per_category.values():
        metrics.recall = ratio(metrics.found, metrics.planted)
        metrics.precision = ratio(metrics.correct, metrics.predicted)
        metrics.f1 = f1_score(metrics.precision, metrics.recall)

    rank: dict[str, int] = {str(k): v for k, v in SEVERITY_RANK.items()}  # plain-text keys
    gaps = [
        abs(rank[case.predictions[p].severity] - rank[case.planted[b].severity])
        for case in cases
        for p, b in case.matched
        if case.predictions[p].severity in rank
    ]
    latencies = [c.latency_ms for c in cases if c.llm_calls]
    cost = sum(c.cost_usd for c in cases)
    return Metrics(
        cases=len(cases),
        bug_cases=len(cases) - len(clean),
        clean_cases=len(clean),
        planted_bugs=planted,
        comments=comments,
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1_score(precision, recall),
        fp_per_clean_case=(
            sum(len(c.false_positives) for c in clean) / len(clean) if clean else None
        ),
        avg_comments_per_case=comments / len(cases) if cases else 0.0,
        fp_reasons=dict(Counter(reason for c in cases for _, reason in c.false_positives)),
        per_category=dict(sorted(per_category.items())),
        severity_exact=ratio(sum(g == 0 for g in gaps), len(gaps)),
        severity_within_one=ratio(sum(g <= 1 for g in gaps), len(gaps)),
        cost_usd_total=cost,
        cost_usd_avg=cost / len(cases) if cases else 0.0,
        latency_p50_ms=percentile(latencies, 50),
        latency_p95_ms=percentile(latencies, 95),
        input_tokens=sum(c.input_tokens for c in cases),
        output_tokens=sum(c.output_tokens for c in cases),
        cases_with_errors=sum(1 for c in cases if c.errors),
    )
