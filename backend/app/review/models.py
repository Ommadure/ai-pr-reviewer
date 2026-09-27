"""Data shapes for the pure review engine.

Nothing in app/review/ does HTTP or touches the database (ADR 0007): inputs are
parsed diffs + repo config, output is a ReviewResult. That's what makes the
engine unit-testable, usable from a CLI, and reusable by the eval harness.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

# ---- diffs ----

LineType = Literal["added", "removed", "context"]
FileStatus = Literal["added", "removed", "modified", "renamed", "copied", "changed", "unchanged"]


@dataclass(frozen=True)
class DiffLine:
    type: LineType
    content: str  # without the leading "+", "-" or " "
    old_line: int | None  # None for added lines
    new_line: int | None  # None for removed lines


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    section: str  # text after the second "@@", often the enclosing function
    lines: tuple[DiffLine, ...]

    @property
    def header(self) -> str:
        section = f" {self.section}" if self.section else ""
        return f"@@ -{self.old_start},{self.old_len} +{self.new_start},{self.new_len} @@{section}"


@dataclass(frozen=True)
class FileDiff:
    path: str
    status: FileStatus
    hunks: tuple[Hunk, ...] = ()
    previous_path: str | None = None
    is_binary: bool = False
    # GitHub omits `patch` for diffs it considers too large (and for binaries).
    patch_missing: bool = False

    @property
    def additions(self) -> int:
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.type == "added")

    @property
    def deletions(self) -> int:
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.type == "removed")

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions

    def commentable_lines(self, *, include_context: bool = False) -> set[int]:
        """New-side line numbers a review comment may point at."""
        allowed: set[LineType] = {"added", "context"} if include_context else {"added"}
        return {
            line.new_line
            for hunk in self.hunks
            for line in hunk.lines
            if line.type in allowed and line.new_line is not None
        }

    def new_side_lines(self) -> dict[int, DiffLine]:
        """new line number -> line, for every added or context line."""
        return {
            line.new_line: line
            for hunk in self.hunks
            for line in hunk.lines
            if line.new_line is not None
        }

    def hunk_index_of(self, new_line: int) -> int | None:
        for index, hunk in enumerate(self.hunks):
            if any(line.new_line == new_line for line in hunk.lines):
                return index
        return None


# ---- LLM structured output (validated with Pydantic) ----

Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal[
    "bug",
    "security",
    "performance",
    "maintainability",
    "error_handling",
    "testing",
    "style",
    "docs",
]
SEVERITY_RANK: dict[Severity, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


class LLMComment(BaseModel):
    path: str
    line: int
    start_line: int | None = None
    severity: Severity
    category: Category
    title: str = Field(max_length=100)
    body: str = Field(max_length=1200)  # markdown: the problem and why it matters
    suggestion: str | None = None  # exact replacement for lines start_line..line
    confidence: float = Field(ge=0, le=1)


class FileSummary(BaseModel):
    path: str
    summary: str = Field(max_length=400)


class FileReviewOutput(BaseModel):
    comments: list[LLMComment] = Field(default_factory=list)
    file_summaries: list[FileSummary] = Field(default_factory=list)


class PRSummaryOutput(BaseModel):
    overview: str
    risk_level: Literal["low", "medium", "high"]
    key_changes: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---- engine output ----

CommentSource = Literal["llm", "secret_scanner"]
DropReason = Literal[
    "unknown_path",
    "invalid_line",
    "invalid_range",
    "low_confidence",
    "below_min_severity",
    "out_of_focus",
    "duplicate",
    "already_posted",
    "over_limit",
]


@dataclass(frozen=True)
class ReviewComment:
    path: str
    line: int
    start_line: int | None
    severity: Severity
    category: Category
    title: str
    body: str
    suggestion: str | None
    confidence: float
    source: CommentSource
    fingerprint: str
    code_snapshot: str  # the target line(s) text, used later for "addressed" detection
    suggestion_dropped: bool = False


@dataclass(frozen=True)
class DroppedComment:
    path: str
    line: int
    title: str
    severity: Severity
    category: Category
    reason: DropReason


@dataclass(frozen=True)
class SkippedFile:
    path: str
    reason: str


LLMCallPurpose = Literal["file_review", "summary", "repair"]


@dataclass(frozen=True)
class LLMCallRecord:
    purpose: LLMCallPurpose
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    status: Literal["success", "error"]
    error: str | None = None


@dataclass(frozen=True)
class PRContext:
    """PR metadata shown to the model (untrusted, delimited in the prompt)."""

    title: str
    description: str = ""
    repo_full_name: str = ""
    base_ref: str = ""
    head_ref: str = ""
    author: str = ""


@dataclass(frozen=True)
class ReviewBudget:
    max_files: int = 50
    max_input_tokens: int = 60_000  # per review run
    max_chunk_tokens: int = 12_000  # per LLM call
    max_concurrent_llm_calls: int = 2
    max_output_tokens: int = 8_192  # per LLM call: a ceiling on what one answer can bill
    max_cost_usd: float | None = None  # per review run; None = no cap


@dataclass
class ReviewResult:
    comments: list[ReviewComment]
    dropped: list[DroppedComment]
    summary: PRSummaryOutput | None
    skipped_files: list[SkippedFile]
    llm_calls: list[LLMCallRecord]
    prompt_version: str
    model: str
    files_total: int
    files_reviewed: int
    errors: list[str] = field(default_factory=list)
    # Re-found in this run, but already posted on the same, unchanged code (same
    # fingerprint): not posted again, yet still present, so they still count for the
    # check's title and conclusion and the summary's risk.
    still_open: list[ReviewComment] = field(default_factory=list)

    @property
    def input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.llm_calls)

    @property
    def output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.llm_calls)

    @property
    def cost_usd(self) -> float:
        return sum(call.cost_usd for call in self.llm_calls)

    def dropped_count(self, reason: DropReason) -> int:
        return sum(1 for comment in self.dropped if comment.reason == reason)
