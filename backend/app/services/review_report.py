"""Turn a ReviewResult into what humans see: the review body and the check run.

All LLM-written text passes through the sanitizer before it's posted.
"""

from collections import Counter
from collections.abc import Sequence

from app.github.client import CheckRunOutput, Conclusion, ReviewCommentInput
from app.review.models import SEVERITY_RANK, ReviewComment, ReviewResult, Severity
from app.review.sanitizer import CATEGORY_LABEL, render_comment_body, sanitize_markdown

CHECK_RUN_NAME = "ReviewPilot"
# GitHub caps a check run summary and a review body at 65,535 characters.
MAX_TEXT = 60_000
SEVERITY_ORDER: tuple[Severity, ...] = ("critical", "high", "medium", "low", "info")


def to_review_comment(comment: ReviewComment) -> ReviewCommentInput:
    return ReviewCommentInput(
        path=comment.path,
        line=comment.line,
        side="RIGHT",
        start_line=comment.start_line,
        start_side="RIGHT" if comment.start_line else None,
        body=render_comment_body(comment),
    )


def severity_counts(comments: Sequence[ReviewComment]) -> str:
    counts = Counter(c.severity for c in comments)
    return ", ".join(f"{counts[s]} {s}" for s in SEVERITY_ORDER if counts[s])


def conclusion_for(comments: Sequence[ReviewComment]) -> Conclusion:
    # "neutral" draws the eye to serious findings without ever blocking a merge.
    serious = any(SEVERITY_RANK[c.severity] >= SEVERITY_RANK["high"] for c in comments)
    return "neutral" if serious else "success"


def check_title(comments: Sequence[ReviewComment]) -> str:
    if not comments:
        return "No issues found"
    noun = "issue" if len(comments) == 1 else "issues"
    return f"{len(comments)} {noun}: {severity_counts(comments)}"


def review_body(result: ReviewResult, *, config_warnings: Sequence[str] = ()) -> str:
    """The top-level review text: the PR summary plus anything the reader should know."""
    parts = ["### 🤖 ReviewPilot review"]
    summary = result.summary
    if summary:
        parts.append(sanitize_markdown(summary.overview))
        issues = severity_counts(result.comments) or "none"
        parts.append(f"**Risk:** {summary.risk_level} · **Issues:** {issues}")
        if summary.key_changes:
            parts.append(
                "**Key changes**\n"
                + "\n".join(f"- {sanitize_markdown(c)}" for c in summary.key_changes[:6])
            )
        if summary.notes:
            parts.append(
                "**Notes**\n" + "\n".join(f"- {sanitize_markdown(n)}" for n in summary.notes[:4])
            )
    elif result.comments:
        parts.append(f"**Issues:** {severity_counts(result.comments)}")
    else:
        parts.append("No issues found in the reviewed files.")
    parts += _skipped_and_warnings(result, config_warnings)
    parts.append(
        f"<sub>ReviewPilot · prompt {result.prompt_version} · {result.model} · "
        "comments are advisory · react 👍/👎 on a comment to rate it</sub>"
    )
    return _truncate("\n\n".join(parts))


def check_run_output(
    result: ReviewResult,
    *,
    posted: int,
    latency_ms: int,
    config_warnings: Sequence[str] = (),
) -> CheckRunOutput:
    lines = []
    if result.summary:
        lines += [
            f"**Overview:** {sanitize_markdown(result.summary.overview)}",
            f"**Risk:** {result.summary.risk_level}",
        ]
    lines.append(f"**Comments posted:** {posted} of {len(result.comments)}")
    if result.comments:
        lines.append("")
        lines.append("| Severity | Count |\n|---|---|")
        counts = Counter(c.severity for c in result.comments)
        lines += [f"| {s} | {counts[s]} |" for s in SEVERITY_ORDER if counts[s]]
        by_category = Counter(CATEGORY_LABEL[c.category] for c in result.comments)
        lines.append("")
        lines.append("**By category:** " + ", ".join(f"{k} {v}" for k, v in by_category.items()))
    lines += ["", *_skipped_and_warnings(result, config_warnings)]
    lines += [
        "",
        f"**Files:** {result.files_reviewed} reviewed of {result.files_total} changed",
        f"**Tokens:** {result.input_tokens:,} in / {result.output_tokens:,} out · "
        f"**Cost:** ${result.cost_usd:.4f} · **Time:** {latency_ms / 1000:.1f}s",
        f"**Prompt:** {result.prompt_version} · **Model:** {result.model}",
    ]
    if result.errors:
        lines.append("\n**Some parts failed and were skipped:**")
        lines += [f"- {sanitize_markdown(error[:300])}" for error in result.errors[:10]]
    return CheckRunOutput(title=check_title(result.comments), summary=_truncate("\n".join(lines)))


def _skipped_and_warnings(result: ReviewResult, config_warnings: Sequence[str]) -> list[str]:
    out: list[str] = []
    if config_warnings:
        out.append(
            "**⚠️ .reviewpilot.yml problems (defaults used where invalid):**\n"
            + "\n".join(f"- {sanitize_markdown(w)}" for w in config_warnings[:10])
        )
    if result.skipped_files:
        rows = "\n".join(
            f"- `{s.path}`: {s.reason.replace('_', ' ')}" for s in result.skipped_files[:50]
        )
        more = len(result.skipped_files) - 50
        if more > 0:
            rows += f"\n- …and {more} more"
        out.append(
            f"<details><summary>Skipped files ({len(result.skipped_files)})</summary>\n\n"
            f"{rows}\n\n</details>"
        )
    return out


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n\n…(truncated)"
