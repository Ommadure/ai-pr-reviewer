"""Check every LLM comment against the real diff before it can be posted.

The model's output is a *proposal*. A comment survives only if it points at a
line that exists in this diff and passes the repo's thresholds. Everything
dropped is kept with a reason: that's how we measure hallucination rates and
debug prompts.
"""

import difflib
import re
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
        suggestion = clean_suggestion(suggestion) if covers_added_only else None
        if suggestion is not None and misfit_reason(
            {n: line.content for n, line in lines.items()}, first, comment.line, suggestion
        ):
            suggestion = None
        suggestion_dropped = suggestion is None

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


# How prompt_builder shows diff lines to the model: "+ 42 | code", "  42 | code",
# "-    | code". Models sometimes copy that view into a suggestion, which GitHub would
# then apply literally: a syntax error in the author's file.
# Added and context lines always carry a line number; removed lines never do. Requiring
# the number keeps real code such as a TypeScript union (`  | "admin"`) from matching.
_DIFF_VIEW_LINE = re.compile(r"^(?:([+ ]) *\d+ |(-) +)\| ?(.*)$")


def clean_suggestion(text: str) -> str | None:
    """The suggested code without copied diff-view markers, or None if it can't be trusted.

    - Every line in the `+ NN | ` view: strip it (the code after `| ` keeps its indentation).
    - Every line starting with `+ ` (the view without its number) or a raw-diff `+`: strip it.
    - A removed (`-`) line, or marked and plain lines mixed: drop it rather than guess.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return text
    views = [_DIFF_VIEW_LINE.match(line) for line in lines]
    if all(views):
        if any(m[2] for m in views if m):  # a removed line: old code, can't be applied
            return None
        return "\n".join(
            m[3] if (m := _DIFF_VIEW_LINE.match(line)) else "" for line in text.splitlines()
        )
    if any(views):
        return None
    # The view's "+ " without the number ("+ def f():"), or a raw diff's "+".
    for marker in ("+ ", "+"):
        if all(line.startswith(marker) for line in lines):
            return "\n".join(line.removeprefix(marker) for line in text.splitlines())
    return text


# Lines too generic to tell whether a suggestion copied them (braces, bare keywords).
_TRIVIAL_LINES = {"", "{", "}", "(", ")", "[", "]", "});", "})", ");", "pass", "return", "else:"}
_SIMILAR = 0.6  # difflib ratio at which a range line counts as edited rather than removed


def misfit_reason(lines: dict[int, str], first: int, last: int, suggestion: str) -> str | None:
    """Why the suggestion wouldn't apply cleanly over lines first..last, or None if it fits.

    GitHub replaces exactly first..last with the suggestion. Models often write a
    replacement for a different span than the one they report: the whole function over
    its `def` line, or one fixed `if` over the `if` and the `raise` under it. Applied,
    that duplicates or silently deletes code (a deleted `raise` can open a security hole).
    """
    new = [line.strip() for line in suggestion.splitlines()]
    old = [lines[n].strip() for n in range(first, last + 1) if n in lines]
    window = len(new) + 1
    nearby = {
        lines[n].strip()
        for n in [*range(first - window, first), *range(last + 1, last + 1 + window)]
        if n in lines
    }
    for line in set(new):
        extra_copies = new.count(line) - old.count(line)
        if _meaningful(line) and extra_copies > 0 and line in nearby:
            return "repeats code outside its range"
    if len(new) < len(old):
        for line in old:
            if _meaningful(line) and not any(_similar(line, n) for n in new):
                return "drops a line from its range"
    # A drop-in fix keeps the block structure: wrapping code in `if (x) { … }` adds a
    # `{` and a `}`, but a replacement that swallows the enclosing function's `}` or
    # `)` changes how many brackets the range leaves open.
    if _bracket_balance(new) != _bracket_balance(old):
        return "changes the bracket structure of its range"
    return None


def _bracket_balance(lines: list[str]) -> int:
    text = "".join(lines)
    return sum(text.count(c) for c in "([{") - sum(text.count(c) for c in ")]}")


def _meaningful(line: str) -> bool:
    return len(line) >= 6 and line not in _TRIVIAL_LINES


def _similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() >= _SIMILAR


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
