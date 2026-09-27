"""The review pipeline around the pure engine: every side effect lives here.

    run (queued) ─► checks: still open? head moved? ─► config (default branch)
       ─► check run "in progress" ─► PR files ─► run_review() ─► head moved? ─► post ONE review
       (422 → post comments one by one) ─► check run summary ─► persist

Guarantees (ADR 0010):
- one review at a time per PR (the job queue's lock_key, ADR 0016);
- a job that runs twice for the same run does nothing the second time (run status);
- never posts for a commit that is no longer the PR head (stale checks);
- never repeats a comment already posted on the PR (fingerprints + unique index);
- never blocks a merge: check runs end success / neutral / skipped only.
"""

import asyncio
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import httpx
import structlog
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import bind_context
from app.github.app_auth import GitHubAppAuth
from app.github.client import (
    CheckRunOutput,
    Conclusion,
    GitHubClient,
    GitHubError,
    GitHubRateLimited,
    PullRequestFile,
    PullRequestInfo,
)
from app.models import PullRequest, ReviewCommentRecord, ReviewRun
from app.repositories import review_runs
from app.review.diff_parser import parse_patch
from app.review.engine import run_review
from app.review.incremental import CommentStatus, detect_status_changes, restrict_to_pr_diff
from app.review.llm.base import LLMProvider
from app.review.models import (
    DroppedComment,
    FileDiff,
    FileStatus,
    PRContext,
    ReviewBudget,
    ReviewComment,
    ReviewResult,
    SkippedFile,
)
from app.review.pricing import PriceTable
from app.review.prompt_builder import DEFAULT_PROMPT_VERSION
from app.services import review_report
from app.services.config_loader import load_repo_config

log = structlog.get_logger()

FILE_STATUSES: set[str] = {
    "added",
    "removed",
    "modified",
    "renamed",
    "copied",
    "changed",
    "unchanged",
}
DROP_BUCKETS = {
    "comments_dropped_invalid_line": {"unknown_path", "invalid_line", "invalid_range"},
    "comments_dropped_duplicate": {"duplicate", "already_posted"},
    "comments_dropped_low_confidence": {"low_confidence", "below_min_severity", "out_of_focus"},
    "comments_dropped_over_limit": {"over_limit"},
}


# Past this, the review fails as "timeout" (the worker's hard limit is 300 s).
REVIEW_TIME_LIMIT_SECONDS = 240


