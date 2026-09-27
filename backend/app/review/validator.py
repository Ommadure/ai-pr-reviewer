"""Check every LLM comment against the real diff before it can be posted.

The model's output is a *proposal*. A comment survives only if it points at a
line that exists in this diff and passes the repo's thresholds. Everything
dropped is kept with a reason: that's how we measure hallucination rates and
debug prompts.
"""

from collections.abc import Iterable, Sequence

from app.config.repo_config import RepoConfig
from app.review.fingerprint import fingerprint
from app.review.models import (
    SEVERITY_RANK,
    DroppedComment,
    DropReason,
    FileDiff,
    LLMComment,
    ReviewComment,
)


def validate_llm_comments(
    comments: Iterable[LLMComment], chunk_files: Sequence[FileDiff], config: RepoConfig
) -> tuple[list[ReviewComment], list[DroppedComment]]:
    files = {file.path: file for file in chunk_files}
    kept: list[ReviewComment] = []
    dropped: list[DroppedComment] = []
    for comment in comments:
        result = _validate_one(comment, files, config)
        if isinstance(result, ReviewComment):
            kept.append(result)
        else:
            dropped.append(_dropped(comment, result))
    return kept, dropped


def _validate_one(
    comment: LLMComment, files: dict[str, FileDiff], config: RepoConfig
) -> ReviewComment | DropReason:
    file = files.get(comment.path)
    if file is None:
        return "unknown_path"
    commentable = file.commentable_lines(include_context=config.comment_on_context_lines)
    if comment.line not in commentable:
        return "invalid_line"

    start_line = comment.start_line
    if start_line == comment.line:
        start_line = None  # a one-line "range" is just a line
    if start_line is not None:
        same_hunk = file.hunk_index_of(start_line) == file.hunk_index_of(comment.line)
        if start_line > comment.line or start_line not in commentable or not same_hunk:
            return "invalid_range"

    if comment.confidence < config.min_confidence:
        return "low_confidence"
    if SEVERITY_RANK[comment.severity] < SEVERITY_RANK[config.min_severity]:
        return "below_min_severity"
    if config.focus and comment.category not in config.focus:
        return "out_of_focus"

    lines = file.new_side_lines()
    first = start_line or comment.line
    target = [lines[n] for n in range(first, comment.line + 1) if n in lines]
    code_snapshot = "\n".join(line.content for line in target)

    # A suggestion replaces start_line..line wholesale, so every line in that
    # range must be one this PR added; otherwise we'd rewrite code it didn't touch.
    suggestion = comment.suggestion
    suggestion_dropped = False
    if suggestion is not None:
        covers_added_only = len(target) == comment.line - first + 1 and all(
            line.type == "added" for line in target
        )
        if not covers_added_only:
            suggestion, suggestion_dropped = None, True

    return ReviewComment(
        path=comment.path,
        line=comment.line,
        start_line=start_line,
        severity=comment.severity,
        category=comment.category,
        title=comment.title.strip(),
        body=comment.body.strip(),
        suggestion=suggestion,
        confidence=comment.confidence,
        source="llm",
        fingerprint=fingerprint(comment.path, comment.category, code_snapshot),
        code_snapshot=code_snapshot,
        suggestion_dropped=suggestion_dropped,
    )


def finalize_comments(
    comments: Sequence[ReviewComment],
    *,
    max_comments: int,
    existing_fingerprints: Iterable[str] = (),
) -> tuple[list[ReviewComment], list[DroppedComment]]:
    """Dedupe (within this run and against earlier runs), rank, and cap."""
    already_posted = set(existing_fingerprints)
    seen: set[str] = set()
    unique: list[ReviewComment] = []
    dropped: list[DroppedComment] = []
    # Most important first, so when two comments collide the better one survives.
    for comment in sorted(comments, key=_rank):
        if comment.fingerprint in already_posted:
            dropped.append(_dropped(comment, "already_posted"))
        elif comment.fingerprint in seen:
            dropped.append(_dropped(comment, "duplicate"))
        else:
            seen.add(comment.fingerprint)
            unique.append(comment)
    for comment in unique[max_comments:]:
        dropped.append(_dropped(comment, "over_limit"))
    return unique[:max_comments], dropped


def still_open(
    comments: Sequence[ReviewComment], existing_fingerprints: Iterable[str]
) -> list[ReviewComment]:
    """The comments that repeat one already posted: one per fingerprint, best first.

    A fingerprint covers the target code, so a match means the same problem on the
    same code: it wasn't fixed, it was just reported before.
    """
    already_posted = set(existing_fingerprints)
    found: dict[str, ReviewComment] = {}
    for comment in sorted(comments, key=_rank):
        if comment.fingerprint in already_posted:
            found.setdefault(comment.fingerprint, comment)
    return list(found.values())


def _rank(comment: ReviewComment) -> tuple[int, int, float]:
    # Secret-scanner findings first, then severity, then confidence.
    return (
        0 if comment.source == "secret_scanner" else 1,
        -SEVERITY_RANK[comment.severity],
        -comment.confidence,
    )


def _dropped(comment: LLMComment | ReviewComment, reason: DropReason) -> DroppedComment:
    return DroppedComment(
        path=comment.path,
        line=comment.line,
        title=comment.title,
        severity=comment.severity,
        category=comment.category,
        reason=reason,
    )
