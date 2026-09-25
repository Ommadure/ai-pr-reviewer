"""Usefulness metrics for posted comments (definitions also in the README).

- posted:        comments ReviewPilot actually posted on GitHub
- helpful:       posted comments with at least one 👍, OR whose flagged code the
                 author later changed ("addressed")
- negative:      posted comments with at least one 👎
- helpful rate:  helpful / posted
- negative rate: negative / posted

A comment can be both helpful and negative (mixed reactions); the rates are
independent, not complementary. Bot reactions are never counted.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PullRequest, Repository, ReviewCommentRecord, ReviewRun


@dataclass
class Rates:
    posted: int = 0
    helpful: int = 0
    negative: int = 0

    @property
    def helpful_rate(self) -> float | None:
        return self.helpful / self.posted if self.posted else None

    @property
    def negative_rate(self) -> float | None:
        return self.negative / self.posted if self.posted else None

    def add(self, other: "Rates") -> None:
        self.posted += other.posted
        self.helpful += other.helpful
        self.negative += other.negative


@dataclass
class FeedbackMetrics:
    overall: Rates = field(default_factory=Rates)
    by_category: dict[str, Rates] = field(default_factory=dict)
    by_severity: dict[str, Rates] = field(default_factory=dict)


async def feedback_metrics(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    repository_ids: Sequence[int] | None = None,
) -> FeedbackMetrics:
    c = ReviewCommentRecord
    helpful = case(((c.thumbs_up > 0) | (c.status == "addressed"), 1), else_=0)
    negative = case((c.thumbs_down > 0, 1), else_=0)
    query = (
        select(
            c.category,
            c.severity,
            func.count().label("posted"),
            func.sum(helpful).label("helpful"),
            func.sum(negative).label("negative"),
        )
        .where(c.posted.is_(True))
        .group_by(c.category, c.severity)
    )
    if since is not None:
        query = query.where(c.created_at >= since)
    if repository_ids is not None:
        query = query.join(PullRequest, PullRequest.id == c.pull_request_id).where(
            PullRequest.repository_id.in_(repository_ids)
        )

    metrics = FeedbackMetrics()
    by_category: defaultdict[str, Rates] = defaultdict(Rates)
    by_severity: defaultdict[str, Rates] = defaultdict(Rates)
    for row in await session.execute(query):
        rates = Rates(int(row.posted), int(row.helpful or 0), int(row.negative or 0))
        metrics.overall.add(rates)
        by_category[row.category].add(rates)
        by_severity[row.severity].add(rates)
    metrics.by_category, metrics.by_severity = dict(by_category), dict(by_severity)
    return metrics


@dataclass
class Overview:
    runs_total: int = 0
    runs_completed: int = 0
    runs_failed: int = 0
    comments_posted: int = 0
    cost_usd_total: float = 0.0
    avg_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    reviews_per_day: list[tuple[date, int]] = field(default_factory=list)
    cost_per_day: list[tuple[date, float]] = field(default_factory=list)
    comments_by_severity: dict[str, int] = field(default_factory=dict)
    comments_by_category: dict[str, int] = field(default_factory=dict)
    top_files: list[tuple[str, str, int]] = field(default_factory=list)  # repo, path, count
    feedback: FeedbackMetrics = field(default_factory=FeedbackMetrics)


async def overview(
    session: AsyncSession, *, repository_ids: Sequence[int], since: datetime
) -> Overview:
    """Everything the Overview/Analytics pages chart, for the given repos and window."""
    result = Overview()
    if not repository_ids:
        return result
    in_scope = PullRequest.repository_id.in_(repository_ids)
    runs = (
        select(ReviewRun)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .where(in_scope, ReviewRun.created_at >= since)
        .subquery()
    )

    totals = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(runs.c.status == "completed"),
                func.count().filter(runs.c.status == "failed"),
                func.coalesce(func.sum(runs.c.cost_usd), 0),
                func.avg(runs.c.latency_ms).filter(runs.c.status == "completed"),
                func.percentile_cont(0.95)
                .within_group(runs.c.latency_ms)
                .filter(runs.c.status == "completed"),
            ).select_from(runs)
        )
    ).one()
    result.runs_total, result.runs_completed, result.runs_failed = (
        int(totals[0]),
        int(totals[1]),
        int(totals[2]),
    )
    result.cost_usd_total = float(totals[3])
    result.avg_latency_ms = float(totals[4]) if totals[4] is not None else None
    result.p95_latency_ms = float(totals[5]) if totals[5] is not None else None

    day = cast(func.date_trunc("day", runs.c.created_at), Date)
    per_day = await session.execute(
        select(
            day,
            func.count().filter(runs.c.status == "completed"),
            func.coalesce(func.sum(runs.c.cost_usd), 0),
        )
        .select_from(runs)
        .group_by(day)
        .order_by(day)
    )
    for d, reviews, cost in per_day:
        result.reviews_per_day.append((d, int(reviews)))
        result.cost_per_day.append((d, float(cost)))

    c = ReviewCommentRecord
    posted = (
        select(c.severity, c.category, c.path, Repository.full_name)
        .join(PullRequest, PullRequest.id == c.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(in_scope, c.posted.is_(True), c.created_at >= since)
        .subquery()
    )
    for severity, count in await session.execute(
        select(posted.c.severity, func.count()).group_by(posted.c.severity)
    ):
        result.comments_by_severity[severity] = int(count)
    for category, count in await session.execute(
        select(posted.c.category, func.count()).group_by(posted.c.category)
    ):
        result.comments_by_category[category] = int(count)
    result.comments_posted = sum(result.comments_by_severity.values())
    top = await session.execute(
        select(posted.c.full_name, posted.c.path, func.count().label("n"))
        .group_by(posted.c.full_name, posted.c.path)
        .order_by(func.count().desc(), posted.c.path)
        .limit(10)
    )
    result.top_files = [(repo, path, int(n)) for repo, path, n in top]

    result.feedback = await feedback_metrics(session, since=since, repository_ids=repository_ids)
    return result
