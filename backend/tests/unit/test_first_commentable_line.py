from app.github.client import PullRequestFile
from app.services.orchestrator import first_commentable_line


def _file(name: str, patch: str | None, status: str = "modified") -> PullRequestFile:
    return PullRequestFile(filename=name, status=status, additions=1, deletions=0, patch=patch)


def test_new_file_first_line() -> None:
    files = [_file("new.py", "@@ -0,0 +1,2 @@\n+import os\n+print(os)", status="added")]
    assert first_commentable_line(files) == ("new.py", 1)


def test_counts_context_lines_but_not_removed_lines() -> None:
    patch = "@@ -10,4 +10,4 @@ def f():\n     a = 1\n     b = 2\n-    c = 3\n+    c = 4\n"
    assert first_commentable_line([_file("m.py", patch)]) == ("m.py", 12)


def test_uses_the_hunk_that_contains_the_first_addition() -> None:
    patch = "@@ -1,2 +1,1 @@\n-gone\n keep\n@@ -40,2 +39,3 @@\n ctx\n+added\n ctx"
    assert first_commentable_line([_file("m.py", patch)]) == ("m.py", 40)


def test_skips_files_without_usable_additions() -> None:
    files = [
        _file("deleted.py", "@@ -1,1 +0,0 @@\n-x", status="removed"),
        _file("image.png", None),  # binary: GitHub sends no patch
        _file("only_removals.py", "@@ -1,2 +1,1 @@\n-x\n y"),
        _file("target.py", "@@ -1,1 +1,2 @@\n y\n+z\n\\ No newline at end of file"),
    ]
    assert first_commentable_line(files) == ("target.py", 2)


def test_none_when_nothing_to_comment_on() -> None:
    assert first_commentable_line([_file("a.py", None)]) is None
    assert first_commentable_line([]) is None
