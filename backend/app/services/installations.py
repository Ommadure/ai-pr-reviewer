"""Keep our installations/repositories tables in sync with GitHub's installation events."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.github import events
from app.repositories import installations, repos


async def sync_installation(
    session: AsyncSession,
    installation: events.Installation,
    repositories: list[events.RepositoryRef],
) -> int:
    installation_id = await installations.upsert(
        session,
        github_installation_id=installation.id,
        account_login=installation.account.login,
        account_type=installation.account.type,
    )
    for repo in repositories:
        await repos.upsert(
            session,
            installation_id=installation_id,
            github_repo_id=repo.id,
            full_name=repo.full_name,
            private=repo.private,
        )
    return installation_id


async def ensure_repository(
    session: AsyncSession, installation_id: int, repository: events.Repository
) -> int:
    """Make sure the installation and repo rows exist before we store a PR.

    The App may have been installed before this server was running (or while it
    was down), in which case we never saw the installation event.
    """
    our_installation_id = await installations.upsert(
        session,
        github_installation_id=installation_id,
        account_login=repository.owner.login,
        account_type=repository.owner.type,
    )
    return await repos.upsert(
        session,
        installation_id=our_installation_id,
        github_repo_id=repository.id,
        full_name=repository.full_name,
        private=repository.private,
        default_branch=repository.default_branch,
    )
