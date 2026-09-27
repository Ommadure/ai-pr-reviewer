from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin


class Job(IdMixin, TimestampMixin, Base):
    """Background work: the queue is this table (ADR 0016).

    A job is inserted in the same transaction as the rows it works on, so it can't be
    lost between "saved" and "enqueued". `lock_key` serializes jobs that must not run
    at the same time: at most one *running* job per key, enforced by a unique index.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index(
            "uq_jobs_running_lock_key",
            "lock_key",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    kind: Mapped[str] = mapped_column(String(32))  # review | command
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    lock_key: Mapped[str | None] = mapped_column(String(64))  # e.g. "pr:42"
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")  # failed attempts
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
