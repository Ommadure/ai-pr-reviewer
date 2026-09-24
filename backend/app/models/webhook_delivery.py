from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebhookDelivery(Base):
    """One row per webhook GitHub sent us, keyed by the X-GitHub-Delivery header.

    The primary key doubles as the dedupe mechanism: a redelivery of the same
    event conflicts on insert, so we never process it twice.
    """

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event: Mapped[str] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(64))
    installation_id: Mapped[int | None] = mapped_column(BigInteger)  # GitHub's id
    # received | queued | ignored | processed | failed
    status: Mapped[str] = mapped_column(String(16))
    ignore_reason: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
