"""GitHub App authentication.

Two kinds of credentials:
1. App JWT: signed with the App's private key (RS256), valid <= 10 minutes.
   Proves "I am the ReviewPilot App". Only used to mint installation tokens.
2. Installation access token: valid 1 hour, scoped to one installation's repos
   and the App's permissions. Used for every repo API call.

Installation tokens are cached in memory so each review doesn't mint a new one
(one long-lived worker process, ADR 0016).
Reference: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
"""

import json
import time
from collections.abc import Callable
from typing import Protocol

import httpx
import jwt

from app.github.client import GitHubClient

JWT_CLOCK_DRIFT_SECONDS = 60
JWT_LIFETIME_SECONDS = 9 * 60  # GitHub's maximum is 10 minutes
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60


class TokenCache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...


class MemoryTokenCache:
    """Per-process cache. Losing it on restart costs one token request, nothing else."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._values: dict[str, tuple[str, float]] = {}  # key -> (value, expires at)

    async def get(self, key: str) -> str | None:
        value, expires_at = self._values.get(key, ("", 0.0))
        return value if value and expires_at > self._clock() else None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._values[key] = (value, self._clock() + ttl_seconds)

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)


class GitHubAppAuth:
    def __init__(
        self,
        *,
        issuer: str,
        private_key_pem: str,
        http: httpx.AsyncClient,
        cache: TokenCache,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._issuer = issuer  # the App's Client ID ("Iv23li...") or numeric App ID
        self._private_key_pem = private_key_pem
        self._http = http
        self._cache = cache
        self._clock = clock

    def create_jwt(self) -> str:
        now = int(self._clock())
        claims = {
            # Backdated so a server clock slightly ahead of GitHub's isn't rejected.
            "iat": now - JWT_CLOCK_DRIFT_SECONDS,
            "exp": now + JWT_LIFETIME_SECONDS,
            # A Client ID ("Iv23li...") is sent as a string; a numeric App ID must be
            # a JSON number, or GitHub answers "'iss' must be an Integer".
            "iss": int(self._issuer) if self._issuer.isdigit() else self._issuer,
        }
        # jwt.encode() refuses a non-string iss (RFC 7519 says StringOrURI), so we
        # sign the claims at the JWS layer, which is what jwt.encode() does inside.
        payload = json.dumps(claims, separators=(",", ":")).encode()
        return jwt.api_jws.encode(payload, self._private_key_pem, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        key = _cache_key(installation_id)
        cached = await self._cache.get(key)
        if cached:
            return cached

        async def app_jwt() -> str:
            return self.create_jwt()

        token = await GitHubClient(self._http, app_jwt).create_installation_token(installation_id)
        # Expire our cache entry 5 minutes before GitHub expires the token, so we
        # never hand out a token that dies in the middle of a review.
        ttl = int(token.expires_at.timestamp() - self._clock()) - TOKEN_REFRESH_MARGIN_SECONDS
        if ttl > 0:
            await self._cache.set(key, token.token, ttl)
        return token.token

    async def invalidate_installation_token(self, installation_id: int) -> None:
        await self._cache.delete(_cache_key(installation_id))

    def installation_client(self, installation_id: int) -> GitHubClient:
        async def get_token() -> str:
            return await self.get_installation_token(installation_id)

        async def on_unauthorized() -> None:
            await self.invalidate_installation_token(installation_id)

        return GitHubClient(self._http, get_token, on_unauthorized=on_unauthorized)


def _cache_key(installation_id: int) -> str:
    return f"gh:inst_token:{installation_id}"
