"""Dashboard API. Every endpoint is tenant-scoped through `ScopeDep` (ADR 0012).

Anything outside the caller's installations is a 404, exactly like a missing id.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.deps import ScopeDep, SessionDep, SettingsDep, wake_worker
from app.models import LLMCall, PullRequest, ReviewCommentRecord, ReviewRun
from app.repositories import dashboard, jobs, review_runs
from app.schemas.dashboard import (
    AnalyticsOverview,
    CommentOut,
    DayCost,
    DayCount,
    FileCount,
    InstallationOut,
    InstallationsResponse,
    LLMCallOut,
    Page,
    PullDetailOut,
    PullOut,
    RateOut,
    RepositoryConfigOut,
    RepositoryOut,
    RepositoryUpdate,
    RereviewResponse,
    RunDetailOut,
    RunSummaryOut,
    SkippedFileOut,
)
from app.services import analytics
from app.services.commands import MANUAL_REVIEWS_PER_HOUR

router = APIRouter(tags=["dashboard"])


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


RANGES = {"7d": 7, "30d": 30, "90d": 90}


# ---- installations & repositories ----


@router.get("/installations")
async def list_installations(
    scope: ScopeDep, session: SessionDep, settings: SettingsDep
) -> InstallationsResponse:
    rows = await dashboard.installations(session, scope.installation_ids)
    return InstallationsResponse(
        installations=[
            InstallationOut(
                id=inst.id,
                github_installation_id=inst.github_installation_id,
                account_login=inst.account_login,
                account_type=inst.account_type,
                suspended=inst.suspended_at is not None,
                repositories_count=count,
            )
            for inst, count in rows
        ],
        install_url=f"https://github.com/apps/{settings.github_app_slug}/installations/new",
    )


@router.get("/installations/{installation_id}/repositories")
async def list_repositories(
    installation_id: int, scope: ScopeDep, session: SessionDep
) -> list[RepositoryOut]:
    if installation_id not in scope.installation_ids:
        raise _not_found()
    rows = await dashboard.repositories(
        session, scope.installation_ids, installation_id=installation_id
    )
    return [_repository_out(row) for row in rows]


@router.get("/repositories")
async def list_all_repositories(scope: ScopeDep, session: SessionDep) -> list[RepositoryOut]:
    rows = await dashboard.repositories(session, scope.installation_ids)
    return [_repository_out(row) for row in rows]


@router.patch("/repositories/{repository_id}")
async def update_repository(
    repository_id: int, body: RepositoryUpdate, scope: ScopeDep, session: SessionDep
) -> RepositoryOut:
    repo = await dashboard.repository(session, scope.installation_ids, repository_id)
    if repo is None:
        raise _not_found()
    repo.enabled = body.enabled
    await session.commit()
    [row] = [
        r
        for r in await dashboard.repositories(session, scope.installation_ids)
        if r.repository.id == repository_id
    ]
    return _repository_out(row)


@router.get("/repositories/{repository_id}/config")
async def repository_config(
    repository_id: int, scope: ScopeDep, session: SessionDep
) -> RepositoryConfigOut | None:
    if await dashboard.repository(session, scope.installation_ids, repository_id) is None:
        raise _not_found()
    config = await dashboard.latest_config(session, repository_id)
    if config is None:
        return None  # not fetched yet: fetched on the repo's first review
    return RepositoryConfigOut(
        commit_sha=config.commit_sha,
        is_valid=config.is_valid,
        has_file=config.raw_yaml is not None,
        raw_yaml=config.raw_yaml,
        parsed=config.parsed,
        errors=list(config.errors),
        warnings=list(config.warnings),
        fetched_at=config.fetched_at,
    )


# ---- pull requests & runs ----


@router.get("/repositories/{repository_id}/pulls")
async def list_pulls(
    repository_id: int,
    scope: ScopeDep,
    session: SessionDep,
    state: Literal["open", "closed", "merged"] | None = None,
    cursor: Annotated[int | None, Query(description="next_cursor from the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[PullOut]:
    if await dashboard.repository(session, scope.installation_ids, repository_id) is None:
        raise _not_found()
    prs = await dashboard.pulls(
        session,
        scope.installation_ids,
        repository_id=repository_id,
        state=state,
        cursor=cursor,
        limit=limit,
    )
    last = await dashboard.latest_runs(session, [pr.id for pr in prs])
    return Page(
        items=[_pull_out(pr, last.get(pr.id)) for pr in prs],
        next_cursor=str(prs[-1].id) if len(prs) == limit else None,
    )


@router.get("/pulls/{pull_request_id}")
async def get_pull(pull_request_id: int, scope: ScopeDep, session: SessionDep) -> PullDetailOut:
    pr = await dashboard.pull(session, scope.installation_ids, pull_request_id)
    if pr is None:
        raise _not_found()
    runs = await dashboard.runs_for_pull(session, pr.id)
    base = _pull_out(pr, runs[0] if runs else None)
    return PullDetailOut(**base.model_dump(), runs=[_run_summary(r) for r in runs])


@router.post("/pulls/{pull_request_id}/rereview", status_code=status.HTTP_202_ACCEPTED)
async def rereview(
    pull_request_id: int,
    request: Request,
    scope: ScopeDep,
    session: SessionDep,
) -> RereviewResponse:
    pr = await dashboard.pull(session, scope.installation_ids, pull_request_id)
    if pr is None:
        raise _not_found()
    if pr.state != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only open pull requests can be reviewed")
    # Same budget as `/reviewpilot review`: each manual review costs LLM calls.
    if await review_runs.manual_runs_last_hour(session, pr.id) >= MANUAL_REVIEWS_PER_HOUR:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"At most {MANUAL_REVIEWS_PER_HOUR} manual reviews per pull request per hour",
        )
    run = await review_runs.create_queued(
        session,
        pull_request_id=pr.id,
        trigger="manual",
        mode="full",
        base_sha=pr.base_sha,
        head_sha=pr.head_sha,
    )
    jobs.enqueue_review(session, run_id=run.id, pull_request_id=pr.id)
    await session.commit()
    wake_worker(request)
    return RereviewResponse(run_id=run.id)


@router.get("/runs/{run_id}")
async def get_run(run_id: int, scope: ScopeDep, session: SessionDep) -> RunDetailOut:
    run = await dashboard.run(session, scope.installation_ids, run_id)
    if run is None:
        raise _not_found()
    pr = run.pull_request
    full_name = pr.repository.full_name
    calls = await dashboard.run_llm_calls(session, run.id)
    comments = await dashboard.run_comments(session, run.id)
    return RunDetailOut(
        **_run_summary(run).model_dump(),
        pull_request_id=pr.id,
        pull_request_number=pr.number,
        pull_request_title=pr.title,
        repository_full_name=full_name,
        base_sha=run.base_sha,
        from_sha=run.from_sha,
        mode_reason=run.mode_reason,
        prompt_version=run.prompt_version,
        model=run.model,
        files_total=run.files_total,
        files_skipped=[SkippedFileOut(**item) for item in run.files_skipped],
        comments_generated=run.comments_generated,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        error_message=run.error_message,
        summary=run.summary,
        github_review_url=(
            f"{_pr_url(full_name, pr.number)}#pullrequestreview-{run.github_review_id}"
            if run.github_review_id
            else None
        ),
        started_at=run.started_at,
        llm_calls=[_llm_call_out(c) for c in calls],
        comments=[_comment_out(c, full_name, pr.number) for c in comments],
    )


# ---- analytics ----


@router.get("/analytics/overview")
async def analytics_overview(
    scope: ScopeDep,
    session: SessionDep,
    settings: SettingsDep,
    range: Literal["7d", "30d", "90d"] = "30d",
    repository_id: int | None = None,
) -> AnalyticsOverview:
    repo_ids = await dashboard.scoped_repository_ids(session, scope.installation_ids, repository_id)
    if repository_id is not None and not repo_ids:
        raise _not_found()
    days = RANGES[range]
    data = await analytics.overview(
        session, repository_ids=repo_ids, since=datetime.now(UTC) - timedelta(days=days)
    )
    return AnalyticsOverview(
        range_days=days,
        runs_total=data.runs_total,
        runs_completed=data.runs_completed,
        runs_failed=data.runs_failed,
        comments_posted=data.comments_posted,
        cost_usd_total=data.cost_usd_total,
        usd_to_inr=settings.usd_to_inr,
        avg_latency_ms=data.avg_latency_ms,
        p95_latency_ms=data.p95_latency_ms,
        feedback=_rate_out(data.feedback.overall),
        feedback_by_category={k: _rate_out(v) for k, v in data.feedback.by_category.items()},
        feedback_by_severity={k: _rate_out(v) for k, v in data.feedback.by_severity.items()},
        reviews_per_day=[DayCount(day=d, count=n) for d, n in data.reviews_per_day],
        cost_per_day=[DayCost(day=d, cost_usd=c) for d, c in data.cost_per_day],
        comments_by_severity=data.comments_by_severity,
        comments_by_category=data.comments_by_category,
        top_files=[
            FileCount(repository_full_name=repo, path=path, count=n)
            for repo, path, n in data.top_files
        ],
    )


# ---- mapping ----


def _repository_out(row: dashboard.RepositoryRow) -> RepositoryOut:
    repo = row.repository
    if row.config_valid is None:
        config_status = "unknown"
    elif not row.config_has_file:
        config_status = "none"
    else:
        config_status = "valid" if row.config_valid else "invalid"
    return RepositoryOut(
        id=repo.id,
        installation_id=repo.installation_id,
        full_name=repo.full_name,
        private=repo.private,
        enabled=repo.enabled,
        default_branch=repo.default_branch,
        last_reviewed_at=row.last_reviewed_at,
        config_status=config_status,
        open_pulls=row.open_pulls,
    )


def _pull_out(pr: PullRequest, last_run: ReviewRun | None) -> PullOut:
    return PullOut(
        id=pr.id,
        repository_id=pr.repository_id,
        repository_full_name=pr.repository.full_name,
        number=pr.number,
        title=pr.title,
        author_login=pr.author_login,
        state=pr.state,
        draft=pr.draft,
        paused=pr.paused,
        head_sha=pr.head_sha,
        updated_at=pr.updated_at,
        html_url=_pr_url(pr.repository.full_name, pr.number),
        last_run=_run_summary(last_run) if last_run else None,
    )


def _run_summary(run: ReviewRun) -> RunSummaryOut:
    return RunSummaryOut(
        id=run.id,
        trigger=run.trigger,
        mode=run.mode,
        status=run.status,
        skip_reason=run.skip_reason,
        head_sha=run.head_sha,
        files_reviewed=run.files_reviewed,
        comments_posted=run.comments_posted,
        cost_usd=float(run.cost_usd),
        latency_ms=run.latency_ms,
        error_code=run.error_code,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _llm_call_out(call: LLMCall) -> LLMCallOut:
    return LLMCallOut(
        id=call.id,
        purpose=call.purpose,
        model=call.model,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        cost_usd=float(call.cost_usd),
        latency_ms=call.latency_ms,
        status=call.status,
        error=call.error,
    )


def _comment_out(c: ReviewCommentRecord, full_name: str, number: int) -> CommentOut:
    return CommentOut(
        id=c.id,
        path=c.path,
        line=c.line,
        start_line=c.start_line,
        severity=c.severity,
        category=c.category,
        title=c.title,
        body=c.body,
        suggestion=c.suggestion,
        confidence=c.confidence,
        source=c.source,
        posted=c.posted,
        drop_reason=c.drop_reason,
        status=c.status,
        thumbs_up=c.thumbs_up,
        thumbs_down=c.thumbs_down,
        github_url=(
            f"{_pr_url(full_name, number)}#discussion_r{c.github_comment_id}"
            if c.github_comment_id
            else None
        ),
    )


def _rate_out(rates: analytics.Rates) -> RateOut:
    return RateOut(
        posted=rates.posted,
        helpful=rates.helpful,
        negative=rates.negative,
        helpful_rate=rates.helpful_rate,
        negative_rate=rates.negative_rate,
    )


def _pr_url(full_name: str, number: int) -> str:
    return f"https://github.com/{full_name}/pull/{number}"
