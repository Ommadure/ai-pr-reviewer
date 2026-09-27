"""What people see when a re-review finds only issues that were already posted."""

from app.review.models import ReviewComment, ReviewResult
from app.services.review_report import check_run_output, check_title, conclusion_for, review_body


def comment(title: str, severity: str = "high", line: int = 6) -> ReviewComment:
    return ReviewComment(
        path="users.py",
        line=line,
        start_line=None,
        severity=severity,  # type: ignore[arg-type]
        category="security",
        title=title,
        body="",
        suggestion=None,
        confidence=0.9,
        source="llm",
        fingerprint=title,
        code_snapshot="",
    )


def result(new: list[ReviewComment], earlier: list[ReviewComment]) -> ReviewResult:
    return ReviewResult(
        comments=new,
        dropped=[],
        summary=None,
        skipped_files=[],
        llm_calls=[],
        prompt_version="v4",
        model="m",
        files_total=1,
        files_reviewed=1,
        still_open=earlier,
    )


SQLI = comment("SQL injection", "critical")
LEAK = comment("Connection leak", "medium", line=5)


def test_check_title_counts_issues_that_are_still_present() -> None:
    assert check_title([], []) == "No issues found"
    assert check_title([SQLI], []) == "1 issue: 1 critical"
    assert check_title([], [SQLI, LEAK]) == (
        "No new issues · 2 issues reported earlier, still present: 1 critical, 1 medium"
    )
    assert check_title([LEAK], [SQLI]) == "1 new: 1 medium · 1 reported earlier"


def test_a_still_present_critical_keeps_the_check_neutral() -> None:
    assert conclusion_for([]) == "success"
    assert conclusion_for([LEAK]) == "success"
    assert conclusion_for([SQLI]) == "neutral"  # the orchestrator passes new + still open


def test_check_summary_lists_the_still_present_issues() -> None:
    output = check_run_output(result([], [SQLI, LEAK]), posted=0, latency_ms=900)
    assert "**Comments posted:** 0 of 0" in output.summary
    assert "**Reported earlier, still present:** 1 critical, 1 medium" in output.summary
    assert "- critical · `users.py:6` SQL injection" in output.summary
    assert "- medium · `users.py:5` Connection leak" in output.summary


def test_review_body_never_says_clean_while_issues_remain() -> None:
    body = review_body(result([], [SQLI]))
    assert "No issues found" not in body
    assert "**Reported earlier, still present:** 1 critical" in body
    assert "No issues found in the reviewed files." in review_body(result([], []))
