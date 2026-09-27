"""run_review() end to end with a scripted LLM: no network, fully deterministic."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.config.repo_config import RepoConfig
from app.review.diff_parser import parse_unified_diff
from app.review.engine import run_review
from app.review.llm.base import LLMError, Message
from app.review.llm.fake import FakeLLMProvider
from app.review.models import FileDiff, PRContext, ReviewBudget
from app.review.pricing import PriceTable
from app.review.prompt_builder import build_review_messages, render_file
from app.review.redaction import REDACTED
from app.review.tokens import estimate_tokens

DIFFS = Path(__file__).parents[2] / "fixtures" / "diffs"
PR = PRContext(title="Add user lookup", repo_full_name="octocat/playground")
SUMMARY = json.dumps(
    {"overview": "Adds a user lookup.", "risk_level": "high", "key_changes": ["x"], "notes": []}
)


@pytest.fixture
def files() -> list[FileDiff]:
    return parse_unified_diff((DIFFS / "sample.patch").read_text())


def _review(comments: list[dict[str, Any]], summaries: list[str] | None = None) -> str:
    return json.dumps(
        {
            "comments": comments,
            "file_summaries": [{"path": p, "summary": "changed"} for p in summaries or []],
        }
    )


def _comment(path: str, line: int, **overrides: Any) -> dict[str, Any]:
    return {
        "path": path,
        "line": line,
        "severity": "high",
        "category": "bug",
        "title": f"issue at {line}",
        "body": "explanation",
        "confidence": 0.9,
    } | overrides


def _is_summary_call(messages: list[Message]) -> bool:
    return "<review_notes>" in messages[-1].content


async def test_happy_path(files: list[FileDiff]) -> None:
    def respond(messages: list[Message]) -> str:
        if _is_summary_call(messages):
            return SUMMARY
        return _review(
            [
                _comment(
                    "app/users.py",
                    12,
                    category="security",
                    severity="critical",
                    title="SQL injection",
                    suggestion='    query = "SELECT * FROM users WHERE id = ?"',
                ),
                _comment("web/src/api.ts", 2, title="Missing await"),
                _comment("app/users.py", 99, title="hallucinated line"),
                _comment("app/nope.py", 1, title="hallucinated file"),
            ],
            ["app/users.py", "web/src/api.ts"],
        )

    llm = FakeLLMProvider(responder=respond)
    result = await run_review(
        files,
        RepoConfig(),
        PR,
        llm,
        model="m",
        prices=PriceTable.from_config({"m": (1.0, 2.0)}),
    )

    assert [(c.path, c.line, c.title) for c in result.comments] == [
        ("app/users.py", 12, "SQL injection"),
        ("web/src/api.ts", 2, "Missing await"),
    ]
    assert result.comments[0].suggestion is not None
    assert sorted((d.title, d.reason) for d in result.dropped) == [
        ("hallucinated file", "unknown_path"),
        ("hallucinated line", "invalid_line"),
    ]
    assert {s.path: s.reason for s in result.skipped_files} == {
        "docs/guide.md": "no_added_lines",
        "scripts/cleanup.sql": "deleted",
        "web/public/logo.png": "binary",
        "web/package-lock.json": "ignored_path",
    }
    assert (result.files_total, result.files_reviewed) == (6, 2)
    assert result.summary is not None and result.summary.risk_level == "high"
    assert [c.purpose for c in result.llm_calls] == ["file_review", "summary"]
    assert result.cost_usd > 0 and result.errors == []

    # Only reviewable files were sent, with line numbers the model can copy.
    prompt = llm.calls[0][1].content
    assert '+ 12 |     query = f"SELECT * FROM users WHERE id = {user_id}"' in prompt
    assert "package-lock" not in prompt and "cleanup.sql" not in prompt


async def test_invalid_json_gets_one_repair_attempt(files: list[FileDiff]) -> None:
    llm = FakeLLMProvider(['{"comments": [oops', _review([_comment("app/users.py", 13)]), SUMMARY])
    result = await run_review(files, RepoConfig(), PR, llm, model="m")

    assert [(c.purpose, c.status) for c in result.llm_calls] == [
        ("file_review", "error"),
        ("repair", "success"),
        ("summary", "success"),
    ]
    assert [c.line for c in result.comments] == [13]
    repair_prompt = llm.calls[1][-1].content
    assert "malformed JSON" in repair_prompt


async def test_failed_chunk_does_not_sink_the_run(files: list[FileDiff]) -> None:
    def respond(messages: list[Message]) -> str:
        if _is_summary_call(messages):
            return SUMMARY
        if any("app/users.py" in m.content for m in messages):
            return "still not json"  # fails, and the repair attempt fails too
        return _review([_comment("web/src/api.ts", 2)], ["web/src/api.ts"])

    budget = ReviewBudget(max_chunk_tokens=_one_file_per_call_limit(files))
    llm = FakeLLMProvider(responder=respond)
    result = await run_review(files, RepoConfig(), PR, llm, model="m", budget=budget)

    assert [c.path for c in result.comments] == ["web/src/api.ts"]
    assert ("app/users.py", "llm_error") in {(s.path, s.reason) for s in result.skipped_files}
    assert any("still invalid after repair" in e for e in result.errors)
    assert result.summary is not None  # the rest of the review still completes


def _one_file_per_call_limit(files: list[FileDiff]) -> int:
    """A per-call token limit that fits the biggest reviewable file, but never two."""
    overhead = estimate_tokens(
        "\n".join(m.content for m in build_review_messages([], PR, RepoConfig()))
    )
    reviewable = [f for f in files if f.path in {"app/users.py", "web/src/api.ts"}]
    return overhead + max(estimate_tokens(render_file(f)) for f in reviewable) + 1


async def test_provider_outage_is_recorded_not_raised(files: list[FileDiff]) -> None:
    llm = FakeLLMProvider(responder=lambda _: LLMError("Gemini unreachable"))
    result = await run_review(files, RepoConfig(), PR, llm, model="m")
    assert result.comments == [] and result.summary is None
    assert result.errors and "Gemini unreachable" in result.errors[0]


async def test_secrets_are_redacted_and_flagged_without_the_llm() -> None:
    key = "AKIA" + "IOSFODNN7EXAMPLE"
    diff = (
        "diff --git a/app/aws.py b/app/aws.py\n--- a/app/aws.py\n+++ b/app/aws.py\n"
        f'@@ -1,1 +1,2 @@\n import boto3\n+ACCESS_KEY = "{key}"\n'
    )
    llm = FakeLLMProvider(responder=lambda _: LLMError("LLM down"))
    result = await run_review(parse_unified_diff(diff), RepoConfig(), PR, llm, model="m")

    [comment] = result.comments
    assert (comment.source, comment.severity, comment.line) == ("secret_scanner", "critical", 2)
    assert key not in comment.body and key not in comment.code_snapshot
    assert key not in "".join(m.content for call in llm.calls for m in call)
    assert REDACTED in llm.calls[0][1].content


async def test_already_posted_comments_are_not_repeated(files: list[FileDiff]) -> None:
    llm = FakeLLMProvider(
        responder=lambda m: (
            SUMMARY if _is_summary_call(m) else _review([_comment("app/users.py", 13)])
        )
    )
    first = await run_review(files, RepoConfig(), PR, llm, model="m")
    [posted] = first.comments

    second = await run_review(
        files, RepoConfig(), PR, llm, model="m", existing_fingerprints={posted.fingerprint}
    )
    assert second.comments == []
    assert [d.reason for d in second.dropped] == ["already_posted"]
    # Not reposted, but still present: it still counts, and the summary's risk sees it.
    assert [c.fingerprint for c in second.still_open] == [posted.fingerprint]
    summary_prompt = next(call for call in reversed(llm.calls) if _is_summary_call(call))
    assert "issue at 13 (reported earlier, still present)" in summary_prompt[-1].content


async def test_clean_change_produces_no_comments() -> None:
    files = parse_unified_diff((DIFFS / "clean.patch").read_text())
    llm = FakeLLMProvider(responder=lambda m: SUMMARY if _is_summary_call(m) else _review([]))
    result = await run_review(files, RepoConfig(), PR, llm, model="m")
    assert result.comments == [] and result.dropped == []
    assert result.files_reviewed == 1


async def test_a_suggestion_that_would_duplicate_code_is_dropped_not_the_comment(
    files: list[FileDiff],
) -> None:
    # A fixed line 12 plus a copy of line 13: applied over line 12 alone, 13 doubles.
    misfit = (
        '    query = "SELECT * FROM users WHERE id = ?"\n    row = db.execute(query).fetchone()'
    )
    llm = FakeLLMProvider(
        responder=lambda m: (
            SUMMARY
            if _is_summary_call(m)
            else _review([_comment("app/users.py", 12, suggestion=misfit)])
        )
    )
    result = await run_review(files, RepoConfig(), PR, llm, model="m")
    [comment] = result.comments
    assert (comment.suggestion, comment.suggestion_dropped) == (None, True)
    assert comment.line == 12  # the finding itself still posts
