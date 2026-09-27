"""The matching rule: when does a review comment count as finding a planted bug? (ADR 0013)

A comment matches a bug when all three hold:
  1. same file path;
  2. the comment's line is within the bug's lines ± LINE_TOLERANCE, or the comment's
     range (start_line..line) overlaps the bug's lines;
  3. compatible category: equal, listed in the bug's `accept_categories`, or both in an
     equivalence group (bug ≡ error_handling: a swallowed exception is fairly called either).

Matching is one-to-one. Each bug can be found once and each comment can find one bug;
closest pairs are matched first. Every comment left over is a false positive, tagged
with why, so failures can be read and the prompt improved:
  duplicate       near a bug that another comment already found (noise for the reader)
  wrong_category  near an unfound bug, but filed under an incompatible category
  unplanted       not near any planted bug (on a clean case, every comment is this)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.evals.dataset import PlantedBug
from app.review.models import ReviewComment

LINE_TOLERANCE = 2
EQUIVALENT_CATEGORIES: tuple[frozenset[str], ...] = (frozenset({"bug", "error_handling"}),)

FalsePositiveReason = Literal["duplicate", "wrong_category", "unplanted"]


@dataclass(frozen=True)
class Prediction:
    """The parts of a posted comment the rule looks at (plus title, for reports)."""

    path: str
    line: int
    start_line: int | None
    category: str
    severity: str
    title: str
    source: str = "llm"
    suggestion: str | None = None  # the ```suggestion block, scored by bad_suggestions
    # The model wrote a suggestion the validator had to drop (it wouldn't apply cleanly).
    suggestion_dropped: bool = False

    @classmethod
    def from_comment(cls, comment: ReviewComment) -> "Prediction":
        return cls(
            path=comment.path,
            line=comment.line,
            start_line=comment.start_line,
            category=comment.category,
            severity=comment.severity,
            title=comment.title,
            source=comment.source,
            suggestion=comment.suggestion,
            suggestion_dropped=comment.suggestion_dropped,
        )


@dataclass(frozen=True)
class CaseMatch:
    matched: list[tuple[int, int]]  # (prediction index, bug index)
    false_positives: list[tuple[int, FalsePositiveReason]]
    missed: list[int]  # bug indexes nobody found

    @property
    def tp(self) -> int:
        return len(self.matched)

    @property
    def fp(self) -> int:
        return len(self.false_positives)

    @property
    def fn(self) -> int:
        return len(self.missed)


def categories_compatible(predicted: str, bug: PlantedBug) -> bool:
    if predicted == bug.category or predicted in bug.accept_categories:
        return True
    return any({predicted, bug.category} <= group for group in EQUIVALENT_CATEGORIES)


def line_distance(prediction: Prediction, bug: PlantedBug) -> int | None:
    """0 if the comment touches the bug's lines, else how far off; None if too far."""
    if prediction.path != bug.path:
        return None
    first = prediction.start_line or prediction.line
    if first <= bug.end and prediction.line >= bug.start:
        return 0  # the comment's range overlaps the bug
    if prediction.line < bug.start:
        distance = bug.start - prediction.line
    else:
        distance = prediction.line - bug.end
    return distance if distance <= LINE_TOLERANCE else None


def match_case(predictions: Sequence[Prediction], bugs: Sequence[PlantedBug]) -> CaseMatch:
    candidates = sorted(
        (distance, p, b)
        for p, prediction in enumerate(predictions)
        for b, bug in enumerate(bugs)
        if (distance := line_distance(prediction, bug)) is not None
        and categories_compatible(prediction.category, bug)
    )
    matched: list[tuple[int, int]] = []
    used_predictions: set[int] = set()
    found: set[int] = set()
    for _, p, b in candidates:
        if p not in used_predictions and b not in found:
            matched.append((p, b))
            used_predictions.add(p)
            found.add(b)

    false_positives: list[tuple[int, FalsePositiveReason]] = []
    for p, prediction in enumerate(predictions):
        if p in used_predictions:
            continue
        near = [b for b, bug in enumerate(bugs) if line_distance(prediction, bug) is not None]
        if any(b in found for b in near):
            false_positives.append((p, "duplicate"))
        elif near:
            false_positives.append((p, "wrong_category"))
        else:
            false_positives.append((p, "unplanted"))

    missed = [b for b in range(len(bugs)) if b not in found]
    return CaseMatch(sorted(matched), false_positives, missed)
