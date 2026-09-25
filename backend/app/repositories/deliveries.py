"""webhook_deliveries access. The primary key (GitHub's delivery id) is our dedupe key."""

from datetime import datetime

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WebhookDelivery


async def try_claim(
    session: AsyncSession,
    *,
    delivery_id: str,
    event: str,
    action: str | None,
    installation_id: int | None,
) -> bool:
    """Record a new delivery. Returns False if we've already handled this delivery.

    A previously *failed* delivery can be claimed again, so "Redeliver" in
    GitHub's UI works as a manual retry.
    """
    stmt = (
        insert(WebhookDelivery)
        .values(
            delivery_id=delivery_id,
            event=event,
            action=action,
            installation_id=installation_id,
            status="received",
        )
        .on_conflict_do_update(
            index_elements=[WebhookDelivery.delivery_id],
            set_={"status": "received", "error": None},
            where=WebhookDelivery.status == "failed",
        )
        .returning(WebhookDelivery.delivery_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def set_status(
    session: AsyncSession,
    delivery_id: str,
    status: str,
    *,
    ignore_reason: str | None = None,
    error: str | None = None,
) -> None:
    await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.delivery_id == delivery_id)
        .values(status=status, ignore_reason=ignore_reason, error=error)
    )


async def record_failure(
    session: AsyncSession,
    *,
    delivery_id: str,
    event: str,
    action: str | None,
    installation_id: int | None,
    error: str,
) -> None:
    """Used after a rollback, when the original insert is gone too."""
    stmt = (
        insert(WebhookDelivery)
        .values(
            delivery_id=delivery_id,
            event=event,
            action=action,
            installation_id=installation_id,
            status="failed",
            error=error,
        )
        .on_conflict_do_update(
            index_elements=[WebhookDelivery.delivery_id],
            set_={"status": "failed", "error": error},
        )
    )
    await session.execute(stmt)


async def delete_received_before(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(
        delete(WebhookDelivery).where(WebhookDelivery.received_at < cutoff)
    )
    return result.rowcount or 0  # type: ignore[attr-defined]
