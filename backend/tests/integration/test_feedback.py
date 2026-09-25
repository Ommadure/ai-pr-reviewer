"""Reaction polling and usefulness metrics."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.github.app_auth import GitHubAppAuth
from app.models import ReviewCommentRecord
from app.services.analytics import feedback_metrics
from app.services.feedback import poll_feedback
from tests.integration.review_fakes import REPO, FakeGitHub, Sessions, all_rows

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _reaction(content: str, login: str, kind: str = "User") -> dict[str, Any]:
    return {"content": content, "user": {"login": login, "type": kind}}


async def _add_comments(sessions: Sessions, run_id: int, specs: list[dict[str, Any]]) -> None:
    async with sessions() as session:
        for i, spec in enumerate(specs):
            session.add(
                ReviewCommentRecord(
                    review_run_id=run_id,
                    pull_request_id=1,
                    path="app/users.py",
                    line=10 + i,
                    severity=spec.get("severity", "high"),
                    category=spec.get("category", "bug"),
                    title=f"comment {i}",
                    fingerprint=f"fp{i}",
                    posted=spec.get("posted", True),
                    github_comment_id=spec.get("github_id"),
                    status=spec.get("status", "open"),
                    thumbs_up=spec.get("up", 0),
                    thumbs_down=spec.get("down", 0),
                )
            )
        await session.commit()


async def test_polling_counts_human_reactions(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    await _add_comments(sessionmaker, run_id, [{"github_id": 101}, {"github_id": 102}])
    github.router.get(f"{REPO}/pulls/comments/101/reactions").respond(
        200,
        json=[
            _reaction("+1", "alice"),
            _reaction("+1", "some-bot[bot]", "Bot"),  # bots don't count
            _reaction("-1", "bob"),
            _reaction("heart", "carol"),
        ],
    )
    github.router.get(f"{REPO}/pulls/comments/102/reactions").respond(404, json={"message": "x"})

    result = await poll_feedback(sessionmaker, github_auth, now=NOW)

    assert (result.checked, result.deleted, result.stopped_early) == (1, 1, False)
    first, second = await all_rows(sessionmaker, ReviewCommentRecord)
    assert (first.thumbs_up, first.thumbs_down, first.feedback_checked_at) == (1, 1, NOW)
    assert second.status == "outdated"  # deleted on GitHub

    # Checked a minute ago: not due again yet.
    again = await poll_feedback(sessionmaker, github_auth, now=NOW + timedelta(minutes=1))
    assert again.checked == 0


async def test_polling_stops_politely_on_rate_limit(
    sessionmaker: Sessions, github_auth: GitHubAppAuth, github: FakeGitHub, run_id: int
) -> None:
    await _add_comments(sessionmaker, run_id, [{"github_id": 201}])
    github.router.get(f"{REPO}/pulls/comments/201/reactions").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"},
        )
    )
    result = await poll_feedback(sessionmaker, github_auth, now=NOW)
    assert result.stopped_early and result.checked == 0


async def test_metrics_definitions(sessionmaker: Sessions, run_id: int) -> None:
    await _add_comments(
        sessionmaker,
        run_id,
        [
            {"up": 2},  # helpful
            {"status": "addressed"},  # helpful: the author fixed it
            {"down": 1},  # negative
            {"up": 1, "down": 1, "category": "security", "severity": "critical"},  # both
            {},  # no signal
            {"posted": False, "up": 5},  # never posted: not counted at all
        ],
    )
    async with sessionmaker() as session:
        metrics = await feedback_metrics(session)

    overall = metrics.overall
    assert (overall.posted, overall.helpful, overall.negative) == (5, 3, 2)
    assert overall.helpful_rate == pytest.approx(0.6)
    assert overall.negative_rate == pytest.approx(0.4)
    assert metrics.by_category["security"].helpful_rate == 1.0
    assert metrics.by_severity["high"].posted == 4

    async with sessionmaker() as session:
        other_repo = await feedback_metrics(session, repository_ids=[999])
    assert other_repo.overall.posted == 0 and other_repo.overall.helpful_rate is None
