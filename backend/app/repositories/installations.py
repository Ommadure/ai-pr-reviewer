from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Installation


async def upsert(
    session: AsyncSession, *, github_installation_id: int, account_login: str, account_type: str
) -> int:
    """Insert or refresh an installation; a re-install clears deleted_at. Returns our id."""
    stmt = (
        insert(Installation)
        .values(
            github_installation_id=github_installation_id,
            account_login=account_login,
            account_type=account_type,
        )
        .on_conflict_do_update(
            index_elements=[Installation.github_installation_id],
            set_={
                "account_login": account_login,
                "account_type": account_type,
                "deleted_at": None,
                "updated_at": func.now(),
            },
        )
        .returning(Installation.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def get_by_github_id(
    session: AsyncSession, github_installation_id: int
) -> Installation | None:
    result = await session.scalars(
        select(Installation).where(Installation.github_installation_id == github_installation_id)
    )
    return result.one_or_none()


async def set_suspended(
    session: AsyncSession, github_installation_id: int, suspended_at: datetime | None
) -> None:
    await session.execute(
        update(Installation)
        .where(Installation.github_installation_id == github_installation_id)
        .values(suspended_at=suspended_at, updated_at=func.now())
    )


async def mark_deleted(session: AsyncSession, github_installation_id: int) -> None:
    # Soft delete: review history stays queryable after an uninstall.
    await session.execute(
        update(Installation)
        .where(Installation.github_installation_id == github_installation_id)
        .values(deleted_at=func.now(), updated_at=func.now())
    )
