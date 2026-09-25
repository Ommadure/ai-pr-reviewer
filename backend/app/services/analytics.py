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
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PullRequest, ReviewCommentRecord


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
