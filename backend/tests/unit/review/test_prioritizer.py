from app.review.diff_parser import parse_patch
from app.review.models import FileDiff
from app.review.prioritizer import limit_files, prioritize


def _file(path: str, added_lines: int = 1) -> FileDiff:
    body = "\n".join(f"+x{i} = {i}" for i in range(added_lines))
    return parse_patch(path, f"@@ -0,0 +1,{added_lines} @@\n{body}")


def test_sensitive_paths_first_tests_and_docs_last_bigger_changes_first() -> None:
    files = [
        _file("README.md"),
        _file("tests/test_users.py", 50),
        _file("app/utils.py", 2),
        _file("app/helpers.py", 9),
        _file("app/auth/login.py"),
        _file("migrations/0002_add_index.sql"),
    ]
    assert [f.path for f in prioritize(files)] == [
        "app/auth/login.py",
        "migrations/0002_add_index.sql",
        "app/helpers.py",
        "app/utils.py",
        "tests/test_users.py",
        "README.md",
    ]


def test_limit_skips_the_least_risky_files() -> None:
    kept, skipped = limit_files([_file("docs/a.md"), _file("app/payment.py"), _file("app/x.py")], 2)
    assert [f.path for f in kept] == ["app/payment.py", "app/x.py"]
    assert [(s.path, s.reason) for s in skipped] == [("docs/a.md", "over_file_limit")]
