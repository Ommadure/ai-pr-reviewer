from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.installation import Installation


class Repository(IdMixin, TimestampMixin, Base):
    __tablename__ = "repositories"

    installation_id: Mapped[int] = mapped_column(ForeignKey("installations.id"), index=True)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(255))  # "owner/name"
    # Installation payloads don't include it; filled from the first pull_request event.
    default_branch: Mapped[str | None] = mapped_column(String(255))
    private: Mapped[bool] = mapped_column(Boolean)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    installation: Mapped["Installation"] = relationship(back_populates="repositories")

    @property
    def owner_and_name(self) -> tuple[str, str]:
        owner, name = self.full_name.split("/", 1)
        return owner, name
