"""Load a repository's .reviewpilot.yml, always from its default branch (ADR 0009).

Reading it from the PR branch would let a PR author switch the reviewer off, or
loosen its rules, for their own PR. Results are cached per default-branch
commit in repo_configs, so an unchanged config costs one cheap API call.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.repo_config import (
    MAX_CONFIG_BYTES,
    ParsedRepoConfig,
    RepoConfig,
    parse_repo_config,
)
from app.github.client import GitHubClient
from app.models import Repository
from app.repositories import repo_configs

CONFIG_PATH = ".reviewpilot.yml"


@dataclass(frozen=True)
class LoadedConfig:
    parsed: ParsedRepoConfig
    commit_sha: str
    from_cache: bool

    @property
    def config(self) -> RepoConfig:
        return self.parsed.config


async def load_repo_config(
    sessionmaker: async_sessionmaker[AsyncSession],
    github: GitHubClient,
    repository: Repository,
    default_branch: str,
) -> LoadedConfig:
    owner, name = repository.owner_and_name
    commit_sha = await github.get_branch_head_sha(owner, name, default_branch)

    async with sessionmaker() as session:
        cached = await repo_configs.get(session, repository.id, commit_sha)
    if cached is not None:
        parsed = ParsedRepoConfig(
            RepoConfig.model_validate(cached.parsed),
            is_valid=cached.is_valid,
            warnings=list(cached.warnings),
            errors=list(cached.errors),
        )
        return LoadedConfig(parsed, commit_sha, from_cache=True)

    try:
        raw = await github.get_file_text(
            owner, name, CONFIG_PATH, ref=commit_sha, max_bytes=MAX_CONFIG_BYTES
        )
        parsed = parse_repo_config(raw)
    except ValueError as exc:  # too large
        raw, parsed = None, ParsedRepoConfig(RepoConfig(), is_valid=False, errors=[str(exc)])

    async with sessionmaker() as session:
        await repo_configs.save(
            session, repository_id=repository.id, commit_sha=commit_sha, raw_yaml=raw, parsed=parsed
        )
        await session.commit()
    return LoadedConfig(parsed, commit_sha, from_cache=False)
