"""Group files into LLM calls that fit the token budget.

Two limits (both configurable):
- per call  (REVIEW_MAX_CHUNK_TOKENS): keeps each request well inside the
  model's context window, where attention is still good;
- per run   (REVIEW_MAX_INPUT_TOKENS): caps what one PR can cost.

Files arrive already prioritised, so when the run budget runs out it's the
least risky files that get skipped. A file too big for one call is split
between hunks. A hunk is never cut in half: the model needs it whole.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.review.models import FileDiff, Hunk, SkippedFile
from app.review.prompt_builder import render_file
from app.review.tokens import estimate_tokens


@dataclass(frozen=True)
class Chunk:
    files: tuple[FileDiff, ...]
    estimated_tokens: int  # diff only; prompt overhead is accounted separately


def build_chunks(
    files: Sequence[FileDiff],
    *,
    max_chunk_tokens: int,
    max_run_tokens: int,
    overhead_tokens: int,
) -> tuple[list[Chunk], list[SkippedFile]]:
    """overhead_tokens: fixed per-call cost (system prompt, template, PR metadata)."""
    capacity = max_chunk_tokens - overhead_tokens
    if capacity <= 0:
        raise ValueError("REVIEW_MAX_CHUNK_TOKENS is smaller than the prompt itself")

    units: list[tuple[FileDiff, int]] = []
    skipped: list[SkippedFile] = []
    for file in files:
        parts, oversized = _split_file(file, capacity)
        units.extend(parts)
        skipped.extend(oversized)

    chunks: list[Chunk] = []
    current: list[FileDiff] = []
    current_tokens = 0
    run_tokens = 0
    for part, tokens in units:
        fits_current = bool(current) and current_tokens + tokens <= capacity
        cost = tokens if fits_current else tokens + overhead_tokens
        if run_tokens + cost > max_run_tokens:
            skipped.append(SkippedFile(part.path, "over_token_budget"))
            continue
        if not fits_current and current:
            chunks.append(Chunk(tuple(current), current_tokens))
            current, current_tokens = [], 0
        current.append(part)
        current_tokens += tokens
        run_tokens += cost
    if current:
        chunks.append(Chunk(tuple(current), current_tokens))
    return chunks, skipped


def _split_file(
    file: FileDiff, capacity: int
) -> tuple[list[tuple[FileDiff, int]], list[SkippedFile]]:
    whole = estimate_tokens(render_file(file))
    if whole <= capacity:
        return [(file, whole)], []

    parts: list[tuple[FileDiff, int]] = []
    skipped: list[SkippedFile] = []
    group: list[Hunk] = []
    for hunk in file.hunks:
        if _cost(file, [hunk]) > capacity:
            last = hunk.new_start + max(hunk.new_len - 1, 0)
            skipped.append(
                SkippedFile(file.path, f"hunk_too_large (lines {hunk.new_start}-{last})")
            )
            continue
        if group and _cost(file, [*group, hunk]) > capacity:
            parts.append(_part(file, group))
            group = []
        group.append(hunk)
    if group:
        parts.append(_part(file, group))
    return parts, skipped


def _part(file: FileDiff, hunks: list[Hunk]) -> tuple[FileDiff, int]:
    return (replace(file, hunks=tuple(hunks)), _cost(file, hunks))


def _cost(file: FileDiff, hunks: list[Hunk]) -> int:
    return estimate_tokens(render_file(replace(file, hunks=tuple(hunks))))
