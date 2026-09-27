"""run_review(): the whole review, as a pure function of its inputs (ADR 0007).

    files ─► filter ─► redact ─► prioritise ─► chunk ─► LLM (map, concurrent)
                          │                                   │
                          └─► secret comments                 ▼
                                                validate ─► dedupe/rank/cap
                                                              │
                                   file summaries ─► LLM (reduce) ─► summary

No HTTP (other than through the injected LLM provider) and no database. The
orchestrator (Phase 3) and the eval harness (Phase 6) both call this.
"""

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.config.repo_config import RepoConfig
from app.review import prompt_builder
from app.review.chunker import Chunk, build_chunks
from app.review.filters import filter_files
from app.review.fingerprint import fingerprint
from app.review.llm.base import LLMError, LLMInvalidOutput, LLMProvider, Message, TokenUsage
from app.review.models import (
    SEVERITY_RANK,
    DroppedComment,
    FileDiff,
    FileReviewOutput,
    FileSummary,
    LLMCallPurpose,
    LLMCallRecord,
    PRContext,
    PRSummaryOutput,
    ReviewBudget,
    ReviewComment,
    ReviewResult,
    SkippedFile,
)
from app.review.pricing import PriceTable
from app.review.prioritizer import limit_files
from app.review.redaction import SecretFinding, redact_files
from app.review.tokens import estimate_tokens
from app.review.validator import finalize_comments, still_open, validate_llm_comments

TEMPERATURE = 0.1  # low: we want consistent, conservative reviews, not creativity


@dataclass
class _CallOutcome:
    records: list[LLMCallRecord] = field(default_factory=list)
    error: str | None = None
    skip_reason: str = "llm_error"


class CostGuard:
    """Keeps a run under `cap` dollars, even with calls running concurrently.

    Before a call starts, its worst case (estimated input + the full output ceiling)
    is reserved; afterwards the reservation is swapped for the real cost. A call that
    doesn't fit is never made. Unpriced models cost 0, so only the token caps apply.
    """

    def __init__(self, cap: float | None) -> None:
        self.cap = cap
        self.committed = 0.0

    def reserve(self, amount: float) -> bool:
        if self.cap is not None and self.committed + amount > self.cap:
            return False
        self.committed += amount
        return True

    def settle(self, reserved: float, actual: float) -> None:
        self.committed += actual - reserved


@dataclass
class _ChunkOutcome:
    chunk: Chunk
    output: FileReviewOutput | None
    call: _CallOutcome


