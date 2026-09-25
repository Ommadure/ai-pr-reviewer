from app.review.diff_parser import parse_patch
from app.review.incremental import OpenComment, detect_status_changes, restrict_to_pr_diff

BASE = "b" * 40


def test_only_lines_in_the_pr_diff_stay_commentable() -> None:
    # Since the last review the author added line 3 AND merged main, which added line 10.
    incremental = parse_patch(
        "app.py", "@@ -1,2 +1,3 @@\n a\n b\n+c\n@@ -8,1 +9,2 @@\n x\n+from_main\n"
    )
    pr_diff = parse_patch("app.py", "@@ -0,0 +1,3 @@\n+a\n+b\n+c\n")  # the PR only added 1-3

    [restricted], skipped = restrict_to_pr_diff([incremental], [pr_diff])

    assert skipped == []
    assert restricted.commentable_lines() == {3}
    # line 10 is still shown to the model, just not commentable
    assert 10 in restricted.commentable_lines(include_context=True)


def test_files_changed_only_by_a_merge_from_main_are_skipped() -> None:
    incremental = [parse_patch("main_only.py", "@@ -1 +1,2 @@\n x\n+y\n")]
    kept, skipped = restrict_to_pr_diff(incremental, [])
    assert kept == []
    assert [(s.path, s.reason) for s in skipped] == [("main_only.py", "not_in_pr_diff")]


def _comment(
    cid: int,
    line: int,
    snapshot: str,
    *,
    start: int | None = None,
    sha: str = BASE,
    path: str = "app.py",
) -> OpenComment:
    return OpenComment(cid, path, line, start, snapshot, sha)


def test_rewriting_the_flagged_line_addresses_the_comment() -> None:
    # Posted on BASE at line 12; the new commit rewrites line 12.
    diff = parse_patch("app.py", "@@ -11,3 +11,3 @@\n a\n-    q = f'{x}'\n+    q = '%s'\n c\n")
    comments = [_comment(1, 12, "    q = f'{x}'"), _comment(2, 20, "untouched()")]
    assert detect_status_changes(comments, [diff], base_sha=BASE) == {1: "addressed"}


def test_multi_line_range_is_addressed_when_any_line_changes() -> None:
    diff = parse_patch("app.py", "@@ -5,3 +5,2 @@\n a\n-b\n c\n")  # removes old line 6
    comment = _comment(1, 7, "a\nb\nc", start=5)
    assert detect_status_changes([comment], [diff], base_sha=BASE) == {1: "addressed"}


def test_older_comments_fall_back_to_content_matching() -> None:
    # Posted two pushes ago: line numbers may have shifted, so match by content.
    patch = "@@ -40,2 +40,2 @@\n ctx\n-  password = 'hunter2'\n+  password = env()\n"
    diff = parse_patch("app.py", patch)
    old = "0" * 40
    comments = [
        _comment(1, 3, "password   =   'hunter2'", sha=old),  # whitespace differs: still a match
        _comment(2, 41, "something else", sha=old),
    ]
    assert detect_status_changes(comments, [diff], base_sha=BASE) == {1: "addressed"}


def test_deleted_and_renamed_files() -> None:
    deleted = parse_patch("old.py", "@@ -1 +0,0 @@\n-x\n", status="removed")
    renamed = parse_patch("new_name.py", None, status="renamed", previous_path="moved.py")
    comments = [_comment(1, 1, "x", path="old.py"), _comment(2, 4, "y", path="moved.py")]
    assert detect_status_changes(comments, [deleted, renamed], base_sha=BASE) == {
        1: "outdated",
        2: "outdated",
    }


def test_untouched_files_change_nothing() -> None:
    diff = parse_patch("other.py", "@@ -1 +1 @@\n-a\n+b\n")
    assert detect_status_changes([_comment(1, 1, "a")], [diff], base_sha=BASE) == {}
