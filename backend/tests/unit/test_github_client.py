import json

import httpx
import pytest
import respx

from app.github.client import (
    GITHUB_API_URL,
    GitHubClient,
    GitHubError,
    GitHubRateLimited,
    ReviewCommentInput,
)

NOW = 1_800_000_000.0


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
def client(sleep: FakeSleep) -> GitHubClient:
    async def token() -> str:
        return "ghs_test"

    return GitHubClient(
        httpx.AsyncClient(base_url=GITHUB_API_URL), token, sleep=sleep, clock=lambda: NOW
    )


@respx.mock(base_url=GITHUB_API_URL)
async def test_sends_versioned_headers_and_token(
    respx_mock: respx.MockRouter, client: GitHubClient
) -> None:
    route = respx_mock.get("/rate_limit").mock(return_value=httpx.Response(200, json={}))
    await client.request("GET", "/rate_limit")
    assert route.calls[0].request.headers["Authorization"] == "Bearer ghs_test"


@respx.mock(base_url=GITHUB_API_URL)
async def test_paginate_follows_link_header(
    respx_mock: respx.MockRouter, client: GitHubClient
) -> None:
    next_url = f"{GITHUB_API_URL}/repositories/1/pulls/1/files?per_page=100&page=2"
    route = respx_mock.get(path__regex=r"/(repos/o/r|repositories/1)/pulls/1/files").mock(
        side_effect=[
            httpx.Response(
                200, json=[{"n": 1}, {"n": 2}], headers={"Link": f'<{next_url}>; rel="next"'}
            ),
            httpx.Response(200, json=[{"n": 3}]),
        ]
    )

    items = await client.paginate("/repos/o/r/pulls/1/files")

    assert items == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert route.calls[0].request.url.params["per_page"] == "100"
    assert str(route.calls[1].request.url) == next_url


@respx.mock(base_url=GITHUB_API_URL)
async def test_retries_server_errors_with_backoff(
    respx_mock: respx.MockRouter, client: GitHubClient, sleep: FakeSleep
) -> None:
    respx_mock.get("/x").mock(side_effect=[httpx.Response(502), httpx.Response(200)])
    response = await client.request("GET", "/x")
    assert response.status_code == 200
    assert len(sleep.calls) == 1


@respx.mock(base_url=GITHUB_API_URL)
async def test_gives_up_after_max_retries(
    respx_mock: respx.MockRouter, client: GitHubClient
) -> None:
    route = respx_mock.get("/x").mock(return_value=httpx.Response(503))
    with pytest.raises(GitHubError) as exc_info:
        await client.request("GET", "/x")
    assert exc_info.value.status_code == 503
    assert route.call_count == 4  # first try + 3 retries


@respx.mock(base_url=GITHUB_API_URL)
async def test_retries_connection_errors(
    respx_mock: respx.MockRouter, client: GitHubClient
) -> None:
    respx_mock.get("/x").mock(side_effect=[httpx.ConnectError("boom"), httpx.Response(200)])
    assert (await client.request("GET", "/x")).status_code == 200


@respx.mock(base_url=GITHUB_API_URL)
async def test_primary_rate_limit_raises_with_time_until_reset(
    respx_mock: respx.MockRouter, client: GitHubClient, sleep: FakeSleep
) -> None:
    respx_mock.get("/x").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 600)},
        )
    )
    with pytest.raises(GitHubRateLimited) as exc_info:
        await client.request("GET", "/x")
    assert exc_info.value.retry_after == 600
    assert sleep.calls == []  # too long to wait inline: the job retries later


@respx.mock(base_url=GITHUB_API_URL)
async def test_short_secondary_rate_limit_waits_then_retries(
    respx_mock: respx.MockRouter, client: GitHubClient, sleep: FakeSleep
) -> None:
    respx_mock.post("/x").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "5"}, json={"message": "slow down"}),
            httpx.Response(201),
        ]
    )
    assert (await client.request("POST", "/x")).status_code == 201
    assert sleep.calls == [5.0]


@respx.mock(base_url=GITHUB_API_URL)
async def test_long_secondary_rate_limit_raises(
    respx_mock: respx.MockRouter, client: GitHubClient
) -> None:
    respx_mock.post("/x").mock(
        return_value=httpx.Response(403, headers={"retry-after": "120"}, json={"message": "x"})
    )
    with pytest.raises(GitHubRateLimited) as exc_info:
        await client.request("POST", "/x")
    assert exc_info.value.retry_after == 120


@respx.mock(base_url=GITHUB_API_URL)
async def test_permission_403_is_a_plain_error(
    respx_mock: respx.MockRouter, client: GitHubClient, sleep: FakeSleep
) -> None:
    respx_mock.get("/x").mock(
        return_value=httpx.Response(
            403,
            json={"message": "Resource not accessible by integration"},
            headers={"x-ratelimit-remaining": "4999", "x-github-request-id": "ABCD:1234"},
        )
    )
    with pytest.raises(GitHubError) as exc_info:
        await client.request("GET", "/x")
    assert not isinstance(exc_info.value, GitHubRateLimited)
    assert exc_info.value.request_id == "ABCD:1234"
    assert "not accessible" in exc_info.value.message
    assert sleep.calls == []


@respx.mock(base_url=GITHUB_API_URL)
async def test_create_review_always_uses_comment_event(
    respx_mock: respx.MockRouter, client: GitHubClient
) -> None:
    route = respx_mock.post("/repos/o/r/pulls/7/reviews").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )
    review_id = await client.create_review(
        "o",
        "r",
        7,
        commit_id="a" * 40,
        body="summary",
        comments=[ReviewCommentInput(path="app.py", line=3, body="hi")],
    )
    assert review_id == 99
    sent = json.loads(route.calls[0].request.content)
    assert sent == {
        "commit_id": "a" * 40,
        "body": "summary",
        "event": "COMMENT",
        # Unset optional fields (start_line, start_side) are left out, not sent as null.
        "comments": [{"path": "app.py", "line": 3, "side": "RIGHT", "body": "hi"}],
    }
