"""repositories table access (module named repos.py to avoid repositories.repositories)."""

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Repository


async def upsert(
    session: AsyncSession,
    *,
    installation_id: int,
    github_repo_id: int,
    full_name: str,
    private: bool,
    default_branch: str | None = None,
) -> int:
    """Insert or refresh a repository (handles renames and re-adds). Returns our id."""
    insert_stmt = insert(Repository).values(
        installation_id=installation_id,
        github_repo_id=github_repo_id,
        full_name=full_name,
        private=private,
        default_branch=default_branch,
    )
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[Repository.github_repo_id],
        set_={
            "installation_id": installation_id,
            "full_name": full_name,
            "private": private,
            # Installation payloads lack default_branch: don't wipe a known value.
            "default_branch": func.coalesce(
                insert_stmt.excluded.default_branch, Repository.default_branch
            ),
            "removed_at": None,
            "updated_at": func.now(),
        },
    ).returning(Repository.id)
    return (await session.execute(stmt)).scalar_one()


async def mark_removed(session: AsyncSession, github_repo_ids: list[int]) -> None:
    if not github_repo_ids:
        return
    await session.execute(
        update(Repository)
        .where(Repository.github_repo_id.in_(github_repo_ids))
        .values(removed_at=func.now(), updated_at=func.now())
    )


async def get(session: AsyncSession, repository_id: int) -> Repository | None:
    result = await session.scalars(select(Repository).where(Repository.id == repository_id))
    return result.one_or_none()
