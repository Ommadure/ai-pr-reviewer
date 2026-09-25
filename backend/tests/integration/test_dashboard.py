"""Dashboard API: GitHub login, sessions, and tenant isolation on every endpoint.

Two tenants: installation A (octocat) and installation B (other-org). GitHub's
/user/installations (mocked) says user A can access only A, and user B only B.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import select

from app.api.deps import get_account_services, get_rate_limiter
from app.core.config import Settings
from app.core.security import SESSION_COOKIE, STATE_COOKIE, SessionTokens, TokenCipher
from app.github.client import GITHUB_API_URL
from app.github.oauth import GITHUB_WEB_URL, GitHubOAuth
from app.models import (
    Installation,
    LLMCall,
    PullRequest,
    RepoConfigRecord,
    Repository,
    ReviewCommentRecord,
    ReviewRun,
    User,
)
from app.services.accounts import AccountServices
from tests.helpers import TEST_FERNET_KEY, RecordingDispatcher
from tests.integration.review_fakes import Sessions

NOW = datetime.now(UTC)


class MemoryLimiter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key] <= limit


@pytest.fixture
def github_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        for token, installations in (("token-a", [1001]), ("token-b", [2002])):
            router.get(
                f"{GITHUB_API_URL}/user/installations",
                headers={"Authorization": f"Bearer {token}"},
                name=f"installations-{token}",
            ).respond(
                200, json={"total_count": 1, "installations": [{"id": i} for i in installations]}
            )
        yield router


@pytest.fixture
async def world(sessionmaker: Sessions) -> dict[str, Any]:
    """Two tenants with a repo, a PR, a finished run, comments and LLM calls each."""
    cipher = TokenCipher(TEST_FERNET_KEY)
    ids: dict[str, Any] = {}
    async with sessionmaker() as session:
        for key, gh_id, login, token in (
            ("a", 1001, "octocat", "token-a"),
            ("b", 2002, "other-org", "token-b"),
        ):
            inst = Installation(
                github_installation_id=gh_id, account_login=login, account_type="User"
            )
            session.add(inst)
            await session.flush()
            repo = Repository(
                installation_id=inst.id,
                github_repo_id=gh_id * 10,
                full_name=f"{login}/app",
                private=False,
                default_branch="main",
            )
            session.add(repo)
            await session.flush()
            pr = PullRequest(
                repository_id=repo.id,
                number=7,
                github_pr_id=gh_id * 100,
                title=f"{login} change",
                author_login=login,
                state="open",
                draft=False,
                base_ref="main",
                base_sha="b" * 40,
                head_sha="a" * 40,
            )
            session.add(pr)
            await session.flush()
            run = ReviewRun(
                pull_request_id=pr.id,
                trigger="opened",
                mode="full",
                base_sha="b" * 40,
                head_sha="a" * 40,
                status="completed",
                files_total=2,
                files_reviewed=1,
                files_skipped=[{"path": "yarn.lock", "reason": "ignored_path"}],
                comments_generated=2,
                comments_posted=1,
                input_tokens=1000,
                output_tokens=300,
                cost_usd=Decimal("0.002"),
                latency_ms=20_000,
                github_review_id=555,
                prompt_version="v1",
                model="m",
                summary={"overview": "o", "risk_level": "low", "key_changes": [], "notes": []},
                started_at=NOW,
                finished_at=NOW,
            )
            session.add(run)
            await session.flush()
            session.add_all(
                [
                    ReviewCommentRecord(
                        review_run_id=run.id,
                        pull_request_id=pr.id,
                        path="app.py",
                        line=3,
                        severity="high",
                        category="security",
                        title="SQLi",
                        body="b",
                        posted=True,
                        github_comment_id=9001,
                        thumbs_up=1,
                        fingerprint=f"fp-{key}",
                    ),
                    ReviewCommentRecord(
                        review_run_id=run.id,
                        pull_request_id=pr.id,
                        path="app.py",
                        line=99,
                        severity="low",
                        category="bug",
                        title="ghost",
                        posted=False,
                        drop_reason="invalid_line",
                    ),
                    LLMCall(
                        review_run_id=run.id,
                        purpose="file_review",
                        model="m",
                        input_tokens=900,
                        output_tokens=200,
                        cost_usd=Decimal("0.0015"),
                        latency_ms=12_000,
                        status="success",
                    ),
                    RepoConfigRecord(
                        repository_id=repo.id,
                        commit_sha="d" * 40,
                        raw_yaml="max_comments: 5\n",
                        parsed={"max_comments": 5},
                        is_valid=True,
                    ),
                ]
            )
            user = User(
                github_user_id=gh_id + 1,
                login=f"{login}-user",
                avatar_url=None,
                access_token_enc=cipher.encrypt(token),
                refresh_token_enc=None,
                token_expires_at=NOW + timedelta(hours=6),
            )
            session.add(user)
            await session.flush()
            ids[key] = {
                "installation": inst.id,
                "repo": repo.id,
                "pr": pr.id,
                "run": run.id,
                "user": user.id,
            }
        await session.commit()
    return ids


@pytest.fixture
async def dash(
    app: FastAPI,
    api: httpx.AsyncClient,
    settings: Settings,
    github_mock: respx.MockRouter,
) -> AsyncIterator[httpx.AsyncClient]:
    """The API client, with GitHub OAuth/API calls going to the respx mock."""
    web = httpx.AsyncClient()
    gh_api = httpx.AsyncClient(base_url=GITHUB_API_URL)
    services = AccountServices(
        GitHubOAuth(
            client_id="Iv23liTest",
            client_secret="test-client-secret",
            redirect_uri=settings.oauth_redirect_uri,
            web_http=web,
            api_http=gh_api,
        ),
        TokenCipher(TEST_FERNET_KEY),
    )
    app.dependency_overrides[get_account_services] = lambda: services
    limiter = MemoryLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    yield api
    await web.aclose()
    await gh_api.aclose()


def login_as(client: httpx.AsyncClient, settings: Settings, user_id: int) -> None:
    tokens = SessionTokens(settings.session_secret.get_secret_value(), 3600)
    client.cookies.set(SESSION_COOKIE, tokens.issue(user_id))


# ---- login ----


async def test_full_oauth_login_flow(
    dash: httpx.AsyncClient,
    github_mock: respx.MockRouter,
    sessionmaker: Sessions,
    settings: Settings,
) -> None:
    start = await dash.get("/api/v1/auth/github/login")
    assert start.status_code == 302
    location = urlparse(start.headers["location"])
    assert (
        f"{location.scheme}://{location.netloc}{location.path}"
        == f"{GITHUB_WEB_URL}/login/oauth/authorize"
    )
    query = parse_qs(location.query)
    assert query["client_id"] == ["Iv23liTest"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/github/callback"]
    state = query["state"][0]
    state_cookie = start.cookies.get(STATE_COOKIE)
    assert state_cookie and "httponly" in start.headers["set-cookie"].lower()

    token_route = github_mock.post(f"{GITHUB_WEB_URL}/login/oauth/access_token").respond(
        200,
        json={
            "access_token": "ghu_new",
            "expires_in": 28800,
            "refresh_token": "ghr_new",
            "token_type": "bearer",
        },
    )
    github_mock.get(f"{GITHUB_API_URL}/user").respond(
        200, json={"id": 424242, "login": "newcomer", "avatar_url": "https://avatars.test/n"}
    )
    dash.cookies.set(STATE_COOKIE, state_cookie, path="/api/v1/auth")
    done = await dash.get("/api/v1/auth/github/callback", params={"code": "abc", "state": state})

    assert done.status_code == 302 and done.headers["location"] == "http://localhost:5173/"
    assert b"code=abc" in token_route.calls[0].request.content
    session_cookie = done.cookies.get(SESSION_COOKIE)
    assert session_cookie
    async with sessionmaker() as session:
        user = (await session.scalars(select(User).where(User.github_user_id == 424242))).one()
    # Tokens are encrypted at rest.
    assert "ghu_new" not in user.access_token_enc
    assert TokenCipher(TEST_FERNET_KEY).decrypt(user.access_token_enc) == "ghu_new"
    assert user.token_expires_at is not None

    dash.cookies.clear()
    dash.cookies.set(SESSION_COOKIE, session_cookie)
    me = await dash.get("/api/v1/me")
    assert me.json() == {"id": user.id, "login": "newcomer", "avatar_url": "https://avatars.test/n"}


@pytest.mark.parametrize(
    ("params", "cookie_state", "expected_error"),
    [
        ({"code": "abc", "state": "forged"}, "real", "state"),  # CSRF: state doesn't match
        ({"code": "abc", "state": "real"}, None, "state"),  # no state cookie at all
        ({"error": "access_denied", "state": "real"}, "real", "denied"),  # user clicked Cancel
    ],
)
async def test_callback_rejections(
    dash: httpx.AsyncClient,
    settings: Settings,
    params: dict[str, str],
    cookie_state: str | None,
    expected_error: str,
) -> None:
    from app.core.security import OAuthState

    real_state, cookie = OAuthState(settings.session_secret.get_secret_value()).issue()
    params = {k: (real_state if v == "real" else v) for k, v in params.items()}
    if cookie_state:
        dash.cookies.set(STATE_COOKIE, cookie, path="/api/v1/auth")
    response = await dash.get("/api/v1/auth/github/callback", params=params)
    assert response.headers["location"] == f"http://localhost:5173/login?error={expected_error}"
    assert SESSION_COOKIE not in response.cookies


async def test_session_is_required_and_verified(
    dash: httpx.AsyncClient, settings: Settings
) -> None:
    assert (await dash.get("/api/v1/me")).status_code == 401
    forged = SessionTokens("x" * 40, 3600).issue(1)  # signed with the wrong secret
    dash.cookies.set(SESSION_COOKIE, forged)
    assert (await dash.get("/api/v1/me")).status_code == 401
    expired = SessionTokens(settings.session_secret.get_secret_value(), 60).issue(
        1, now=(NOW - timedelta(hours=1)).timestamp()
    )
    dash.cookies.set(SESSION_COOKIE, expired)
    assert (await dash.get("/api/v1/installations")).status_code == 401


async def test_logout_clears_the_cookie(dash: httpx.AsyncClient) -> None:
    response = await dash.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert f'{SESSION_COOKIE}=""' in response.headers["set-cookie"]


async def test_expired_github_token_is_refreshed(
    dash: httpx.AsyncClient,
    github_mock: respx.MockRouter,
    sessionmaker: Sessions,
    settings: Settings,
    world: dict[str, Any],
) -> None:
    cipher = TokenCipher(TEST_FERNET_KEY)
    async with sessionmaker() as session:
        user = await session.get(User, world["a"]["user"])
        assert user is not None
        user.access_token_enc = cipher.encrypt("stale")
        user.refresh_token_enc = cipher.encrypt("ghr_old")
        user.token_expires_at = NOW - timedelta(minutes=1)
        await session.commit()
    refresh = github_mock.post(f"{GITHUB_WEB_URL}/login/oauth/access_token").respond(
        200, json={"access_token": "token-a", "expires_in": 28800, "refresh_token": "ghr_next"}
    )
    login_as(dash, settings, world["a"]["user"])

    response = await dash.get("/api/v1/installations")

    assert response.status_code == 200
    assert b"grant_type=refresh_token" in refresh.calls[0].request.content
    async with sessionmaker() as session:
        user = await session.get(User, world["a"]["user"])
        assert user is not None and cipher.decrypt(user.refresh_token_enc or "") == "ghr_next"


async def test_rejected_refresh_asks_to_sign_in_again(
    dash: httpx.AsyncClient,
    github_mock: respx.MockRouter,
    sessionmaker: Sessions,
    settings: Settings,
    world: dict[str, Any],
) -> None:
    async with sessionmaker() as session:
        user = await session.get(User, world["a"]["user"])
        assert user is not None
        user.refresh_token_enc = TokenCipher(TEST_FERNET_KEY).encrypt("revoked")
        user.token_expires_at = NOW - timedelta(minutes=1)
        await session.commit()
    github_mock.post(f"{GITHUB_WEB_URL}/login/oauth/access_token").respond(
        200,
        json={"error": "bad_refresh_token"},  # GitHub reports OAuth errors with HTTP 200
    )
    login_as(dash, settings, world["a"]["user"])
    assert (await dash.get("/api/v1/installations")).status_code == 401


# ---- tenancy ----


async def test_users_only_see_their_own_installations(
    dash: httpx.AsyncClient,
    settings: Settings,
    world: dict[str, Any],
    github_mock: respx.MockRouter,
) -> None:
    login_as(dash, settings, world["a"]["user"])
    body = (await dash.get("/api/v1/installations")).json()
    assert [i["account_login"] for i in body["installations"]] == ["octocat"]
    assert body["install_url"] == "https://github.com/apps/reviewpilot-test/installations/new"
    repos = (await dash.get("/api/v1/repositories")).json()
    assert [r["full_name"] for r in repos] == ["octocat/app"]

    # The GitHub answer is cached for 5 minutes: no second call.
    await dash.get("/api/v1/installations")
    assert github_mock["installations-token-a"].call_count == 1


def _foreign_requests(b: dict[str, int]) -> list[tuple[str, str, dict[str, Any] | None]]:
    return [
        ("GET", f"/api/v1/installations/{b['installation']}/repositories", None),
        ("PATCH", f"/api/v1/repositories/{b['repo']}", {"enabled": False}),
        ("GET", f"/api/v1/repositories/{b['repo']}/config", None),
        ("GET", f"/api/v1/repositories/{b['repo']}/pulls", None),
        ("GET", f"/api/v1/pulls/{b['pr']}", None),
        ("POST", f"/api/v1/pulls/{b['pr']}/rereview", None),
        ("GET", f"/api/v1/runs/{b['run']}", None),
        ("GET", f"/api/v1/analytics/overview?repository_id={b['repo']}", None),
    ]


async def test_every_endpoint_hides_other_tenants(
    dash: httpx.AsyncClient,
    settings: Settings,
    world: dict[str, Any],
    sessionmaker: Sessions,
    dispatcher: RecordingDispatcher,
) -> None:
    login_as(dash, settings, world["a"]["user"])
    for method, url, body in _foreign_requests(world["b"]):
        response = await dash.request(method, url, json=body)
        # 404, not 403: an outsider can't even learn the id exists.
        assert response.status_code == 404, f"{method} {url} -> {response.status_code}"
    async with sessionmaker() as session:
        repo_b = await session.get(Repository, world["b"]["repo"])
        assert repo_b is not None and repo_b.enabled is True  # the PATCH changed nothing
    assert dispatcher.jobs == []  # and no review was queued

    # Tenant B sees its own data through the very same endpoints.
    dash.cookies.clear()
    login_as(dash, settings, world["b"]["user"])
    for method, url, _body in _foreign_requests(world["b"]):
        if method == "GET":
            assert (await dash.get(url)).status_code == 200, url


async def test_analytics_only_counts_the_callers_data(
    dash: httpx.AsyncClient, settings: Settings, world: dict[str, Any]
) -> None:
    login_as(dash, settings, world["a"]["user"])
    body = (await dash.get("/api/v1/analytics/overview?range=7d")).json()
    assert (body["runs_total"], body["runs_completed"], body["comments_posted"]) == (1, 1, 1)
    assert body["cost_usd_total"] == pytest.approx(0.002)
    assert body["usd_to_inr"] == 83.0
    assert body["p95_latency_ms"] == pytest.approx(20_000)
    assert body["comments_by_category"] == {"security": 1}
    assert body["feedback"]["helpful_rate"] == 1.0
    assert body["top_files"] == [
        {"repository_full_name": "octocat/app", "path": "app.py", "count": 1}
    ]
    assert [d["count"] for d in body["reviews_per_day"]] == [1]


# ---- endpoints ----


async def test_run_detail(
    dash: httpx.AsyncClient, settings: Settings, world: dict[str, Any]
) -> None:
    login_as(dash, settings, world["a"]["user"])
    run = (await dash.get(f"/api/v1/runs/{world['a']['run']}")).json()
    assert (run["status"], run["repository_full_name"], run["pull_request_number"]) == (
        "completed",
        "octocat/app",
        7,
    )
    assert run["files_skipped"] == [{"path": "yarn.lock", "reason": "ignored_path"}]
    assert [c["purpose"] for c in run["llm_calls"]] == ["file_review"]
    posted, dropped = run["comments"]
    assert posted["github_url"] == "https://github.com/octocat/app/pull/7#discussion_r9001"
    assert (dropped["posted"], dropped["drop_reason"]) == (False, "invalid_line")
    assert run["github_review_url"].endswith("#pullrequestreview-555")


async def test_pulls_are_cursor_paginated(
    dash: httpx.AsyncClient, settings: Settings, world: dict[str, Any], sessionmaker: Sessions
) -> None:
    async with sessionmaker() as session:
        for number in (8, 9):
            session.add(
                PullRequest(
                    repository_id=world["a"]["repo"],
                    number=number,
                    github_pr_id=number,
                    title=f"PR {number}",
                    author_login="o",
                    state="open",
                    draft=False,
                    base_ref="main",
                    base_sha="b" * 40,
                    head_sha="c" * 40,
                )
            )
        await session.commit()
    login_as(dash, settings, world["a"]["user"])
    url = f"/api/v1/repositories/{world['a']['repo']}/pulls"

    first = (await dash.get(url, params={"limit": 2})).json()
    second = (await dash.get(url, params={"limit": 2, "cursor": first["next_cursor"]})).json()

    assert [p["number"] for p in first["items"]] == [9, 8]
    assert [p["number"] for p in second["items"]] == [7]
    assert second["next_cursor"] is None
    assert second["items"][0]["last_run"]["status"] == "completed"


async def test_rereview_queues_a_run_and_is_rate_limited(
    dash: httpx.AsyncClient,
    settings: Settings,
    world: dict[str, Any],
    dispatcher: RecordingDispatcher,
) -> None:
    login_as(dash, settings, world["a"]["user"])
    url = f"/api/v1/pulls/{world['a']['pr']}/rereview"
    statuses = [(await dash.post(url)).status_code for _ in range(6)]
    assert statuses == [202] * 5 + [429]
    assert len(dispatcher.jobs) == 5
    pr = (await dash.get(f"/api/v1/pulls/{world['a']['pr']}")).json()
    assert pr["runs"][0]["trigger"] == "manual" and pr["runs"][0]["status"] == "queued"


async def test_toggle_repository_and_read_config(
    dash: httpx.AsyncClient, settings: Settings, world: dict[str, Any]
) -> None:
    login_as(dash, settings, world["a"]["user"])
    repo_url = f"/api/v1/repositories/{world['a']['repo']}"
    updated = (await dash.patch(repo_url, json={"enabled": False})).json()
    assert (updated["enabled"], updated["config_status"]) == (False, "valid")
    config = (await dash.get(f"{repo_url}/config")).json()
    assert (config["has_file"], config["parsed"]) == (True, {"max_comments": 5})