async def run_review(
    files: Sequence[FileDiff],
    config: RepoConfig,
    pr: PRContext,
    llm: LLMProvider,
    *,
    model: str,
    summary_model: str | None = None,
    budget: ReviewBudget | None = None,
    prices: PriceTable | None = None,
    existing_fingerprints: Iterable[str] = frozenset(),
    prompt_version: str = prompt_builder.DEFAULT_PROMPT_VERSION,
    summarize: bool = True,
) -> ReviewResult:
    """Review `files`. `summarize=False` skips the PR-summary call (the eval harness
    scores comments only, and free-tier quotas are small)."""
    budget = budget or ReviewBudget()
    prices = prices or PriceTable()
    skipped: list[SkippedFile] = []

    candidates, filtered_out = filter_files(files, config.ignore_paths)
    skipped += filtered_out
    # Redact before anything else looks at content: nothing downstream sees a secret.
    redacted, secrets = redact_files(candidates)
    secret_comments = [_secret_comment(finding, redacted) for finding in secrets]
    selected, over_limit = limit_files(redacted, budget.max_files)
    skipped += over_limit

    overhead = estimate_tokens(
        "\n".join(
            m.content
            for m in prompt_builder.build_review_messages([], pr, config, version=prompt_version)
        )
    )
    chunks, over_budget = build_chunks(
        selected,
        max_chunk_tokens=budget.max_chunk_tokens,
        max_run_tokens=budget.max_input_tokens,
        overhead_tokens=overhead,
    )
    skipped += over_budget

    # Map: one LLM call per chunk, at most N in flight (free tiers have low rate limits).
    semaphore = asyncio.Semaphore(budget.max_concurrent_llm_calls)
    guard = CostGuard(budget.max_cost_usd)

    async def review_chunk(chunk: Chunk) -> _ChunkOutcome:
        messages = prompt_builder.build_review_messages(
            chunk.files, pr, config, version=prompt_version
        )
        async with semaphore:
            output, call = await _structured_call(
                llm,
                messages,
                FileReviewOutput,
                model=model,
                purpose="file_review",
                prices=prices,
                guard=guard,
                max_output_tokens=budget.max_output_tokens,
            )
        return _ChunkOutcome(chunk, output, call)

    outcomes = await asyncio.gather(*(review_chunk(chunk) for chunk in chunks))

    llm_calls: list[LLMCallRecord] = []
    errors: list[str] = []
    llm_comments: list[ReviewComment] = []
    dropped: list[DroppedComment] = []
    summaries: dict[str, list[str]] = {}
    reviewed_paths: set[str] = set()
    for outcome in outcomes:
        llm_calls += outcome.call.records
        paths = [file.path for file in outcome.chunk.files]
        if outcome.output is None:
            errors.append(outcome.call.error or "file_review failed")
            skipped += [SkippedFile(path, outcome.call.skip_reason) for path in paths]
            continue
        reviewed_paths.update(paths)
        kept, rejected = validate_llm_comments(outcome.output.comments, outcome.chunk.files, config)
        llm_comments += kept
        dropped += rejected
        for file_summary in outcome.output.file_summaries:
            if file_summary.path in paths:  # ignore summaries of files not in this chunk
                summaries.setdefault(file_summary.path, []).append(file_summary.summary)

    found = [*secret_comments, *llm_comments]
    comments, rejected = finalize_comments(
        found, max_comments=config.max_comments, existing_fingerprints=existing_fingerprints
    )
    dropped += rejected
    reported_before = still_open(found, existing_fingerprints)

    # Reduce: one small call turns per-file notes into a PR-level summary.
    summary: PRSummaryOutput | None = None
    if summarize and reviewed_paths:
        file_summaries = [FileSummary(path=p, summary=" ".join(s)) for p, s in summaries.items()]
        # Issues reported on an earlier commit and still present count for the risk
        # too: a re-review that only re-finds a SQL injection is not a low-risk PR.
        earlier = {c.fingerprint for c in reported_before}
        issues = [
            f"[{c.severity}] {c.path}:{c.line} {c.title}"
            + (" (reported earlier, still present)" if c.fingerprint in earlier else "")
            for c in sorted([*comments, *reported_before], key=lambda c: -SEVERITY_RANK[c.severity])
        ]
        messages = prompt_builder.build_summary_messages(
            pr, file_summaries, issues, language=config.summary_language, version=prompt_version
        )
        summary, call = await _structured_call(
            llm,
            messages,
            PRSummaryOutput,
            model=summary_model or model,
            purpose="summary",
            prices=prices,
            guard=guard,
            max_output_tokens=budget.max_output_tokens,
        )
        llm_calls += call.records
        if call.error:
            errors.append(call.error)

    return ReviewResult(
        comments=comments,
        dropped=dropped,
        summary=summary,
        skipped_files=skipped,
        llm_calls=llm_calls,
        prompt_version=prompt_version,
        model=model,
        files_total=len(files),
        files_reviewed=len(reviewed_paths),
        errors=errors,
        still_open=reported_before,
    )


