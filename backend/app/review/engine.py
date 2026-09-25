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
from app.review.validator import finalize_comments, validate_llm_comments

TEMPERATURE = 0.1  # low: we want consistent, conservative reviews, not creativity


@dataclass
class _CallOutcome:
    records: list[LLMCallRecord] = field(default_factory=list)
    error: str | None = None


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

    async def review_chunk(chunk: Chunk) -> _ChunkOutcome:
        messages = prompt_builder.build_review_messages(
            chunk.files, pr, config, version=prompt_version
        )
        async with semaphore:
            output, call = await _structured_call(
                llm, messages, FileReviewOutput, model=model, purpose="file_review", prices=prices
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
            skipped += [SkippedFile(path, "llm_error") for path in paths]
            continue
        reviewed_paths.update(paths)
        kept, rejected = validate_llm_comments(outcome.output.comments, outcome.chunk.files, config)
        llm_comments += kept
        dropped += rejected
        for file_summary in outcome.output.file_summaries:
            if file_summary.path in paths:  # ignore summaries of files not in this chunk
                summaries.setdefault(file_summary.path, []).append(file_summary.summary)

    comments, rejected = finalize_comments(
        [*secret_comments, *llm_comments],
        max_comments=config.max_comments,
        existing_fingerprints=existing_fingerprints,
    )
    dropped += rejected

    # Reduce: one small call turns per-file notes into a PR-level summary.
    summary: PRSummaryOutput | None = None
    if summarize and reviewed_paths:
        file_summaries = [FileSummary(path=p, summary=" ".join(s)) for p, s in summaries.items()]
        issues = [
            f"[{c.severity}] {c.path}:{c.line} {c.title}"
            for c in sorted(comments, key=lambda c: -SEVERITY_RANK[c.severity])
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
    )


async def _structured_call[T: BaseModel](
    llm: LLMProvider,
    messages: list[Message],
    schema: type[T],
    *,
    model: str,
    purpose: LLMCallPurpose,
    prices: PriceTable,
) -> tuple[T | None, _CallOutcome]:
    """One call, plus exactly one repair attempt if the output is invalid."""
    outcome = _CallOutcome()
    try:
        result = await llm.generate_structured(
            messages, schema, model=model, temperature=TEMPERATURE
        )
    except LLMInvalidOutput as invalid:
        outcome.records.append(
            _record(
                purpose, model, prices, invalid.usage, invalid.latency_ms, error="invalid_output"
            )
        )
        repair = prompt_builder.build_repair_messages(messages, invalid.raw_text, invalid.problem)
        try:
            result = await llm.generate_structured(
                repair, schema, model=model, temperature=TEMPERATURE
            )
        except LLMError as exc:
            outcome.records.append(
                _record("repair", model, prices, exc.usage, exc.latency_ms, error=str(exc))
            )
            outcome.error = f"{purpose}: output still invalid after repair ({exc})"
            return None, outcome
        outcome.records.append(
            _record("repair", result.model, prices, result.usage, result.latency_ms)
        )
        return result.output, outcome
    except LLMError as exc:
        outcome.records.append(
            _record(purpose, model, prices, exc.usage, exc.latency_ms, error=str(exc))
        )
        outcome.error = f"{purpose}: {exc}"
        return None, outcome
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
