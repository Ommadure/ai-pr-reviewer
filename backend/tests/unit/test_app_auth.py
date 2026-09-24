import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github.app_auth import GitHubAppAuth
from app.github.client import GITHUB_API_URL
from tests.helpers import InMemoryTokenCache

NOW = 1_800_000_000.0
INSTALLATION_ID = 4001
TOKEN_URL = f"/app/installations/{INSTALLATION_ID}/access_tokens"


def _token_response(token: str, expires_in: timedelta) -> httpx.Response:
    expires_at = datetime.fromtimestamp(NOW, UTC) + expires_in
    return httpx.Response(
        201, json={"token": token, "expires_at": expires_at.isoformat().replace("+00:00", "Z")}
    )


@pytest.fixture
def cache() -> InMemoryTokenCache:
    return InMemoryTokenCache()


@pytest.fixture
async def auth(private_key_pem: str, cache: InMemoryTokenCache) -> GitHubAppAuth:
    return GitHubAppAuth(
        issuer="Iv1.test-client-id",
        private_key_pem=private_key_pem,
        http=httpx.AsyncClient(base_url=GITHUB_API_URL),
        cache=cache,
        clock=lambda: NOW,
    )


def test_app_jwt_claims(auth: GitHubAppAuth, rsa_private_key: rsa.RSAPrivateKey) -> None:
    token = auth.create_jwt()
    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    claims = jwt.decode(
        token,
        rsa_private_key.public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {"iss": "Iv1.test-client-id", "iat": NOW - 60, "exp": NOW + 540}


def test_numeric_app_id_issuer_is_a_json_number(
    private_key_pem: str, rsa_private_key: rsa.RSAPrivateKey
) -> None:
    # GitHub rejects '"iss": "123456"' with "'Issuer' claim ('iss') must be an Integer".
    auth = GitHubAppAuth(
        issuer="123456",
        private_key_pem=private_key_pem,
        http=httpx.AsyncClient(),
        cache=InMemoryTokenCache(),
        clock=lambda: NOW,
    )
    payload = jwt.api_jws.decode(
        auth.create_jwt(), rsa_private_key.public_key(), algorithms=["RS256"]
    )
    assert json.loads(payload) == {"iss": 123456, "iat": NOW - 60, "exp": NOW + 540}


@respx.mock(base_url=GITHUB_API_URL)
async def test_installation_token_is_fetched_once_then_cached(
    respx_mock: respx.MockRouter, auth: GitHubAppAuth, cache: InMemoryTokenCache
) -> None:
    route = respx_mock.post(TOKEN_URL).mock(
        return_value=_token_response("ghs_first", timedelta(hours=1))
    )

    assert await auth.get_installation_token(INSTALLATION_ID) == "ghs_first"
    assert await auth.get_installation_token(INSTALLATION_ID) == "ghs_first"

    assert route.call_count == 1
    # Authenticated as the App (JWT), not with an installation token.
    assert route.calls[0].request.headers["Authorization"].startswith("Bearer ey")
    # Cached until 5 minutes before GitHub's expiry.
    assert cache.ttls[f"gh:inst_token:{INSTALLATION_ID}"] == 3600 - 300


@respx.mock(base_url=GITHUB_API_URL)
async def test_nearly_expired_token_is_not_cached(
    respx_mock: respx.MockRouter, auth: GitHubAppAuth, cache: InMemoryTokenCache
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=_token_response("ghs_x", timedelta(minutes=4)))
    await auth.get_installation_token(INSTALLATION_ID)
    assert cache.values == {}


@respx.mock(base_url=GITHUB_API_URL)
async def test_401_invalidates_cached_token_and_retries_once(
    respx_mock: respx.MockRouter, auth: GitHubAppAuth
) -> None:
    token_route = respx_mock.post(TOKEN_URL).mock(
        side_effect=[
            _token_response("ghs_revoked", timedelta(hours=1)),
            _token_response("ghs_fresh", timedelta(hours=1)),
        ]
    )
    api_route = respx_mock.get("/repos/octocat/playground").mock(
        side_effect=[httpx.Response(401, json={"message": "Bad credentials"}), httpx.Response(200)]
    )

    client = auth.installation_client(INSTALLATION_ID)
    response = await client.request("GET", "/repos/octocat/playground")

    assert response.status_code == 200
    assert token_route.call_count == 2
    used_tokens = [call.request.headers["Authorization"] for call in api_route.calls]
    assert used_tokens == ["Bearer ghs_revoked", "Bearer ghs_fresh"]


@respx.mock(base_url=GITHUB_API_URL)
async def test_persistent_401_is_not_retried_forever(
    respx_mock: respx.MockRouter, auth: GitHubAppAuth
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=_token_response("t", timedelta(hours=1)))
    api_route = respx_mock.get("/repos/o/r").mock(
        return_value=httpx.Response(401, content=json.dumps({"message": "Bad credentials"}))
    )
    with pytest.raises(Exception, match="401"):
        await auth.installation_client(INSTALLATION_ID).request("GET", "/repos/o/r")
    assert api_route.call_count == 2