async def _structured_call[T: BaseModel](
    llm: LLMProvider,
    messages: list[Message],
    schema: type[T],
    *,
    model: str,
    purpose: LLMCallPurpose,
    prices: PriceTable,
    guard: CostGuard | None = None,
    max_output_tokens: int | None = None,
) -> tuple[T | None, _CallOutcome]:
    """One call, plus exactly one repair attempt if the output is invalid.

    With a guard, each call first reserves its worst-case cost; a call that would push
    the run over its cost cap is not made at all.
    """
    outcome = _CallOutcome()
    guard = guard or CostGuard(None)

    def worst_case(msgs: list[Message]) -> float:
        prompt_tokens = estimate_tokens("\n".join(m.content for m in msgs))
        return prices.cost_usd(model, TokenUsage(prompt_tokens, max_output_tokens or 0))

    def over_budget() -> tuple[None, _CallOutcome]:
        outcome.error = f"{purpose}: skipped, the run's cost cap would be exceeded"
        outcome.skip_reason = "cost_cap"
        return None, outcome

    reserved = worst_case(messages)
    if not guard.reserve(reserved):
        return over_budget()
    try:
        result = await llm.generate_structured(
            messages,
            schema,
            model=model,
            temperature=TEMPERATURE,
            max_output_tokens=max_output_tokens,
        )
    except LLMInvalidOutput as invalid:
        guard.settle(reserved, prices.cost_usd(model, invalid.usage))
        outcome.records.append(
            _record(
                purpose, model, prices, invalid.usage, invalid.latency_ms, error="invalid_output"
            )
        )
        repair = prompt_builder.build_repair_messages(messages, invalid.raw_text, invalid.problem)
        reserved = worst_case(repair)
        if not guard.reserve(reserved):
            return over_budget()
        try:
            result = await llm.generate_structured(
                repair,
                schema,
                model=model,
                temperature=TEMPERATURE,
                max_output_tokens=max_output_tokens,
            )
        except LLMError as exc:
            guard.settle(reserved, prices.cost_usd(model, exc.usage))
            outcome.records.append(
                _record("repair", model, prices, exc.usage, exc.latency_ms, error=str(exc))
            )
            outcome.error = f"{purpose}: output still invalid after repair ({exc})"
            return None, outcome
        guard.settle(reserved, prices.cost_usd(result.model, result.usage))
        outcome.records.append(
            _record("repair", result.model, prices, result.usage, result.latency_ms)
        )
        return result.output, outcome
    except LLMError as exc:
        guard.settle(reserved, prices.cost_usd(model, exc.usage))
        outcome.records.append(
            _record(purpose, model, prices, exc.usage, exc.latency_ms, error=str(exc))
        )
        outcome.error = f"{purpose}: {exc}"
        return None, outcome
    guard.settle(reserved, prices.cost_usd(result.model, result.usage))
    outcome.records.append(_record(purpose, result.model, prices, result.usage, result.latency_ms))
    return result.output, outcome


def _record(
    purpose: LLMCallPurpose,
    model: str,
    prices: PriceTable,
    usage: TokenUsage,
    latency_ms: int,
    *,
    error: str | None = None,
) -> LLMCallRecord:
    return LLMCallRecord(
        purpose=purpose,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        latency_ms=latency_ms,
        # Failed calls still bill the tokens they used, so they still count.
        cost_usd=prices.cost_usd(model, usage),
        status="error" if error else "success",
        error=error[:500] if error else None,
    )


def _secret_comment(finding: SecretFinding, files: Sequence[FileDiff]) -> ReviewComment:
    file = next(f for f in files if f.path == finding.path)
    code = file.new_side_lines()[finding.line].content  # already redacted
    return ReviewComment(
        path=finding.path,
        line=finding.line,
        start_line=None,
        severity="critical",
        category="security",
        title=f"Possible {finding.kind} committed",
        # Never echo the value: this comment is public on the PR.
        body=(
            f"This line appears to contain a **{finding.kind}**. Anything committed to git "
            "stays in the repository history even after it is deleted, so treat it as exposed: "
            "revoke or rotate it, then load it from an environment variable or a secret manager."
        ),
        suggestion=None,
        confidence=1.0,
        source="secret_scanner",
        # Same fingerprint as an LLM "security" comment on this line, so the two dedupe.
        fingerprint=fingerprint(finding.path, "security", code),
        code_snapshot=code,
    )
