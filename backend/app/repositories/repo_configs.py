"""repo_configs: .reviewpilot.yml snapshots, keyed by default-branch commit."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.repo_config import ParsedRepoConfig
from app.models import RepoConfigRecord


async def get(
    session: AsyncSession, repository_id: int, commit_sha: str
) -> RepoConfigRecord | None:
    result = await session.scalars(
        select(RepoConfigRecord).where(
            RepoConfigRecord.repository_id == repository_id,
            RepoConfigRecord.commit_sha == commit_sha,
        )
    )
    return result.one_or_none()


async def latest(session: AsyncSession, repository_id: int) -> RepoConfigRecord | None:
    result = await session.scalars(
        select(RepoConfigRecord)
        .where(RepoConfigRecord.repository_id == repository_id)
        .order_by(RepoConfigRecord.fetched_at.desc())
        .limit(1)
    )
    return result.one_or_none()


async def save(
    session: AsyncSession,
    *,
    repository_id: int,
    commit_sha: str,
    raw_yaml: str | None,
    parsed: ParsedRepoConfig,
) -> None:
    # Two workers can race to cache the same commit; the first one wins, harmlessly.
    await session.execute(
        insert(RepoConfigRecord)
        .values(
            repository_id=repository_id,
            commit_sha=commit_sha,
            raw_yaml=raw_yaml,
            parsed=parsed.config.model_dump(mode="json"),
            is_valid=parsed.is_valid,
            errors=parsed.errors,
            warnings=parsed.warnings,
        )
        .on_conflict_do_nothing(
            index_elements=[RepoConfigRecord.repository_id, RepoConfigRecord.commit_sha]
        )
    )
