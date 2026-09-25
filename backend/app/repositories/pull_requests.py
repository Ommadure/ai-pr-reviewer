from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.github.events import PullRequest as PullRequestPayload
from app.models import PullRequest, Repository


async def upsert_from_payload(
    session: AsyncSession, *, repository_id: int, payload: PullRequestPayload
) -> PullRequest:
    values = {
        "github_pr_id": payload.id,
        "title": payload.title,
        "author_login": payload.user.login,
        "state": "merged" if payload.merged else payload.state,
        "draft": payload.draft,
        "base_ref": payload.base.ref,
        "base_sha": payload.base.sha,
        "head_sha": payload.head.sha,
    }
    stmt = (
        insert(PullRequest)
        .values(repository_id=repository_id, number=payload.number, **values)
        .on_conflict_do_update(
            index_elements=[PullRequest.repository_id, PullRequest.number],
            set_={**values, "updated_at": func.now()},
        )
        .returning(PullRequest)
    )
    # populate_existing: refresh the identity map, don't return a stale cached object.
    result = await session.execute(stmt, execution_options={"populate_existing": True})
    return result.scalar_one()


async def get_with_repository(session: AsyncSession, pull_request_id: int) -> PullRequest | None:
    """PR plus its repository and installation, loaded in one query."""
    result = await session.scalars(
        select(PullRequest)
        .where(PullRequest.id == pull_request_id)
        .options(joinedload(PullRequest.repository).joinedload(Repository.installation))
    )
    return result.one_or_none()


async def get_by_number(
    session: AsyncSession, repository_id: int, number: int
) -> PullRequest | None:
    result = await session.scalars(
        select(PullRequest).where(
            PullRequest.repository_id == repository_id, PullRequest.number == number
        )
    )
    return result.one_or_none()
