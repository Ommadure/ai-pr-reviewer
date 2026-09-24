from pathlib import Path

import pytest

from app.review.diff_parser import parse_hunks, parse_patch, parse_unified_diff
from app.review.models import FileDiff

DIFFS = Path(__file__).parents[2] / "fixtures" / "diffs"


@pytest.fixture(scope="module")
def sample() -> dict[str, FileDiff]:
    files = parse_unified_diff((DIFFS / "sample.patch").read_text())
    return {file.path: file for file in files}


def test_finds_every_file_with_its_status(sample: dict[str, FileDiff]) -> None:
    assert {path: file.status for path, file in sample.items()} == {
        "app/users.py": "modified",
        "web/src/api.ts": "added",
        "docs/guide.md": "renamed",
        "scripts/cleanup.sql": "removed",
        "web/public/logo.png": "added",
        "web/package-lock.json": "modified",
    }


def test_modified_file_line_numbers_across_hunks(sample: dict[str, FileDiff]) -> None:
    users = sample["app/users.py"]
    assert len(users.hunks) == 2
    assert users.hunks[1].section == "def get_user(user_id):"
    added = {
        line.new_line: line.content.strip()
        for hunk in users.hunks
        for line in hunk.lines
        if line.type == "added"
    }
    assert added == {
        2: "import os",
        12: 'query = f"SELECT * FROM users WHERE id = {user_id}"',
        13: "row = db.execute(query).fetchone()",
        14: "return row",
    }
    assert users.commentable_lines() == {2, 12, 13, 14}
    assert (users.additions, users.deletions) == (4, 2)


def test_context_lines_are_commentable_only_when_asked(sample: dict[str, FileDiff]) -> None:
    users = sample["app/users.py"]
    with_context = users.commentable_lines(include_context=True)
    assert {1, 11} <= with_context  # "import sqlite3", "db = get_db()"
    assert 1 not in users.commentable_lines()


def test_removed_lines_have_only_old_numbers(sample: dict[str, FileDiff]) -> None:
    removed = [line for line in sample["app/users.py"].hunks[1].lines if line.type == "removed"]
    assert [(line.old_line, line.new_line) for line in removed] == [(11, None), (12, None)]


def test_new_file(sample: dict[str, FileDiff]) -> None:
    api = sample["web/src/api.ts"]
    assert api.commentable_lines() == {1, 2, 3, 4, 5, 6}
    assert api.hunks[0].lines[1].content == "  const res = fetch(`/api/users/${id}`);"


def test_pure_rename_has_previous_path_and_no_hunks(sample: dict[str, FileDiff]) -> None:
    guide = sample["docs/guide.md"]
    assert guide.previous_path == "docs/old-guide.md"
    assert guide.hunks == ()


def test_removed_sql_comment_is_not_mistaken_for_a_file_header(
    sample: dict[str, FileDiff],
) -> None:
    # "--- remove stale sessions" is the line "-- remove..." being removed.
    cleanup = sample["scripts/cleanup.sql"]
    assert [line.content for line in cleanup.hunks[0].lines] == [
        "-- remove stale sessions",
        "DELETE FROM sessions",
        "WHERE expires_at < now();",
    ]
    assert cleanup.commentable_lines() == set()


def test_binary_file(sample: dict[str, FileDiff]) -> None:
    logo = sample["web/public/logo.png"]
    assert logo.is_binary and logo.hunks == ()


def test_no_newline_marker_is_ignored() -> None:
    patch = (
        "@@ -1,2 +1,2 @@\n a\n-b\n\\ No newline at end of file\n+c\n\\ No newline at end of file"
    )
    [hunk] = parse_hunks(patch)
    assert [(line.type, line.content) for line in hunk.lines] == [
        ("context", "a"),
        ("removed", "b"),
        ("added", "c"),
    ]
    assert hunk.lines[2].new_line == 2


def test_crlf_line_endings() -> None:
    [hunk] = parse_hunks("@@ -1,1 +1,2 @@\r\n keep\r\n+new\r\n")
    assert [line.content for line in hunk.lines] == ["keep", "new"]


def test_hunk_header_without_counts_means_one_line() -> None:
    [hunk] = parse_hunks("@@ -3 +3 @@\n-old\n+new")
    assert (hunk.old_len, hunk.new_len) == (1, 1)
    assert hunk.lines[1].new_line == 3


def test_blank_context_line_without_leading_space() -> None:
    # Some tools strip trailing whitespace, turning " " into "".
    [hunk] = parse_hunks("@@ -1,3 +1,4 @@\n a\n\n+b\n c")
    assert [(line.type, line.new_line) for line in hunk.lines] == [
        ("context", 1),
        ("context", 2),
        ("added", 3),
        ("context", 4),
    ]


def test_github_patch_field_and_missing_patch() -> None:
    parsed = parse_patch("x.py", "@@ -0,0 +1 @@\n+print(1)", status="added")
    assert parsed.commentable_lines() == {1}
    missing = parse_patch("huge.py", None)
    assert missing.patch_missing and missing.hunks == ()


def test_empty_inputs() -> None:
    assert parse_hunks("") == ()
    assert parse_unified_diff("") == []
