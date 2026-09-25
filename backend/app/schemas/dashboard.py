"""Response shapes for the dashboard API (also the source of the frontend's TS types)."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel


class Page[T](BaseModel):
    """Cursor pagination: pass `next_cursor` back as `cursor` to get the next page."""

    items: list[T]
    next_cursor: str | None = None


class InstallationOut(BaseModel):
    id: int
    github_installation_id: int
    account_login: str
    account_type: str
    suspended: bool
    repositories_count: int


class InstallationsResponse(BaseModel):
    installations: list[InstallationOut]
    install_url: str


ConfigStatus = Literal["valid", "invalid", "none", "unknown"]


class RepositoryOut(BaseModel):
    id: int
    installation_id: int
    full_name: str
    private: bool
    enabled: bool
    default_branch: str | None
    last_reviewed_at: datetime | None
    config_status: ConfigStatus
    open_pulls: int


class RepositoryUpdate(BaseModel):
    enabled: bool


class RepositoryConfigOut(BaseModel):
    commit_sha: str
    is_valid: bool
    has_file: bool
    raw_yaml: str | None
    parsed: dict[str, Any]
    errors: list[str]
    warnings: list[str]
    fetched_at: datetime


class RunSummaryOut(BaseModel):
    id: int
    trigger: str
    mode: str
    status: str
    skip_reason: str | None
    head_sha: str
    files_reviewed: int
    comments_posted: int
    cost_usd: float
    latency_ms: int | None
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class PullOut(BaseModel):
    id: int
    repository_id: int
    repository_full_name: str
    number: int
    title: str
    author_login: str
    state: str
    draft: bool
    paused: bool
    head_sha: str
    updated_at: datetime
    html_url: str
    last_run: RunSummaryOut | None


class PullDetailOut(PullOut):
    runs: list[RunSummaryOut]


class CommentOut(BaseModel):
    id: int
    path: str
    line: int
    start_line: int | None
    severity: str
    category: str
    title: str
    body: str
    suggestion: str | None
    confidence: float | None
    source: str
    posted: bool
    drop_reason: str | None
    status: str
    thumbs_up: int
    thumbs_down: int
    github_url: str | None


class LLMCallOut(BaseModel):
    id: int
    purpose: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    status: str
    error: str | None


class SkippedFileOut(BaseModel):
    path: str
    reason: str


class RunDetailOut(RunSummaryOut):
    pull_request_id: int
    pull_request_number: int
    pull_request_title: str
    repository_full_name: str
    base_sha: str
    from_sha: str | None
    mode_reason: str | None
    prompt_version: str | None
    model: str | None
    files_total: int
    files_skipped: list[SkippedFileOut]
    comments_generated: int
    input_tokens: int
    output_tokens: int
    error_message: str | None
    summary: dict[str, Any] | None
    github_review_url: str | None
    started_at: datetime | None
    llm_calls: list[LLMCallOut]
    comments: list[CommentOut]


class RereviewResponse(BaseModel):
    run_id: int


class DayCount(BaseModel):
    day: date
    count: int


class DayCost(BaseModel):
    day: date
    cost_usd: float


class FileCount(BaseModel):
    repository_full_name: str
    path: str
    count: int


class RateOut(BaseModel):
    posted: int
    helpful: int
    negative: int
    helpful_rate: float | None
    negative_rate: float | None


class AnalyticsOverview(BaseModel):
    range_days: int
    runs_total: int
    runs_completed: int
    runs_failed: int
    comments_posted: int
    cost_usd_total: float
    usd_to_inr: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    feedback: RateOut
    feedback_by_category: dict[str, RateOut]
    feedback_by_severity: dict[str, RateOut]
    reviews_per_day: list[DayCount]
    cost_per_day: list[DayCost]
    comments_by_severity: dict[str, int]
    comments_by_category: dict[str, int]
    top_files: list[FileCount]