class RetryableReviewError(Exception):
    """A transient failure (rate limit, network): the whole run should be retried."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class ReviewDeps:
    sessionmaker: async_sessionmaker[AsyncSession]
    github_auth: GitHubAppAuth
    llm: LLMProvider
    model: str
    summary_model: str | None = None
    budget: ReviewBudget = field(default_factory=ReviewBudget)
    prices: PriceTable = field(default_factory=PriceTable)
    prompt_version: str = DEFAULT_PROMPT_VERSION


# Replies for a review someone asked for with `/reviewpilot review` that the worker then
# skipped: the command already said "Starting", so silence would look like a hang.
COMMAND_SKIP_REPLIES = {
    "pr_closed": "⏭️ Skipped the review of `{sha}`: this PR was closed before it started.",
    "newer_commit": (
        "⏭️ Skipped the review of `{sha}`: a newer commit was pushed before it started. "
        "Run `/reviewpilot review` again to review the latest one."
    ),
    "draft": (
        "⏭️ Skipped the review of `{sha}`: this PR is a draft, and this repository doesn't "
        "review drafts (`review_drafts` in `.reviewpilot.yml`). Mark it ready for review, "
        "then run `/reviewpilot review` again."
    ),
    "disabled_in_config": (
        "⏭️ Skipped the review of `{sha}`: ReviewPilot is turned off for this repository "
        "(`enabled: false` in `.reviewpilot.yml` on the default branch)."
    ),
    "no_new_changes": "⏭️ Nothing new to review in `{sha}` since the last review.",
}


@dataclass(frozen=True)
class RunOutcome:
    status: str  # completed | skipped | superseded | failed | missing | <already-final status>
    reason: str | None = None
    posted: int = 0


@dataclass
class _Posting:
    review_id: int | None = None
    comment_ids: dict[str, int] = field(default_factory=dict)  # fingerprint -> GitHub id
    rejected: set[str] = field(default_factory=set)  # fingerprints GitHub refused


async def execute_review_run(
    deps: ReviewDeps, run_id: int, *, final_attempt: bool = True
) -> RunOutcome:
    async with deps.sessionmaker() as session:
        run = await review_runs.get_with_context(session, run_id)
        if run is None:
            return RunOutcome("missing")
        if run.status not in review_runs.ACTIVE_STATUSES:
            return RunOutcome(run.status, reason="already_finished")  # the job ran twice
        pr = run.pull_request
        if pr.state != "open":
            return await _finish(session, run, "skipped", "pr_closed")
        if pr.head_sha != run.head_sha:
            return await _finish(session, run, "superseded", "newer_commit")
        run.status, run.started_at = "running", datetime.now(UTC)
        run.prompt_version, run.model = deps.prompt_version, deps.model
        await session.commit()
        repository = pr.repository
        installation_id = repository.installation.github_installation_id
        pr_id, pr_number, head_sha = pr.id, pr.number, run.head_sha
        default_branch = repository.default_branch or pr.base_ref
        existing_check_run = run.check_run_id
        requested_mode, last_reviewed = run.mode, pr.last_reviewed_sha
        requested_by_command = run.trigger == "command"

    owner, name = repository.owner_and_name
    github = deps.github_auth.installation_client(installation_id)
    # Everything below, however deep (GitHub retries, LLM backoff), logs with this context.
    bind_context(run_id=run_id, repo=repository.full_name, pr=pr_number, head_sha=head_sha)
    logger = log
    started = time.monotonic()
    check_run_id = existing_check_run
    result: ReviewResult | None = None

    async def skip(status: str, reason: str) -> RunOutcome:
        outcome = await _finish_by_id(deps, run_id, status, reason)
        # Someone asked for this review and was told it's starting: say why it isn't.
        if requested_by_command and reason in COMMAND_SKIP_REPLIES:
            with suppress(GitHubError):
                await github.create_issue_comment(
                    owner, name, pr_number, COMMAND_SKIP_REPLIES[reason].format(sha=head_sha[:7])
                )
        return outcome

    try:
        # The time limit raises TimeoutError here, inside the try, so a slow review is
        # recorded and its check run closed like any other failure.
        async with asyncio.timeout(REVIEW_TIME_LIMIT_SECONDS):
            loaded = await load_repo_config(deps.sessionmaker, github, repository, default_branch)
            config = loaded.config
            config_warnings = loaded.parsed.errors + loaded.parsed.warnings
            if not config.enabled:
                return await skip("skipped", "disabled_in_config")

            live = await github.get_pull_request(owner, name, pr_number)
            if live.state != "open":
                return await skip("skipped", "pr_closed")
            if live.head_sha != head_sha:
                return await skip("superseded", "newer_commit")
            if live.draft and not config.review_drafts:
                return await skip("skipped", "draft")

            pr_files = _to_file_diffs(await github.list_pull_request_files(owner, name, pr_number))
            plan = await _plan_diff(
                deps,
                github,
                owner,
                name,
                pr_id,
                pr_files,
                requested_mode=requested_mode,
                last_reviewed=last_reviewed,
                head_sha=head_sha,
            )
            if plan.nothing_new:
                return await skip("skipped", "no_new_changes")
            await _update_run(
                deps, run_id, mode=plan.mode, mode_reason=plan.mode_reason, from_sha=plan.from_sha
            )
            if plan.status_changes:
                async with deps.sessionmaker() as session:
                    await review_runs.set_comment_statuses(session, plan.status_changes)
                    await session.commit()
                logger.info("review.comments_resolved", changes=len(plan.status_changes))

            if check_run_id is None:
                check_run_id = await github.create_check_run(
                    owner,
                    name,
                    name=review_report.CHECK_RUN_NAME,
                    head_sha=head_sha,
                    output=CheckRunOutput(
                        title="Reviewing…", summary="ReviewPilot is reviewing this PR."
                    ),
                )
                await _update_run(deps, run_id, check_run_id=check_run_id)

            async with deps.sessionmaker() as session:
                already_posted = await review_runs.posted_fingerprints(session, pr_id)
            result = await run_review(
                plan.files,
                config,
                _pr_context(live, repository.full_name),
                deps.llm,
                model=deps.model,
                summary_model=deps.summary_model,
                budget=deps.budget,
                prices=deps.prices,
                existing_fingerprints=already_posted,
                prompt_version=deps.prompt_version,
            )
            result.skipped_files.extend(plan.skipped)

            if await _current_head(deps, pr_id) != head_sha:
                # A push landed while we were thinking: comments would point at old code.
                await _complete_check(
                    github,
                    owner,
                    name,
                    check_run_id,
                    "neutral",
                    CheckRunOutput(
                        title="Superseded",
                        summary="A newer commit arrived; it is being reviewed instead.",
                    ),
                )
                await _save_result(
                    deps,
                    run_id,
                    pr_id,
                    result,
                    _Posting(),
                    status="superseded",
                    skip_reason="newer_commit",
                    started=started,
                )
                return RunOutcome("superseded", reason="newer_commit")

            if result.files_reviewed == 0 and result.errors and not result.comments:
                raise _LLMUnavailable("; ".join(result.errors)[:500])

            posting = await _post_review(
                github,
                owner,
                name,
                pr_number,
                head_sha,
                result,
                post_when_clean=config.post_when_clean,
                config_warnings=config_warnings,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            posted = sum(1 for c in result.comments if c.fingerprint in posting.comment_ids)
            await _complete_check(
                github,
                owner,
                name,
                check_run_id,
                review_report.conclusion_for([*result.comments, *result.still_open]),
                review_report.check_run_output(
                    result, posted=posted, latency_ms=latency_ms, config_warnings=config_warnings
                ),
            )
            await _save_result(
                deps, run_id, pr_id, result, posting, status="completed", started=started
            )
            logger.info(
                "review.completed",
                comments=len(result.comments),
                still_open=len(result.still_open),
                posted=posted,
                cost_usd=result.cost_usd,
                latency_ms=latency_ms,
            )
            return RunOutcome("completed", posted=posted)

    except Exception as exc:
        retryable = _retryable(exc)
        if retryable is not None and not final_attempt:
            # Leave the check run open and the run re-queued; the job retries the whole run.
            if result is not None:
                # This attempt's LLM calls were billed even though we'll redo them.
                async with deps.sessionmaker() as session:
                    session.add_all(review_runs.llm_call_rows(run_id, result.llm_calls))
                    await session.commit()
            await _update_run(deps, run_id, status="queued", error_message=f"retrying: {exc}"[:500])
            logger.warning("review.retrying", error=str(exc)[:200])
            raise retryable from exc
        code = _error_code(exc)
        logger.exception("review.failed", error_code=code)
        if check_run_id is not None:
            with suppress(Exception):
                await _complete_check(
                    github, owner, name, check_run_id, "neutral", _failure_output(code)
                )
        if result is not None:
            with suppress(Exception):
                await _save_result(
                    deps,
                    run_id,
                    pr_id,
                    result,
                    _Posting(),
                    status="failed",
                    started=started,
                    error=(code, str(exc)),
                )
                return RunOutcome("failed", reason=code)
        await _update_run(
            deps,
            run_id,
            status="failed",
            error_code=code,
            error_message=str(exc)[:2000],
            finished_at=datetime.now(UTC),
        )
        return RunOutcome("failed", reason=code)


# ---- posting ----


async def _post_review(
    github: GitHubClient,
    owner: str,
    name: str,
    number: int,
    head_sha: str,
    result: ReviewResult,
    *,
    post_when_clean: bool,
    config_warnings: Sequence[str],
) -> _Posting:
    posting = _Posting()
    body = review_report.review_body(result, config_warnings=config_warnings)
    if not result.comments:
        if post_when_clean:
            posting.review_id = await github.create_review(
                owner, name, number, commit_id=head_sha, body=body, comments=[]
            )
        return posting

    inputs = [review_report.to_review_comment(c) for c in result.comments]
    try:
        # One review = one notification for the author, and all comments land together.
        posting.review_id = await github.create_review(
            owner, name, number, commit_id=head_sha, body=body, comments=inputs
        )
    except GitHubError as exc:
        if exc.status_code != 422:
            raise
        # 422: GitHub rejected at least one comment position, which fails the whole batch.
        # Post comments one by one so the good ones still land, then the summary alone.
        for comment, payload in zip(result.comments, inputs, strict=True):
            try:
                posting.comment_ids[comment.fingerprint] = await github.create_review_comment(
                    owner, name, number, commit_id=head_sha, comment=payload
                )
            except GitHubError as single:
                if single.status_code != 422:
                    raise
                posting.rejected.add(comment.fingerprint)
        posting.review_id = await github.create_review(
            owner, name, number, commit_id=head_sha, body=body, comments=[]
        )
        return posting

    # Map GitHub's comment ids back to ours (needed for reactions/feedback in Phase 4).
    posted = await github.list_review_comments(owner, name, number, posting.review_id)
    unmatched = list(posted)
    for comment, payload in zip(result.comments, inputs, strict=True):
        match = next(
            (
                p
                for p in unmatched
                if p.path == payload.path and p.body.strip() == payload.body.strip()
            ),
            None,
        )
        if match is not None:
            unmatched.remove(match)
            posting.comment_ids[comment.fingerprint] = match.id
    return posting


async def _complete_check(
    github: GitHubClient,
    owner: str,
    name: str,
    check_run_id: int | None,
    conclusion: Conclusion,
    output: CheckRunOutput,
) -> None:
    if check_run_id is None:
        return
    await github.complete_check_run(owner, name, check_run_id, conclusion=conclusion, output=output)


def _failure_output(code: str) -> CheckRunOutput:
    return CheckRunOutput(
        title="ReviewPilot couldn't finish",
        summary=(
            f"The review stopped with an internal error (`{code}`). This check never blocks "
            "merging. Comment `/reviewpilot review` to try again."
        ),
    )


# ---- incremental diff planning ----


@dataclass
class _DiffPlan:
    files: list[FileDiff]
    mode: str = "full"
    mode_reason: str | None = None
    from_sha: str | None = None
    skipped: list[SkippedFile] = field(default_factory=list)
    status_changes: dict[int, CommentStatus] = field(default_factory=dict)
    nothing_new: bool = False


async def _plan_diff(
    deps: ReviewDeps,
    github: GitHubClient,
    owner: str,
    name: str,
    pr_id: int,
    pr_files: list[FileDiff],
    *,
    requested_mode: str,
    last_reviewed: str | None,
    head_sha: str,
) -> _DiffPlan:
    """What to review: the whole PR, or only the commits since the last review."""
    if requested_mode != "incremental":
        return _DiffPlan(pr_files)
    if not last_reviewed:
        return _DiffPlan(pr_files, mode_reason="no_previous_review")
    if last_reviewed == head_sha:
        return _DiffPlan([], nothing_new=True)
    try:
        compare = await github.compare_commits(owner, name, last_reviewed, head_sha)
    except GitHubError as exc:
        if exc.status_code != 404:
            raise
        # The last reviewed commit no longer exists (force push + garbage collection).
        return _DiffPlan(pr_files, mode_reason="history_rewritten")
    if compare.status == "identical":
        return _DiffPlan([], nothing_new=True)
    if compare.status != "ahead":
        # diverged/behind: history was rewritten, so last...head isn't "what's new".
        return _DiffPlan(pr_files, mode_reason="history_rewritten")

    changed = _to_file_diffs(compare.files)
    files, skipped = restrict_to_pr_diff(changed, pr_files)
    async with deps.sessionmaker() as session:
        open_comments = await review_runs.open_posted_comments(session, pr_id)
    return _DiffPlan(
        files,
        mode="incremental",
        from_sha=last_reviewed,
        skipped=skipped,
        status_changes=detect_status_changes(open_comments, changed, base_sha=last_reviewed),
    )


# ---- persistence ----


async def _save_result(
    deps: ReviewDeps,
    run_id: int,
    pr_id: int,
    result: ReviewResult,
    posting: _Posting,
    *,
    status: str,
    started: float,
    skip_reason: str | None = None,
    error: tuple[str, str] | None = None,
) -> None:
    now = datetime.now(UTC)
    async with deps.sessionmaker() as session:
        run = await session.get(ReviewRun, run_id)
        if run is None:
            raise RuntimeError(f"review run {run_id} disappeared")
        run.status, run.skip_reason, run.finished_at = status, skip_reason, now
        run.latency_ms = int((time.monotonic() - started) * 1000)
        run.files_total, run.files_reviewed = result.files_total, result.files_reviewed
        run.files_skipped = [{"path": s.path, "reason": s.reason} for s in result.skipped_files]
        run.comments_generated = len(result.comments) + len(result.dropped)
        run.comments_posted = sum(
            1 for c in result.comments if c.fingerprint in posting.comment_ids
        )
        for column, reasons in DROP_BUCKETS.items():
            setattr(run, column, sum(1 for d in result.dropped if d.reason in reasons))
        run.input_tokens, run.output_tokens = result.input_tokens, result.output_tokens
        run.cost_usd = review_runs.to_money(result.cost_usd)
        run.github_review_id = posting.review_id
        run.summary = result.summary.model_dump() if result.summary else None
        if error:
            run.error_code, run.error_message = error[0], error[1][:2000]
        elif result.errors:
            run.error_message = "; ".join(result.errors)[:2000]

        session.add_all(review_runs.llm_call_rows(run_id, result.llm_calls))
        session.add_all(_comment_rows(run_id, pr_id, result.comments, result.dropped, posting))
        if status == "completed":
            await session.execute(
                update(PullRequest)
                .where(PullRequest.id == pr_id)
                .values(last_reviewed_sha=run.head_sha, updated_at=func.now())
            )
        await session.commit()


def _comment_rows(
    run_id: int,
    pr_id: int,
    kept: Sequence[ReviewComment],
    dropped: Sequence[DroppedComment],
    posting: _Posting,
) -> list[ReviewCommentRecord]:
    rows = []
    for c in kept:
        github_id = posting.comment_ids.get(c.fingerprint)
        rows.append(
            ReviewCommentRecord(
                review_run_id=run_id,
                pull_request_id=pr_id,
                path=c.path,
                line=c.line,
                start_line=c.start_line,
                severity=c.severity,
                category=c.category,
                title=c.title[:200],
                body=c.body,
                suggestion=c.suggestion,
                confidence=c.confidence,
                source=c.source,
                fingerprint=c.fingerprint,
                code_snapshot=c.code_snapshot,
                posted=github_id is not None,
                github_comment_id=github_id,
                drop_reason=(
                    "github_rejected"
                    if c.fingerprint in posting.rejected
                    else None
                    if github_id is not None
                    else "not_posted"
                ),
            )
        )
    for d in dropped:
        rows.append(
            ReviewCommentRecord(
                review_run_id=run_id,
                pull_request_id=pr_id,
                path=d.path,
                line=d.line,
                severity=d.severity,
                category=d.category,
                title=d.title[:200],
                posted=False,
                drop_reason=d.reason,
            )
        )
    return rows


async def _update_run(deps: ReviewDeps, run_id: int, **values: object) -> None:
    async with deps.sessionmaker() as session:
        await session.execute(
            update(ReviewRun).where(ReviewRun.id == run_id).values(**values, updated_at=func.now())
        )
        await session.commit()


async def _finish(session: AsyncSession, run: ReviewRun, status: str, reason: str) -> RunOutcome:
    run.status, run.skip_reason, run.finished_at = status, reason, datetime.now(UTC)
    await session.commit()
    return RunOutcome(status, reason=reason)


async def _finish_by_id(deps: ReviewDeps, run_id: int, status: str, reason: str) -> RunOutcome:
    await _update_run(
        deps, run_id, status=status, skip_reason=reason, finished_at=datetime.now(UTC)
    )
    return RunOutcome(status, reason=reason)


async def _current_head(deps: ReviewDeps, pr_id: int) -> str | None:
    """The PR head as last reported by webhooks (updated on every push)."""
    async with deps.sessionmaker() as session:
        pr = await session.get(PullRequest, pr_id, populate_existing=True)
        return pr.head_sha if pr else None


# ---- helpers ----


class _LLMUnavailable(Exception):
    pass


def _retryable(exc: Exception) -> RetryableReviewError | None:
    if isinstance(exc, GitHubRateLimited):
        return RetryableReviewError(str(exc), retry_after=exc.retry_after)
    if isinstance(exc, httpx.TransportError):
        return RetryableReviewError(f"network error: {type(exc).__name__}")
    if isinstance(exc, GitHubError) and exc.status_code >= 500:
        return RetryableReviewError(str(exc))
    return None


def _error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):  # REVIEW_TIME_LIMIT_SECONDS
        return "timeout"
    if isinstance(exc, _LLMUnavailable):
        return "llm_unavailable"
    if isinstance(exc, GitHubError):
        return f"github_{exc.status_code}"
    return "internal_error"


def _to_file_diffs(files: Sequence[PullRequestFile]) -> list[FileDiff]:
    diffs = []
    for f in files:
        status = cast(FileStatus, f.status if f.status in FILE_STATUSES else "modified")
        diffs.append(
            parse_patch(f.filename, f.patch, status=status, previous_path=f.previous_filename)
        )
    return diffs


def _pr_context(pr: PullRequestInfo, repo_full_name: str) -> PRContext:
    return PRContext(
        title=pr.title,
        description=pr.body or "",
        repo_full_name=repo_full_name,
        base_ref=pr.base_ref,
        head_ref=pr.head_ref,
        author=pr.author,
    )
