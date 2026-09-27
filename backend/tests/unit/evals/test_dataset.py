"""Case loading, validation, and the shipped dataset itself."""

from pathlib import Path

import pytest

from app.evals.cli import CASES_DIR
from app.evals.dataset import CaseError, PlantedBug, load_case, load_cases

PATCH = """\
diff --git a/app/x.py b/app/x.py
new file mode 100644
--- /dev/null
+++ b/app/x.py
@@ -0,0 +1,3 @@
+def f(a=[]):
+    a.append(1)
+    return a
"""


def write_case(root: Path, case_id: str, yaml_body: str, patch: str = PATCH) -> Path:
    directory = root / case_id
    directory.mkdir()
    (directory / "case.yaml").write_text(
        f"id: {case_id}\nlanguage: python\ndescription: d\n{yaml_body}"
    )
    (directory / "diff.patch").write_text(patch)
    return directory


def planted(
    path: str = "app/x.py", lines: str = "[1, 1]", category: str = "bug", extra: str = ""
) -> str:
    return (
        f"planted_bugs:\n  - {{path: {path}, lines: {lines}, category: {category}, "
        f"severity: medium, description: m{extra}}}\n"
    )


def test_valid_case_loads(tmp_path: Path) -> None:
    body = planted()
    case = load_case(write_case(tmp_path, "py-mutable-01", body))
    assert case.kind == "bug"
    assert case.spec.planted_bugs[0].start == 1
    assert case.files[0].path == "app/x.py"


def test_case_without_bugs_is_clean(tmp_path: Path) -> None:
    assert load_case(write_case(tmp_path, "clean-01", "")).kind == "clean"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            planted(path="app/y.py"),
            "not in the diff",
        ),
        (
            planted(lines="[9, 9]"),
            "not an added line",
        ),
        (
            planted(lines="[3, 1]"),
            "start <= end",
        ),
        (
            planted(category="vibes"),
            "category",
        ),
        (
            planted(extra=", bad_suggestions: ['with (']"),
            "bad_suggestions pattern",
        ),
    ],
)
def test_bad_cases_fail_loudly(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(CaseError, match=message):
        load_case(write_case(tmp_path, "bad-01", body))


def test_directory_name_must_match_id(tmp_path: Path) -> None:
    directory = write_case(tmp_path, "one", "")
    (directory / "case.yaml").write_text("id: two\nlanguage: python\ndescription: d\n")
    with pytest.raises(CaseError, match="must match"):
        load_case(directory)


def test_pattern_filters_and_empty_selection_is_an_error(tmp_path: Path) -> None:
    write_case(tmp_path, "py-a", "")
    write_case(tmp_path, "ts-b", "")
    assert [c.id for c in load_cases(tmp_path, "py-*")] == ["py-a"]
    with pytest.raises(CaseError, match="no cases"):
        load_cases(tmp_path, "go-*")


def test_the_shipped_dataset_is_valid_and_complete() -> None:
    # Every case parses and every planted bug sits on an added line (load_case checks).
    cases = load_cases(CASES_DIR)
    assert sum(c.kind == "bug" for c in cases) >= 25
    assert sum(c.kind == "clean" for c in cases) >= 5
    assert {c.spec.language for c in cases} >= {"python", "typescript", "javascript", "sql"}


def test_bad_fix_names_the_matching_pattern() -> None:
    bug = PlantedBug(
        path="a.py",
        lines=(1, 1),
        category="bug",
        severity="medium",
        description="leak",
        bad_suggestions=[r"\bwith\s+sqlite3\.connect\(", "never"],
    )
    assert bug.bad_fix("with sqlite3.connect(p) as c:") == r"\bwith\s+sqlite3\.connect\("
    assert bug.bad_fix("with closing(sqlite3.connect(p)) as c:") is None
    assert bug.bad_fix(None) is None
