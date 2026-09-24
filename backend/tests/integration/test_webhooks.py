"""Webhook endpoint → router → real Postgres. Celery is replaced by a recording dispatcher."""

from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github.signatures import compute_signature
from app.models import Installation, PullRequest, Repository, WebhookDelivery
from tests.helpers import (
    BOT_LOGIN,
    WEBHOOK_SECRET,
    RecordingDispatcher,
    load_webhook,
    send_webhook,
)

Sessions = async_sessionmaker[AsyncSession]


async def _delivery(sessions: Sessions, delivery_id: str) -> WebhookDelivery | None:
    async with sessions() as session:
        return await session.get(WebhookDelivery, delivery_id)


async def _all(sessions: Sessions, model: Any) -> list[Any]:
    async with sessions() as session:
        return list((await session.scalars(select(model).order_by(model.id))).all())


# ---- authenticity ----


@pytest.mark.parametrize("signature", ["", "sha256=" + "0" * 64])
async def test_unsigned_or_badly_signed_requests_are_rejected(
    api: httpx.AsyncClient, sessionmaker: Sessions, signature: str
) -> None:
    response = await send_webhook(
        api,
        "pull_request",
        load_webhook("pull_request_opened"),
        delivery_id="d-1",
        signature=signature,
    )
    assert response.status_code == 401
    assert await _delivery(sessionmaker, "d-1") is None  # nothing recorded


async def test_ping(api: httpx.AsyncClient) -> None:
    response = await send_webhook(api, "ping", {"zen": "Keep it logically awesome."})
    assert response.status_code == 200


async def test_rejects_non_json_body(api: httpx.AsyncClient) -> None:
    body = b"not json"
    response = await api.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "d-json",
            "X-Hub-Signature-256": compute_signature(WEBHOOK_SECRET, body),
        },
    )
    assert response.status_code == 400


# ---- dedupe ----


async def test_duplicate_delivery_is_processed_once(
    api: httpx.AsyncClient, sessionmaker: Sessions, dispatcher: RecordingDispatcher
) -> None:
    payload = load_webhook("pull_request_opened")
    first = await send_webhook(api, "pull_request", payload, delivery_id="same-id")
    second = await send_webhook(api, "pull_request", payload, delivery_id="same-id")

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert len(dispatcher.jobs) == 1


async def test_failed_delivery_can_be_redelivered(
    api: httpx.AsyncClient, sessionmaker: Sessions, dispatcher: RecordingDispatcher
) -> None:
    broken = load_webhook("pull_request_opened")
    del broken["pull_request"]["head"]  # a field we rely on is missing
    response = await send_webhook(api, "pull_request", broken, delivery_id="retry-me")
    assert response.status_code == 500
    delivery = await _delivery(sessionmaker, "retry-me")
    assert delivery is not None and delivery.status == "failed"
    assert delivery.error is not None and "ValidationError" in delivery.error

    # "Redeliver" in GitHub's UI resends the same delivery id.
    good = load_webhook("pull_request_opened")
    response = await send_webhook(api, "pull_request", good, delivery_id="retry-me")
    assert response.status_code == 202
    delivery = await _delivery(sessionmaker, "retry-me")
    assert delivery is not None and delivery.status == "queued" and delivery.error is None
    assert len(dispatcher.jobs) == 1


# ---- installation sync ----


async def test_installation_created_syncs_installation_and_repositories(
    api: httpx.AsyncClient, sessionmaker: Sessions
) -> None:
    response = await send_webhook(api, "installation", load_webhook("installation_created"))
    assert response.status_code == 202

    [installation] = await _all(sessionmaker, Installation)
    assert (installation.github_installation_id, installation.account_login) == (4001, "octocat")
    repos = await _all(sessionmaker, Repository)
    assert [(r.full_name, r.private) for r in repos] == [
        ("octocat/playground", False),
        ("octocat/secret-sauce", True),
    ]


async def test_installation_repositories_removed_soft_deletes(
    api: httpx.AsyncClient, sessionmaker: Sessions
) -> None:
    await send_webhook(api, "installation", load_webhook("installation_created"))
    await send_webhook(
        api, "installation_repositories", load_webhook("installation_repositories_removed")
    )
    repos = {r.full_name: r for r in await _all(sessionmaker, Repository)}
    assert repos["octocat/playground"].removed_at is None
    assert repos["octocat/secret-sauce"].removed_at is not None


@pytest.mark.parametrize(
    ("action", "suspended", "deleted"),
    [("suspend", True, False), ("deleted", False, True)],
)
async def test_installation_lifecycle(
    api: httpx.AsyncClient, sessionmaker: Sessions, action: str, suspended: bool, deleted: bool
) -> None:
    await send_webhook(api, "installation", load_webhook("installation_created"))
    payload = load_webhook("installation_created") | {"action": action}
    await send_webhook(api, "installation", payload)

    [installation] = await _all(sessionmaker, Installation)
    assert (installation.suspended_at is not None) is suspended
    assert (installation.deleted_at is not None) is deleted


