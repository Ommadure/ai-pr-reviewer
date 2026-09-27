"""Collect 👍/👎 reactions on posted review comments (a worker timer: every 30 minutes).

GitHub sends no webhook for reactions, so we poll: comments on open PRs, or PRs
closed within the last 7 days, that weren't checked in the last 30 minutes.
Reactions by bots don't count. On a rate limit we stop and let the next run
continue, so polling can never starve the reviews of API budget.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github.app_auth import GitHubAppAuth
from app.github.client import GitHubError, GitHubRateLimited
from app.models import Installation, PullRequest, Repository, ReviewCommentRecord

log = structlog.get_logger()

RECHECK_AFTER = timedelta(minutes=30)
CLOSED_PR_GRACE = timedelta(days=7)
BATCH_SIZE = 200


@dataclass
class PollResult:
    checked: int = 0
    changed: int = 0
    deleted: int = 0
    stopped_early: bool = False


async def poll_feedback(
    sessionmaker: async_sessionmaker[AsyncSession], github_auth: GitHubAppAuth, *, now: datetime
) -> PollResult:
    result = PollResult()
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    ReviewCommentRecord, Repository.full_name, Installation.github_installation_id
                )
                .join(PullRequest, PullRequest.id == ReviewCommentRecord.pull_request_id)
                .join(Repository, Repository.id == PullRequest.repository_id)
                .join(Installation, Installation.id == Repository.installation_id)
                .where(
                    ReviewCommentRecord.posted.is_(True),
                    ReviewCommentRecord.github_comment_id.is_not(None),
                    or_(
                        ReviewCommentRecord.feedback_checked_at.is_(None),
                        ReviewCommentRecord.feedback_checked_at < now - RECHECK_AFTER,
                    ),
                    or_(
                        PullRequest.state == "open",
                        PullRequest.updated_at > now - CLOSED_PR_GRACE,
                    ),
                    Installation.deleted_at.is_(None),
                )
                .order_by(ReviewCommentRecord.feedback_checked_at.asc().nulls_first())
                .limit(BATCH_SIZE)
            )
        ).all()

        for comment, full_name, installation_id in rows:
            owner, name = full_name.split("/", 1)
            github = github_auth.installation_client(installation_id)
            try:
                reactions = await github.list_review_comment_reactions(
                    owner, name, int(comment.github_comment_id)
                )
            except GitHubRateLimited:
                result.stopped_early = True
                break
            except GitHubError as exc:
                if exc.status_code != 404:
                    raise
                # The comment was deleted on GitHub: stop tracking it.
                comment.status, comment.feedback_checked_at = "outdated", now
                result.deleted += 1
                continue
            humans = [r for r in reactions if r.user_type != "Bot"]
            up = sum(1 for r in humans if r.content == "+1")
            down = sum(1 for r in humans if r.content == "-1")
            if (up, down) != (comment.thumbs_up, comment.thumbs_down):
                result.changed += 1
            comment.thumbs_up, comment.thumbs_down = up, down
            comment.feedback_checked_at = now
            comment.updated_at = func.now()
            result.checked += 1
        await session.commit()

    log.info("feedback.polled", **result.__dict__)
    return result
