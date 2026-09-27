from typing import Any

import pytest

from app.config.repo_config import RepoConfig
from app.review.diff_parser import parse_patch
from app.review.fingerprint import fingerprint
from app.review.models import FileDiff, LLMComment, ReviewComment
from app.review.validator import (
    clean_suggestion,
    finalize_comments,
    misfit_reason,
    still_open,
    validate_llm_comments,
)

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


def test_still_open_keeps_the_best_repeat_of_each_posted_issue() -> None:
    comments = [
        _review_comment("posted", "medium", 0.6),
        _review_comment("posted", "high", 0.9),  # the same issue, found twice: keep the best
        _review_comment("new", "critical"),
    ]
    assert [(c.title, c.severity) for c in still_open(comments, {"posted", "gone"})] == [
        ("posted", "high")
    ]
    assert still_open(comments, ()) == []


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # Copied from the model's view of the diff (seen in real eval runs).
        ("+ 16 |     await session.commit()", "    await session.commit()"),
        ("  7 |   for (let i = 0; i < n; i++) {", "  for (let i = 0; i < n; i++) {"),
        ("+ 9 | a = 1\n+ 10 |     b = 2", "a = 1\n    b = 2"),
        ("+     await session.commit()", "    await session.commit()"),  # "+ " without number
        ("+ def f():\n+     return 1", "def f():\n    return 1"),
        ("+def f():\n+    return 1", "def f():\n    return 1"),  # a raw diff
        # Real code is left alone, including lines that merely contain a pipe.
        ("    await session.commit()", "    await session.commit()"),
        ('type Role =\n  | "admin"\n  | "member";', 'type Role =\n  | "admin"\n  | "member";'),
        ("x = a | b", "x = a | b"),
        # Can't be trusted: removed (old) code, or marked and plain lines mixed.
        ("-    | old_call()\n+ 5 | new_call()", None),
        ("+ 5 | new_call()\nother_call()", None),
    ],
)
def test_suggestions_lose_copied_diff_markers(given: str, expected: str | None) -> None:
    assert clean_suggestion(given) == expected


# Real suggestions from eval runs, applied over the lines they claim to replace.
TOKEN = {
    10: "def verify(token_id: str):",
    11: "    token = load_token(token_id)",
    12: "    if token.expires_at > datetime.now(UTC):",
    13: "        raise TokenExpired(token_id)",
    14: "    return token.user_id",
}
FILTERS = {
    10: "def add_filter(field: str, value: str, filters: list[Filter] = []) -> list[Filter]:",
    11: '    """Append a filter and return the list, for building queries step by step."""',
    12: "    filters.append(Filter(field, value))",
    13: "    return filters",
}
BATCH = {
    10: "    sent = 0",
    11: "    for i in range(len(messages) - 1):",
    12: '        send_email(messages[i]["to"], messages[i]["body"])',
    13: "        sent += 1",
    14: "    return sent",
}
FIXED_DEF = (
    "def add_filter(field: str, value: str, filters: list[Filter] | None = None) -> list[Filter]:"
)
FIXED_SORT = "  return points.sort((a, b) => b - a).slice(0, 3);"
TOP = {
    3: "export function topThree(scores: Score[]): number[] {",
    4: "  const points = scores.map((s) => s.points);",
    5: "  return points.sort().reverse().slice(0, 3);",
    6: "}",
}


@pytest.mark.parametrize(
    ("lines", "first", "last", "suggestion", "reason"),
    [
        # The fixed `if` replaces the `if` and the `raise`: expired tokens would pass.
        (TOKEN, 12, 13, "    if token.expires_at < datetime.now(UTC):", "drops a line"),
        # The whole function over its `def` line: the old body stays underneath.
        (FILTERS, 10, 10, "\n".join([
            FIXED_DEF, FILTERS[11], "    if filters is None:", "        filters = []",
            FILTERS[12], FILTERS[13],
        ]), "repeats code"),
        # The loop and its body over the `for` line: every email would be sent twice.
        (BATCH, 11, 11, "\n".join(
            ["    for i in range(len(messages)):", BATCH[12], BATCH[13]]
        ), "repeats code"),
        # The function with its closing brace over lines 3-5: two `}` in a row.
        (TOP, 3, 5, "\n".join([TOP[3], TOP[4], FIXED_SORT, "}"]), "bracket structure"),
    ],
)  # fmt: skip
def test_suggestions_that_do_not_fit_their_range_are_caught(
    lines: dict[int, str], first: int, last: int, suggestion: str, reason: str
) -> None:
    assert reason in (misfit_reason(lines, first, last, suggestion) or "")


@pytest.mark.parametrize(
    ("lines", "first", "last", "suggestion"),
    [
        (TOKEN, 12, 13, "    if token.expires_at < datetime.now(UTC):\n" + TOKEN[13]),
        (TOKEN, 12, 12, "    if token.expires_at < datetime.now(UTC):"),
        (BATCH, 11, 11, "    for i in range(len(messages)):"),
        (TOP, 5, 5, FIXED_SORT),
        (TOP, 3, 5, "\n".join([TOP[3], TOP[4], FIXED_SORT])),
        # Wrapping a line in a new block adds a `{` and a `}`: the structure is kept.
        (TOP, 5, 5, "  if (points.length) {\n  " + FIXED_SORT + "\n  }"),
        # Replacing the `def` line only, with the rest of the fix left to the body.
        (FILTERS, 10, 10, FIXED_DEF),
    ],
)  # fmt: skip
def test_suggestions_that_fit_their_range_are_kept(
    lines: dict[int, str], first: int, last: int, suggestion: str
) -> None:
    assert misfit_reason(lines, first, last, suggestion) is None
