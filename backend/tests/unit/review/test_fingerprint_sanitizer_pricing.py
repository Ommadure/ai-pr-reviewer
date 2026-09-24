import pytest

from app.review.fingerprint import fingerprint
from app.review.llm.base import TokenUsage
from app.review.models import ReviewComment
from app.review.pricing import PriceTable
from app.review.sanitizer import render_comment_body, render_suggestion, sanitize_markdown

# ---- fingerprint ----


def test_fingerprint_ignores_whitespace_and_blank_lines() -> None:
    a = fingerprint("a.py", "bug", "    x = f(y)\n\n    return x")
    b = fingerprint("a.py", "bug", "x  =  f(y)\nreturn x   ")
    assert a == b


def test_fingerprint_depends_on_path_category_and_code() -> None:
    base = fingerprint("a.py", "bug", "x = 1")
    assert base != fingerprint("b.py", "bug", "x = 1")
    assert base != fingerprint("a.py", "security", "x = 1")
    assert base != fingerprint("a.py", "bug", "x = 2")


def test_fingerprint_has_no_line_number_input() -> None:
    # The same code moved 30 lines down (someone added code above) is the same problem.
    assert fingerprint("a.py", "bug", "query = f'{id}'") == fingerprint(
        "a.py", "bug", "query = f'{id}'"
    )


# ---- sanitizer ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ping @octocat please", "ping `@octocat` please"),
        ("cc @org/team", "cc `@org/team`"),
        ("mail me at dev@example.com", "mail me at dev@example.com"),  # not a mention
        ("![pixel](https://evil.test/p.png) text", "pixel text"),
        ("<img src=x onerror=alert(1)>", "&lt;img src=x onerror=alert(1)>"),
        ("see [docs](https://evil.test/login)", "see docs"),
        ("see [PR](https://github.com/o/r/pull/1)", "see [PR](https://github.com/o/r/pull/1)"),
        ("visit https://evil.test/x now", "visit [link removed] now"),
        ("use `@decorator` and `<T>`", "use `@decorator` and `<T>`"),  # code untouched
        ("```py\n@app.get('/')\n<b>\n```", "```py\n@app.get('/')\n<b>\n```"),
    ],
)
def test_sanitize_markdown(text: str, expected: str) -> None:
    assert sanitize_markdown(text) == expected


def test_suggestion_fence_outgrows_backticks_inside() -> None:
    assert render_suggestion("x = 1\n") == "```suggestion\nx = 1\n```"
    fenced = render_suggestion('doc = """```py```"""')
    assert fenced.startswith("````suggestion\n") and fenced.endswith("\n````")


def test_comment_body_format() -> None:
    comment = ReviewComment(
        path="a.py",
        line=3,
        start_line=None,
        severity="high",
        category="security",
        title="SQL injection via f-string",
        body="User input reaches the query. Thanks @someone!",
        suggestion='query = "SELECT * FROM users WHERE id = %s"',
        confidence=0.86,
        source="llm",
        fingerprint="f",
        code_snapshot="",
    )
    assert render_comment_body(comment) == (
        "**🔴 High · Security: SQL injection via f-string**\n\n"
        "User input reaches the query. Thanks `@someone`!\n\n"
        '```suggestion\nquery = "SELECT * FROM users WHERE id = %s"\n```\n\n'
        "<sub>ReviewPilot · confidence 0.86 · react 👍/👎 to rate this comment</sub>"
    )


# ---- pricing ----


def test_pricing_per_million_tokens_and_prefix_match() -> None:
    table = PriceTable.from_config({"model-a": (1.0, 4.0)})
    usage = TokenUsage(input_tokens=500_000, output_tokens=100_000)
    assert table.cost_usd("model-a", usage) == pytest.approx(0.5 + 0.4)
    assert table.cost_usd("model-a-001", usage) == pytest.approx(0.9)  # versioned name
    assert table.cost_usd("unknown", usage) == 0.0
    assert not table.is_priced("unknown")
