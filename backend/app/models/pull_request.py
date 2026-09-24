from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin
from app.models.repository import Repository


class PullRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        Index("uq_pull_requests_repository_id_number", "repository_id", "number", unique=True),
        Index("ix_pull_requests_repository_id_state", "repository_id", "state"),
    )

    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    number: Mapped[int] = mapped_column(Integer)
    github_pr_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(1024))
    author_login: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(16))  # open | closed | merged
    draft: Mapped[bool] = mapped_column(Boolean)
    base_ref: Mapped[str] = mapped_column(String(255))
    base_sha: Mapped[str] = mapped_column(String(40))
    head_sha: Mapped[str] = mapped_column(String(40))
    last_reviewed_sha: Mapped[str | None] = mapped_column(String(40))
    paused: Mapped[bool] = mapped_column(Boolean, server_default=false())

    repository: Mapped[Repository] = relationship()
