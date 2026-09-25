"""Review history: one row per review attempt, its LLM calls and its comments.

This is what the dashboard (Phase 5) and the feedback loop (Phase 4) read, and
what makes "every run visible with tokens, cost and latency" true.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin
from app.models.pull_request import PullRequest

Money = Numeric(12, 6)  # USD; exact decimal arithmetic for sums, never float drift


class RepoConfigRecord(IdMixin, TimestampMixin, Base):
    """.reviewpilot.yml as it was at one default-branch commit (a cache + audit trail)."""

    __tablename__ = "repo_configs"
    __table_args__ = (
        Index(
            "uq_repo_configs_repository_id_commit_sha", "repository_id", "commit_sha", unique=True
        ),
    )

    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    commit_sha: Mapped[str] = mapped_column(String(40))
    raw_yaml: Mapped[str | None] = mapped_column(Text)  # None = the repo has no config file
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_valid: Mapped[bool] = mapped_column(Boolean)
    errors: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    warnings: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ReviewRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "review_runs"
    __table_args__ = (
        Index(
            "ix_review_runs_pull_request_id_created_at", "pull_request_id", text("created_at DESC")
        ),
        Index("ix_review_runs_status_started_at", "status", "started_at"),
    )

    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id"))
    # opened | reopened | ready_for_review | synchronize | command | manual
    trigger: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))  # full | incremental
    base_sha: Mapped[str] = mapped_column(String(40))
    head_sha: Mapped[str] = mapped_column(String(40))
    from_sha: Mapped[str | None] = mapped_column(String(40))  # incremental start (Phase 4)
    # queued | running | completed | failed | superseded | skipped
    status: Mapped[str] = mapped_column(String(16))
    skip_reason: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(128))

    files_total: Mapped[int] = mapped_column(Integer, server_default="0")
    files_reviewed: Mapped[int] = mapped_column(Integer, server_default="0")
    files_skipped: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    comments_generated: Mapped[int] = mapped_column(Integer, server_default="0")
    comments_posted: Mapped[int] = mapped_column(Integer, server_default="0")
    comments_dropped_invalid_line: Mapped[int] = mapped_column(Integer, server_default="0")
    comments_dropped_duplicate: Mapped[int] = mapped_column(Integer, server_default="0")
    comments_dropped_low_confidence: Mapped[int] = mapped_column(Integer, server_default="0")
    comments_dropped_over_limit: Mapped[int] = mapped_column(Integer, server_default="0")

    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Money, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    github_review_id: Mapped[int | None] = mapped_column(BigInteger)
    check_run_id: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pull_request: Mapped[PullRequest] = relationship()


class LLMCall(IdMixin, TimestampMixin, Base):
    __tablename__ = "llm_calls"

    review_run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(16))  # file_review | summary | repair
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Money)
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # success | error
    error: Mapped[str | None] = mapped_column(Text)


class ReviewCommentRecord(IdMixin, TimestampMixin, Base):
    """Every comment the engine produced: posted ones, and dropped ones with the reason."""

    __tablename__ = "review_comments"
    __table_args__ = (
        Index("ix_review_comments_pull_request_id", "pull_request_id"),
        Index("ix_review_comments_status_feedback_checked_at", "status", "feedback_checked_at"),
        # The database itself refuses to record the same problem as posted twice on one PR.
        Index(
            "uq_review_comments_pull_request_id_fingerprint_posted",
            "pull_request_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("posted"),
        ),
    )

    review_run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id"), index=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id"))
    path: Mapped[str] = mapped_column(String(1024))
    line: Mapped[int] = mapped_column(Integer)
    start_line: Mapped[int | None] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(8), server_default="RIGHT")
    severity: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, server_default="")
    suggestion: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), server_default="llm")  # llm | secret_scanner
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    code_snapshot: Mapped[str | None] = mapped_column(Text)
    posted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    drop_reason: Mapped[str | None] = mapped_column(String(64))
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger)
    # open | addressed | outdated  (Phase 4 feedback)
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    thumbs_up: Mapped[int] = mapped_column(Integer, server_default="0")
    thumbs_down: Mapped[int] = mapped_column(Integer, server_default="0")
    feedback_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
