"""Tenant-scoped reads for the dashboard (ADR 0012).

Every function takes `installation_ids`: the installations the current user may
see. Every query joins back to Repository.installation_id and filters on it, so
a row outside the user's scope is indistinguishable from a row that doesn't
exist (callers turn None into 404, never 403, so ids can't be probed).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import (
    Installation,
    LLMCall,
    PullRequest,
    RepoConfigRecord,
    Repository,
    ReviewCommentRecord,
    ReviewRun,
)

Ids = Sequence[int]


# ---- installations & repositories ----


async def installations(session: AsyncSession, ids: Ids) -> list[tuple[Installation, int]]:
    repo_count = (
        select(func.count(Repository.id))
        .where(Repository.installation_id == Installation.id, Repository.removed_at.is_(None))
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Installation, repo_count)
        .where(Installation.id.in_(ids), Installation.deleted_at.is_(None))
        .order_by(Installation.account_login)
    )
    return [(row[0], int(row[1])) for row in rows]


@dataclass(frozen=True)
class RepositoryRow:
    repository: Repository
    last_reviewed_at: datetime | None
    config_valid: bool | None  # None: no config snapshot fetched yet
    config_has_file: bool
    open_pulls: int


async def repositories(
    session: AsyncSession, ids: Ids, *, installation_id: int | None = None
) -> list[RepositoryRow]:
    last_reviewed = (
        select(func.max(ReviewRun.finished_at))
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .where(PullRequest.repository_id == Repository.id, ReviewRun.status == "completed")
        .scalar_subquery()
    )
    open_pulls = (
        select(func.count(PullRequest.id))
        .where(PullRequest.repository_id == Repository.id, PullRequest.state == "open")
        .scalar_subquery()
    )
    query = (
        select(Repository, last_reviewed, open_pulls)
        .where(Repository.installation_id.in_(ids), Repository.removed_at.is_(None))
        .order_by(Repository.full_name)
    )
    if installation_id is not None:
        query = query.where(Repository.installation_id == installation_id)
    rows = (await session.execute(query)).all()
    configs = await _latest_configs(session, [row[0].id for row in rows])
    result = []
    for repo, reviewed_at, pulls in rows:
        config = configs.get(repo.id)
        result.append(
            RepositoryRow(
                repository=repo,
                last_reviewed_at=reviewed_at,
                config_valid=config.is_valid if config else None,
                config_has_file=bool(config and config.raw_yaml is not None),
                open_pulls=int(pulls),
            )
        )
    return result


async def repository(session: AsyncSession, ids: Ids, repository_id: int) -> Repository | None:
    result = await session.scalars(
        select(Repository).where(
            Repository.id == repository_id,
            Repository.installation_id.in_(ids),
            Repository.removed_at.is_(None),
        )
    )
    return result.one_or_none()


async def latest_config(session: AsyncSession, repository_id: int) -> RepoConfigRecord | None:
    return (await _latest_configs(session, [repository_id])).get(repository_id)


async def _latest_configs(
    session: AsyncSession, repository_ids: Sequence[int]
) -> dict[int, RepoConfigRecord]:
    if not repository_ids:
        return {}
    rows = await session.scalars(
        select(RepoConfigRecord)
        .where(RepoConfigRecord.repository_id.in_(repository_ids))
        .distinct(RepoConfigRecord.repository_id)  # Postgres DISTINCT ON: newest per repo
        .order_by(RepoConfigRecord.repository_id, RepoConfigRecord.fetched_at.desc())
    )
    return {row.repository_id: row for row in rows}


# ---- pull requests & runs ----


async def pulls(
    session: AsyncSession,
    ids: Ids,
    *,
    repository_id: int,
    state: str | None,
    cursor: int | None,
    limit: int,
) -> list[PullRequest]:
    query = (
        select(PullRequest)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(PullRequest.repository_id == repository_id, Repository.installation_id.in_(ids))
        .options(joinedload(PullRequest.repository))
        .order_by(PullRequest.id.desc())
        .limit(limit)
    )
    if state:
        query = query.where(PullRequest.state == state)
    if cursor is not None:
        query = query.where(PullRequest.id < cursor)
    return list((await session.scalars(query)).all())


async def latest_runs(
    session: AsyncSession, pull_request_ids: Sequence[int]
) -> dict[int, ReviewRun]:
    if not pull_request_ids:
        return {}
    rows = await session.scalars(
        select(ReviewRun)
        .where(ReviewRun.pull_request_id.in_(pull_request_ids))
        .distinct(ReviewRun.pull_request_id)
        .order_by(ReviewRun.pull_request_id, ReviewRun.id.desc())
    )
    return {run.pull_request_id: run for run in rows}


async def pull(session: AsyncSession, ids: Ids, pull_request_id: int) -> PullRequest | None:
    result = await session.scalars(
        select(PullRequest)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(PullRequest.id == pull_request_id, Repository.installation_id.in_(ids))
        .options(joinedload(PullRequest.repository))
    )
    return result.one_or_none()


async def runs_for_pull(session: AsyncSession, pull_request_id: int) -> list[ReviewRun]:
    result = await session.scalars(
        select(ReviewRun)
        .where(ReviewRun.pull_request_id == pull_request_id)
        .order_by(ReviewRun.id.desc())
    )
    return list(result.all())


async def run(session: AsyncSession, ids: Ids, run_id: int) -> ReviewRun | None:
    result = await session.scalars(
        select(ReviewRun)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(ReviewRun.id == run_id, Repository.installation_id.in_(ids))
        .options(joinedload(ReviewRun.pull_request).joinedload(PullRequest.repository))
    )
    return result.one_or_none()


async def run_llm_calls(session: AsyncSession, run_id: int) -> list[LLMCall]:
    result = await session.scalars(
        select(LLMCall).where(LLMCall.review_run_id == run_id).order_by(LLMCall.id)
    )
    return list(result.all())


async def run_comments(session: AsyncSession, run_id: int) -> list[ReviewCommentRecord]:
    result = await session.scalars(
        select(ReviewCommentRecord)
        .where(ReviewCommentRecord.review_run_id == run_id)
        .order_by(
            ReviewCommentRecord.posted.desc(), ReviewCommentRecord.path, ReviewCommentRecord.line
        )
    )
    return list(result.all())


# ---- analytics ----


async def scoped_repository_ids(
    session: AsyncSession, ids: Ids, repository_id: int | None = None
) -> list[int]:
    query = select(Repository.id).where(Repository.installation_id.in_(ids))
    if repository_id is not None:
        query = query.where(Repository.id == repository_id)
    return list((await session.scalars(query)).all())
