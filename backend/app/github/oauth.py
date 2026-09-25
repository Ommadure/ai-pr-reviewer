"""Dashboard login through the GitHub App's user-authorization (OAuth) flow.

1. /login redirects to github.com/login/oauth/authorize with a random `state`.
2. GitHub redirects back with `code` + `state`; we exchange the code for a user
   access token (+ refresh token: GitHub App user tokens expire after ~8 hours).
3. With that token we read who the user is (/user) and which installations of
   *this App* they can access (/user/installations): the basis of tenancy.
Reference: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.github.client import GitHubClient

GITHUB_WEB_URL = "https://github.com"


class OAuthError(Exception):
    """The code/refresh token was rejected (expired, reused, revoked...)."""


@dataclass(frozen=True)
class UserTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None  # None: the App has token expiry turned off


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str
    avatar_url: str | None


class GitHubOAuth:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        web_http: httpx.AsyncClient,
        api_http: httpx.AsyncClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._web = web_http
        self._api = api_http
        self._clock = clock

    def authorize_url(self, state: str) -> str:
        # GitHub Apps ignore `scope`: the App's permissions define what the token can do.
        query = urlencode(
            {"client_id": self._client_id, "redirect_uri": self._redirect_uri, "state": state}
        )
        return f"{GITHUB_WEB_URL}/login/oauth/authorize?{query}"

    async def exchange_code(self, code: str) -> UserTokens:
        return await self._token_request({"code": code, "redirect_uri": self._redirect_uri})

    async def refresh(self, refresh_token: str) -> UserTokens:
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def get_user(self, access_token: str) -> GitHubUser:
        data = (await self._client(access_token).request("GET", "/user")).json()
        return GitHubUser(
            id=int(data["id"]), login=data["login"], avatar_url=data.get("avatar_url")
        )

    async def installation_ids(self, access_token: str) -> set[int]:
        """GitHub ids of this App's installations the user can access."""
        client = self._client(access_token)
        ids: set[int] = set()
        page = 1
        while True:  # the response is an object ({total_count, installations}), not a list
            data = (
                await client.request(
                    "GET", "/user/installations", params={"per_page": 100, "page": page}
                )
            ).json()
            batch = data.get("installations", [])
            ids.update(int(item["id"]) for item in batch)
            if len(batch) < 100:
                return ids
            page += 1

    async def _token_request(self, params: dict[str, str]) -> UserTokens:
        response = await self._web.post(
            f"{GITHUB_WEB_URL}/login/oauth/access_token",
            data={"client_id": self._client_id, "client_secret": self._client_secret, **params},
            headers={"Accept": "application/json"},
        )
        data: dict[str, Any] = response.json() if response.content else {}
        # GitHub reports OAuth errors with HTTP 200 and an "error" field.
        if response.is_error or "error" in data or "access_token" not in data:
            raise OAuthError(
                str(data.get("error_description") or data.get("error") or response.status_code)
            )
        expires_in = data.get("expires_in")
        expires_at = (
            datetime.fromtimestamp(self._clock(), UTC) + timedelta(seconds=int(expires_in))
            if expires_in
            else None
        )
        return UserTokens(data["access_token"], data.get("refresh_token"), expires_at)

    def _client(self, access_token: str) -> GitHubClient:
        async def token() -> str:
            return access_token

        return GitHubClient(self._api, token)
