"""The matching rule (ADR 0013): what counts as finding a planted bug."""

from app.evals.dataset import PlantedBug
from app.evals.matching import Prediction, categories_compatible, line_distance, match_case


def bug(
    start: int, end: int | None = None, category: str = "security", **extra: object
) -> PlantedBug:
    return PlantedBug.model_validate(
        {
            "path": "app/users.py",
            "lines": [start, end or start],
            "category": category,
            "severity": "high",
            "description": "planted",
        }
        | extra
    )


def pred(
    line: int, category: str = "security", path: str = "app/users.py", start: int | None = None
) -> Prediction:
    return Prediction(
        path=path, line=line, start_line=start, category=category, severity="high", title="t"
    )


def test_line_within_tolerance_matches_and_beyond_it_does_not() -> None:
    target = bug(20, 22)
    assert line_distance(pred(21), target) == 0
    assert line_distance(pred(18), target) == 2
    assert line_distance(pred(24), target) == 2
    assert line_distance(pred(17), target) is None
    assert line_distance(pred(25), target) is None


def test_a_multi_line_comment_overlapping_the_bug_matches() -> None:
    assert line_distance(pred(30, start=10), bug(20)) == 0


def test_different_file_never_matches() -> None:
    assert line_distance(pred(20, path="app/other.py"), bug(20)) is None


def test_category_equivalences() -> None:
    assert categories_compatible("security", bug(1))
    assert not categories_compatible("performance", bug(1))
    # bug and error_handling are one group: a swallowed exception is fairly called either
    assert categories_compatible("error_handling", bug(1, category="bug"))
    assert categories_compatible("bug", bug(1, category="error_handling"))
    # per-case extras for genuinely ambiguous bugs
    assert categories_compatible("bug", bug(1, accept_categories=["bug"]))


def test_one_to_one_closest_first_and_extras_are_duplicates() -> None:
    result = match_case([pred(22), pred(20), pred(21)], [bug(20)])
    assert result.matched == [(1, 0)]  # the exact line wins
    assert sorted(reason for _, reason in result.false_positives) == ["duplicate", "duplicate"]
    assert result.missed == []


def test_wrong_category_near_a_bug_is_reported_as_such() -> None:
    result = match_case([pred(20, category="performance")], [bug(20)])
    assert (result.tp, result.fn) == (0, 1)
    assert result.false_positives == [(0, "wrong_category")]


def test_comment_far_from_every_bug_is_unplanted() -> None:
    result = match_case([pred(90)], [bug(20)])
    assert result.false_positives == [(0, "unplanted")]
    assert result.missed == [0]


def test_two_bugs_two_comments() -> None:
    result = match_case([pred(41), pred(10)], [bug(10), bug(40, 42)])
    assert result.matched == [(0, 1), (1, 0)]
    assert (result.fp, result.fn) == (0, 0)
