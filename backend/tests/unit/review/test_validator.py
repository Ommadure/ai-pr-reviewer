from typing import Any

import pytest

from app.config.repo_config import RepoConfig
from app.review.diff_parser import parse_patch
from app.review.fingerprint import fingerprint
from app.review.models import FileDiff, LLMComment, ReviewComment
from app.review.validator import finalize_comments, validate_llm_comments

# Two hunks. New-file lines: 10 ctx, 11-12 added, 13 ctx | 40 ctx, 41 added
PATCH = (
    "@@ -10,2 +10,4 @@ def f():\n"
    "     a = 1\n"
    "+    b = 2\n"
    "+    c = 3\n"
    "     return a\n"
    "@@ -38,1 +40,2 @@\n"
    "     x = 0\n"
    "+    y = x / 0\n"
)


@pytest.fixture
def files() -> list[FileDiff]:
    return [parse_patch("app/f.py", PATCH)]


def _comment(**overrides: Any) -> LLMComment:
    values: dict[str, Any] = {
        "path": "app/f.py",
        "line": 11,
        "severity": "high",
        "category": "bug",
        "title": "Problem",
        "body": "Explanation",
        "confidence": 0.9,
    }
    return LLMComment(**(values | overrides))


def _validate(
    files: list[FileDiff], comment: LLMComment, **config: Any
) -> tuple[list[ReviewComment], list[str]]:
    kept, dropped = validate_llm_comments([comment], files, RepoConfig(**config))
    return kept, [d.reason for d in dropped]


def test_valid_comment_is_kept_with_snapshot_and_fingerprint(files: list[FileDiff]) -> None:
    [kept], dropped = _validate(files, _comment(line=12, start_line=11))
    assert dropped == []
    assert kept.code_snapshot == "    b = 2\n    c = 3"
    assert kept.fingerprint == fingerprint("app/f.py", "bug", "b = 2\nc = 3")
    assert kept.source == "llm"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"path": "app/other.py"}, "unknown_path"),
        ({"line": 99}, "invalid_line"),  # hallucinated line number
        ({"line": 10}, "invalid_line"),  # context line
        ({"line": 11, "start_line": 12}, "invalid_range"),  # start after end
        ({"line": 41, "start_line": 11}, "invalid_range"),  # crosses hunks
        ({"line": 12, "start_line": 10}, "invalid_range"),  # starts on context
        ({"confidence": 0.3}, "low_confidence"),
        ({"severity": "info"}, "below_min_severity"),
    ],
)
def test_drop_reasons(files: list[FileDiff], overrides: dict[str, Any], reason: str) -> None:
    kept, dropped = _validate(files, _comment(**overrides))
    assert kept == [] and dropped == [reason]


def test_focus_filters_categories(files: list[FileDiff]) -> None:
    _, dropped = _validate(files, _comment(category="style"), focus=["bug", "security"])
    assert dropped == ["out_of_focus"]


def test_context_lines_allowed_when_configured(files: list[FileDiff]) -> None:
    kept, _ = _validate(files, _comment(line=13), comment_on_context_lines=True)
    assert len(kept) == 1


def test_suggestion_covering_unchanged_code_is_dropped_but_comment_kept(
    files: list[FileDiff],
) -> None:
    [kept], _ = _validate(
        files,
        _comment(line=13, start_line=12, suggestion="    c = 4\n    return a"),
        comment_on_context_lines=True,
    )
    assert kept.suggestion is None and kept.suggestion_dropped


def test_suggestion_on_added_lines_is_kept(files: list[FileDiff]) -> None:
    [kept], _ = _validate(files, _comment(line=41, suggestion="    y = x / 1"))
    assert kept.suggestion == "    y = x / 1"


def test_one_line_range_is_normalised(files: list[FileDiff]) -> None:
    [kept], _ = _validate(files, _comment(line=11, start_line=11))
    assert kept.start_line is None


# ---- finalize: dedupe, rank, cap ----


def _review_comment(fp: str, severity: str = "medium", confidence: float = 0.8) -> ReviewComment:
    return ReviewComment(
        path="a.py",
        line=1,
        start_line=None,
        severity=severity,  # type: ignore[arg-type]
        category="bug",
        title=fp,
        body="",
        suggestion=None,
        confidence=confidence,
        source="llm",
        fingerprint=fp,
        code_snapshot="",
    )


def test_finalize_dedupes_ranks_and_caps() -> None:
    comments = [
        _review_comment("low-1", "low"),
        _review_comment("dup", "high", 0.7),
        _review_comment("dup", "high", 0.95),  # the better duplicate should survive
        _review_comment("crit", "critical"),
        _review_comment("posted-before", "high"),
    ]
    kept, dropped = finalize_comments(
        comments, max_comments=2, existing_fingerprints={"posted-before"}
    )
    assert [(c.title, c.confidence) for c in kept] == [("crit", 0.8), ("dup", 0.95)]
    assert sorted((d.title, d.reason) for d in dropped) == [
        ("dup", "duplicate"),
        ("low-1", "over_limit"),
        ("posted-before", "already_posted"),
    ]
