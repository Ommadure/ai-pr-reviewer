"""Incremental reviews: only what changed since the last review (ADR 0011).

Two pure helpers the orchestrator uses on `compare(last_reviewed_sha...head)`:

1. restrict_to_pr_diff: the compare diff can contain changes that are NOT part of
   the pull request, e.g. when the author merges `main` into their branch. GitHub
   only accepts review comments on lines in the PR's own diff (base...head), so an
   added line is only commentable if the PR diff also shows it as added. Other
   added lines are kept as context: the model still sees them, but can't comment.

2. detect_status_changes: comments whose flagged code was modified or removed by
   the new commits become "addressed"; comments on deleted files become "outdated".
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from app.review.models import DiffLine, FileDiff, SkippedFile

CommentStatus = Literal["addressed", "outdated"]


def restrict_to_pr_diff(
    incremental: Sequence[FileDiff], pr_files: Sequence[FileDiff]
) -> tuple[list[FileDiff], list[SkippedFile]]:
    pr_by_path = {file.path: file for file in pr_files}
    kept: list[FileDiff] = []
    skipped: list[SkippedFile] = []
    for file in incremental:
        pr_file = pr_by_path.get(file.path)
        if pr_file is None:
            # Changed between the two reviews, but not by this PR (e.g. merged from main).
            skipped.append(SkippedFile(file.path, "not_in_pr_diff"))
            continue
        allowed = pr_file.commentable_lines()
        hunks = tuple(
            replace(hunk, lines=tuple(_limit(line, allowed) for line in hunk.lines))
            for hunk in file.hunks
        )
        kept.append(replace(file, hunks=hunks))
    return kept, skipped


def _limit(line: DiffLine, allowed: set[int]) -> DiffLine:
    if line.type == "added" and line.new_line not in allowed:
        return replace(line, type="context")
    return line


@dataclass(frozen=True)
class OpenComment:
    id: int  # our review_comments.id
    path: str
    line: int
    start_line: int | None
    code_snapshot: str
    posted_at_sha: str  # head of the run that posted it


def detect_status_changes(
    comments: Sequence[OpenComment], incremental: Sequence[FileDiff], *, base_sha: str
) -> dict[int, CommentStatus]:
    """Which open comments the commits in `incremental` (base_sha...head) resolved.

    Precise when the comment was posted on base_sha itself: its line numbers are then
    the compare diff's *old* line numbers, so we check whether any of those lines was
    removed or rewritten. For comments posted on older commits the numbers may have
    shifted, so we fall back to matching the removed lines' content.
    """
    by_path: dict[str, FileDiff] = {}
    for changed in incremental:
        by_path[changed.path] = changed
        if changed.previous_path:
            by_path.setdefault(changed.previous_path, changed)

    changes: dict[int, CommentStatus] = {}
    for comment in comments:
        file = by_path.get(comment.path)
        if file is None:
            continue  # the new commits didn't touch this file
        if file.status == "removed":
            changes[comment.id] = "outdated"
            continue
        removed = [line for hunk in file.hunks for line in hunk.lines if line.type == "removed"]
        if comment.posted_at_sha == base_sha:
            first = comment.start_line or comment.line
            hit = any(
                line.old_line is not None and first <= line.old_line <= comment.line
                for line in removed
            )
        else:
            snapshot = {_norm(text) for text in comment.code_snapshot.splitlines() if text.strip()}
            hit = bool(snapshot) and any(_norm(line.content) in snapshot for line in removed)
        if hit:
            changes[comment.id] = "addressed"
        elif file.path != comment.path:
            changes[comment.id] = "outdated"  # file renamed away; the comment no longer applies
    return changes


def _norm(text: str) -> str:
    return " ".join(text.split())