# ---- pull requests ----


async def test_pull_request_opened_stores_pr_and_enqueues_review(
    api: httpx.AsyncClient, sessionmaker: Sessions, dispatcher: RecordingDispatcher
) -> None:
    # The App was installed before we were listening: no installation event seen.
    response = await send_webhook(
        api, "pull_request", load_webhook("pull_request_opened"), delivery_id="pr-1"
    )

    assert response.status_code == 202
    assert response.json() == {"status": "queued"}
    [pr] = await _all(sessionmaker, PullRequest)
    assert (pr.number, pr.state, pr.head_sha) == (1, "open", "a" * 40)
    [repo] = await _all(sessionmaker, Repository)
    assert repo.default_branch == "main"
    assert [(j.pull_request_id, j.head_sha, j.trigger) for j in dispatcher.jobs] == [
        (pr.id, "a" * 40, "opened")
    ]
    delivery = await _delivery(sessionmaker, "pr-1")
    assert delivery is not None and delivery.status == "queued"


@pytest.mark.parametrize(
    "sender",
    [
        {"login": BOT_LOGIN, "type": "Bot"},  # ourselves: would loop forever
        {"login": "dependabot[bot]", "type": "Bot"},
    ],
)
async def test_events_from_bots_are_ignored(
    api: httpx.AsyncClient,
    sessionmaker: Sessions,
    dispatcher: RecordingDispatcher,
    sender: dict[str, str],
) -> None:
    payload = load_webhook("pull_request_opened") | {"sender": sender}
    response = await send_webhook(api, "pull_request", payload, delivery_id="bot")

    assert response.json() == {"status": "ignored"}
    delivery = await _delivery(sessionmaker, "bot")
    assert delivery is not None and delivery.ignore_reason == "bot_sender"
    assert dispatcher.jobs == []
    assert await _all(sessionmaker, PullRequest) == []


async def test_draft_pull_request_is_stored_but_not_reviewed(
    api: httpx.AsyncClient, sessionmaker: Sessions, dispatcher: RecordingDispatcher
) -> None:
    payload = load_webhook("pull_request_opened")
    payload["pull_request"]["draft"] = True
    await send_webhook(api, "pull_request", payload, delivery_id="draft")

    delivery = await _delivery(sessionmaker, "draft")
    assert delivery is not None and delivery.ignore_reason == "draft"
    assert len(await _all(sessionmaker, PullRequest)) == 1
    assert dispatcher.jobs == []


async def test_disabled_repository_is_not_reviewed(
    api: httpx.AsyncClient, sessionmaker: Sessions, dispatcher: RecordingDispatcher
) -> None:
    await send_webhook(api, "installation", load_webhook("installation_created"))
    async with sessionmaker() as session:
        await session.execute(update(Repository).values(enabled=False))
        await session.commit()

    await send_webhook(api, "pull_request", load_webhook("pull_request_opened"), delivery_id="d")

    delivery = await _delivery(sessionmaker, "d")
    assert delivery is not None and delivery.ignore_reason == "repo_disabled"
    assert dispatcher.jobs == []


async def test_new_commits_update_head_sha(
    api: httpx.AsyncClient, sessionmaker: Sessions, dispatcher: RecordingDispatcher
) -> None:
    await send_webhook(api, "pull_request", load_webhook("pull_request_opened"))
    pushed = load_webhook("pull_request_opened") | {"action": "synchronize"}
    pushed["pull_request"]["head"]["sha"] = "c" * 40
    await send_webhook(api, "pull_request", pushed)

    [pr] = await _all(sessionmaker, PullRequest)
    assert pr.head_sha == "c" * 40
    assert len(dispatcher.jobs) == 1  # only the "opened" review; incremental is Phase 4


async def test_merged_pull_request_state(api: httpx.AsyncClient, sessionmaker: Sessions) -> None:
    closed = load_webhook("pull_request_opened") | {"action": "closed"}
    closed["pull_request"] |= {"state": "closed", "merged": True}
    await send_webhook(api, "pull_request", closed)
    [pr] = await _all(sessionmaker, PullRequest)
    assert pr.state == "merged"


# ---- other events ----


async def test_slash_command_is_recognised_but_not_yet_handled(
    api: httpx.AsyncClient, sessionmaker: Sessions
) -> None:
    await send_webhook(api, "issue_comment", load_webhook("issue_comment_created"), delivery_id="c")
    delivery = await _delivery(sessionmaker, "c")
    assert delivery is not None and delivery.ignore_reason == "commands_not_implemented"


async def test_unknown_events_are_recorded_as_ignored(
    api: httpx.AsyncClient, sessionmaker: Sessions
) -> None:
    response = await send_webhook(api, "star", {"action": "created"}, delivery_id="star")
    assert response.status_code == 202
    delivery = await _delivery(sessionmaker, "star")
    assert delivery is not None
    assert (delivery.status, delivery.ignore_reason) == ("ignored", "unhandled_event")
