"""GitHub login for the dashboard.

GET  /auth/github/login     → redirect to GitHub, with a CSRF `state`
GET  /auth/github/callback  → verify state, exchange code, create session, back to the app
POST /auth/logout           → drop the session cookie
GET  /me                    → who is signed in
"""

from typing import Annotated
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.deps import AccountsDep, CurrentUser, SessionDep, SettingsDep, get_session_tokens
from app.core.config import Settings
from app.core.security import (
    SESSION_COOKIE,
    STATE_COOKIE,
    STATE_TTL_SECONDS,
    OAuthState,
    SessionTokens,
)
from app.github.client import GitHubError
from app.github.oauth import OAuthError
from app.services.accounts import complete_login

router = APIRouter(tags=["auth"])
log = structlog.get_logger()
STATE_COOKIE_PATH = "/api/v1/auth"


class MeResponse(BaseModel):
    id: int
    login: str
    avatar_url: str | None


@router.get("/auth/github/login")
async def login(settings: SettingsDep, accounts: AccountsDep) -> RedirectResponse:
    state, cookie = OAuthState(settings.session_secret.get_secret_value()).issue()
    response = RedirectResponse(accounts.oauth.authorize_url(state), status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE,
        cookie,
        max_age=STATE_TTL_SECONDS,
        path=STATE_COOKIE_PATH,  # only ever sent back to the callback
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",  # sent on GitHub's top-level redirect back to us
    )
    return response


@router.get("/auth/github/callback")
async def callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    accounts: AccountsDep,
    tokens: Annotated[SessionTokens, Depends(get_session_tokens)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    state_ok = OAuthState(settings.session_secret.get_secret_value()).matches(
        state, request.cookies.get(STATE_COOKIE)
    )
    if error or not code:
        return _back_to_app(settings, error="denied")
    if not state_ok:
        # Someone else started this login (CSRF), or the 10-minute window passed.
        log.warning("auth.state_mismatch")
        return _back_to_app(settings, error="state")
    try:
        user = await complete_login(session, accounts, code)
    except (OAuthError, GitHubError):
        log.exception("auth.login_failed")
        return _back_to_app(settings, error="github")

    response = _back_to_app(settings)
    response.set_cookie(
        SESSION_COOKIE,
        tokens.issue(user.id),
        max_age=tokens.ttl_seconds,
        path="/",
        httponly=True,  # JavaScript can't read it, so XSS can't steal the session
        secure=settings.secure_cookies,
        samesite="lax",  # not sent on cross-site POSTs: CSRF protection for the API
    )
    log.info("auth.login", user_id=user.id, login=user.login)
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(settings: SettingsDep) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.secure_cookies, samesite="lax")
    return response


@router.get("/me")
async def me(user: CurrentUser) -> MeResponse:
    return MeResponse(id=user.id, login=user.login, avatar_url=user.avatar_url)


def _back_to_app(settings: Settings, *, error: str | None = None) -> RedirectResponse:
    target = settings.frontend_url.rstrip("/") + "/"
    if error:
        target += "login?" + urlencode({"error": error})
    response = RedirectResponse(target, status.HTTP_302_FOUND)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)  # single use
    return response
