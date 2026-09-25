"""Eval cases: `evals/cases/<id>/case.yaml` + `diff.patch`.

    id: py-sql-injection-01
    language: python
    description: "User lookup built with f-string"
    title: "Add user lookup"            # optional: PR title shown to the model
    planted_bugs:
      - path: app/users.py
        lines: [14, 14]
        category: security
        severity: high
        description: "SQL injection"
        accept_categories: [bug]        # optional, for genuinely ambiguous bugs

A case with no planted bugs is a *clean* case: every comment on it is a false positive.
Loading validates each case against its own diff, so a typo in a line number fails
loudly instead of silently lowering recall.
"""

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.review.diff_parser import parse_unified_diff
from app.review.models import Category, FileDiff, Severity


class CaseError(Exception):
    """A case file is malformed or disagrees with its diff."""


class PlantedBug(BaseModel):
    path: str
    lines: tuple[int, int]
    category: Category
    severity: Severity
    description: str
    accept_categories: list[Category] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> "PlantedBug":
        if self.lines[0] > self.lines[1]:
            raise ValueError(f"lines {list(self.lines)} must be [start, end] with start <= end")
        return self

    @property
    def start(self) -> int:
        return self.lines[0]

    @property
    def end(self) -> int:
        return self.lines[1]


class CaseSpec(BaseModel):
    id: str
    language: str
    description: str
    title: str = ""
    pr_description: str = ""
    planted_bugs: list[PlantedBug] = Field(default_factory=list)


@dataclass(frozen=True)
class EvalCase:
    spec: CaseSpec
    files: list[FileDiff]

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def kind(self) -> Literal["bug", "clean"]:
        return "bug" if self.spec.planted_bugs else "clean"


def load_case(directory: Path) -> EvalCase:
    try:
        raw = yaml.safe_load((directory / "case.yaml").read_text(encoding="utf-8"))
        spec = CaseSpec.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CaseError(f"{directory.name}: {exc}") from exc
    if spec.id != directory.name:
        raise CaseError(f"{directory.name}: id {spec.id!r} must match the directory name")
    try:
        diff = (directory / "diff.patch").read_text(encoding="utf-8")
    except OSError as exc:
        raise CaseError(f"{spec.id}: {exc}") from exc
    files = parse_unified_diff(diff)
    if not files:
        raise CaseError(f"{spec.id}: diff.patch has no file changes")
    _check_bugs_are_on_added_lines(spec, files)
    return EvalCase(spec, files)


def load_cases(root: Path, pattern: str = "*") -> list[EvalCase]:
    """Every case under `root` whose id matches the glob `pattern`, sorted by id."""
    directories = sorted(p for p in root.iterdir() if (p / "case.yaml").is_file())
    cases = [load_case(d) for d in directories if fnmatch.fnmatch(d.name, pattern)]
    if not cases:
        raise CaseError(f"no cases in {root} match {pattern!r}")
    return cases


def _check_bugs_are_on_added_lines(spec: CaseSpec, files: list[FileDiff]) -> None:
    # A planted bug must sit on lines the PR adds: those are the only lines the
    # reviewer is allowed to comment on, so anything else could never be found.
    by_path = {f.path: f for f in files}
    for bug in spec.planted_bugs:
        file = by_path.get(bug.path)
        if file is None:
            raise CaseError(f"{spec.id}: planted bug path {bug.path!r} is not in the diff")
        added = {
            line.new_line
            for hunk in file.hunks
            for line in hunk.lines
            if line.type == "added" and line.new_line is not None
        }
        if bug.end not in added:
            raise CaseError(
                f"{spec.id}: planted bug {bug.path}:{bug.end} is not an added line in the diff"
            )
