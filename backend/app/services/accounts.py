"""Dashboard users: login, token refresh, and which installations they may see.

Tenancy rule (ADR 0012): a user can see an installation's data only if GitHub's
`GET /user/installations`, called with *their* token, lists it. We cache that
answer for 5 minutes in user_installations; every dashboard query is then
scoped to those installation ids.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenCipher
from app.github.oauth import GitHubOAuth, OAuthError
from app.models import Installation, User, UserInstallation

log = structlog.get_logger()

INSTALLATIONS_CACHE_TTL = timedelta(minutes=5)
REFRESH_MARGIN = timedelta(minutes=5)


class ReauthenticationRequired(Exception):
    """The user's GitHub token can't be used or refreshed: they must log in again."""


@dataclass(frozen=True)
class AccountServices:
    oauth: GitHubOAuth
    cipher: TokenCipher


async def complete_login(session: AsyncSession, services: AccountServices, code: str) -> User:
    tokens = await services.oauth.exchange_code(code)
    profile = await services.oauth.get_user(tokens.access_token)
    now = datetime.now(UTC)
    values = {
        "login": profile.login,
        "avatar_url": profile.avatar_url,
        "access_token_enc": services.cipher.encrypt(tokens.access_token),
        "refresh_token_enc": (
            services.cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None
        ),
        "token_expires_at": tokens.expires_at,
        "last_login_at": now,
        "updated_at": now,
    }
    stmt = (
        insert(User)
        .values(github_user_id=profile.id, **values)
        .on_conflict_do_update(index_elements=[User.github_user_id], set_=values)
        .returning(User)
    )
    user = (await session.execute(stmt, execution_options={"populate_existing": True})).scalar_one()
    # Forget the old installation list: logging in is a natural moment to re-check access.
    await session.execute(delete(UserInstallation).where(UserInstallation.user_id == user.id))
    await session.commit()
    return user


async def access_token(session: AsyncSession, services: AccountServices, user: User) -> str:
    """A usable GitHub token for this user, refreshing it first if it's about to expire."""
    now = datetime.now(UTC)
    expiring = user.token_expires_at is not None and user.token_expires_at - REFRESH_MARGIN <= now
    if not expiring:
        token = services.cipher.decrypt(user.access_token_enc)
        if token is None:
            raise ReauthenticationRequired("stored token can't be decrypted")
        return token

    refresh = services.cipher.decrypt(user.refresh_token_enc) if user.refresh_token_enc else None
    if refresh is None:
        raise ReauthenticationRequired("token expired and no refresh token")
    try:
        tokens = await services.oauth.refresh(refresh)
    except OAuthError as exc:
        raise ReauthenticationRequired(f"refresh rejected: {exc}") from exc
    user.access_token_enc = services.cipher.encrypt(tokens.access_token)
    user.refresh_token_enc = (
        services.cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None
    )
    user.token_expires_at = tokens.expires_at
    await session.commit()
    return tokens.access_token


async def accessible_installation_ids(
    session: AsyncSession, services: AccountServices, user: User
) -> list[int]:
    """Our installation ids this user may see (from cache, or asked fresh from GitHub)."""
    now = datetime.now(UTC)
    cached = (
        await session.execute(
            select(UserInstallation.installation_id, UserInstallation.refreshed_at).where(
                UserInstallation.user_id == user.id
            )
        )
    ).all()
    if cached and min(row.refreshed_at for row in cached) > now - INSTALLATIONS_CACHE_TTL:
        return [row.installation_id for row in cached]

    token = await access_token(session, services, user)
    github_ids = await services.oauth.installation_ids(token)
    ours = (
        await session.scalars(
            select(Installation.id).where(
                Installation.github_installation_id.in_(github_ids),
                Installation.deleted_at.is_(None),
            )
        )
    ).all()
    await session.execute(delete(UserInstallation).where(UserInstallation.user_id == user.id))
    if ours:
        await session.execute(
            insert(UserInstallation),
            [{"user_id": user.id, "installation_id": i, "refreshed_at": now} for i in ours],
        )
    await session.commit()
    log.info("tenancy.refreshed", user_id=user.id, installations=len(ours))
    return list(ours)
