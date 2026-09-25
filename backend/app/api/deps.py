"""Request-scoped dependencies for the dashboard API: who's asking, and what they may see."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter, RedisRateLimiter
from app.core.redis import get_redis
from app.core.security import SESSION_COOKIE, SessionTokens, TokenCipher
from app.db.session import get_session
from app.github.client import GitHubError
from app.github.oauth import GitHubOAuth
from app.models import User
from app.services.accounts import (
    AccountServices,
    ReauthenticationRequired,
    accessible_installation_ids,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_session_tokens(settings: SettingsDep) -> SessionTokens:
    return SessionTokens(
        secret=settings.session_secret.get_secret_value(),
        ttl_seconds=settings.session_ttl_days * 24 * 3600,
    )


def get_account_services(request: Request, settings: SettingsDep) -> AccountServices:
    # HTTP clients live on app.state (created in the lifespan) so connections are reused.
    oauth = GitHubOAuth(
        client_id=settings.github_app_client_id,
        client_secret=settings.github_app_client_secret.get_secret_value(),
        redirect_uri=settings.oauth_redirect_uri,
        web_http=request.app.state.github_web_http,
        api_http=request.app.state.github_api_http,
    )
    return AccountServices(oauth, TokenCipher(settings.encryption_key.get_secret_value()))


def get_rate_limiter() -> RateLimiter:
    return RedisRateLimiter(get_redis())


AccountsDep = Annotated[AccountServices, Depends(get_account_services)]


async def get_current_user(
    request: Request,
    session: SessionDep,
    tokens: Annotated[SessionTokens, Depends(get_session_tokens)],
) -> User:
    user_id = tokens.user_id(request.cookies.get(SESSION_COOKIE))
    user = await session.get(User, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass(frozen=True)
class Scope:
    """What the current request may see. Every dashboard query goes through this."""

    user: User
    installation_ids: list[int]


async def get_scope(user: CurrentUser, session: SessionDep, accounts: AccountsDep) -> Scope:
    try:
        ids = await accessible_installation_ids(session, accounts, user)
    except ReauthenticationRequired as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again") from exc
    except GitHubError as exc:
        if exc.status_code == 401:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again") from exc
        # Fail closed: without GitHub's answer we can't know what this user may see.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub unavailable") from exc
    return Scope(user, ids)


ScopeDep = Annotated[Scope, Depends(get_scope)]
