"""Make LLM-written markdown safe to post under our bot's name.

The text is shaped by untrusted PR content (prompt injection), so before posting:
- @mentions are neutralised: the bot must never ping people or teams;
- images are removed: they can be used as tracking pixels;
- raw HTML is escaped: it can hide text or break the layout;
- links to non-GitHub domains become plain text: no phishing under our name.
Code spans and fenced blocks are left untouched: they render literally anyway.
"""

import re

from app.review.models import ReviewComment, Severity

SEVERITY_LABEL: dict[Severity, str] = {
    "critical": "🚨 Critical",
    "high": "🔴 High",
    "medium": "🟠 Medium",
    "low": "🟡 Low",
    "info": "🔵 Info",
}
CATEGORY_LABEL = {
    "bug": "Bug",
    "security": "Security",
    "performance": "Performance",
    "maintainability": "Maintainability",
    "error_handling": "Error handling",
    "testing": "Testing",
    "style": "Style",
    "docs": "Docs",
}

CODE_SEGMENT = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
BARE_URL = re.compile(r"\bhttps?://[^\s)>\]]+", re.IGNORECASE)
MENTION = re.compile(r"(?<![\w.`/])@([A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9._-]+)?)")
TRUSTED_HOSTS = re.compile(
    r"^https://(?:[a-z0-9-]+\.)?(?:github\.com|githubusercontent\.com)(?:/|$)", re.IGNORECASE
)


def sanitize_markdown(text: str) -> str:
    parts = CODE_SEGMENT.split(text)
    # Odd indexes are the captured code segments; only prose is rewritten.
    return "".join(part if i % 2 else _sanitize_prose(part) for i, part in enumerate(parts))


def _sanitize_prose(text: str) -> str:
    text = IMAGE.sub(lambda m: m.group(1), text)
    text = text.replace("<", "&lt;")  # no tag can open; ">" alone is harmless (quotes)
    text = LINK.sub(lambda m: m.group(0) if _trusted(m.group(2)) else m.group(1), text)
    text = BARE_URL.sub(lambda m: m.group(0) if _trusted(m.group(0)) else "[link removed]", text)
    # A backticked mention renders as code and notifies nobody.
    return MENTION.sub(lambda m: f"`@{m.group(1)}`", text)


def _trusted(url: str) -> bool:
    return TRUSTED_HOSTS.match(url) is not None


def render_comment_body(comment: ReviewComment) -> str:
    """The markdown posted on the PR line (see spec 11.9)."""
    heading = (
        f"**{SEVERITY_LABEL[comment.severity]} · {CATEGORY_LABEL[comment.category]}: "
        f"{sanitize_markdown(comment.title)}**"
    )
    parts = [heading, sanitize_markdown(comment.body)]
    if comment.suggestion is not None:
        parts.append(render_suggestion(comment.suggestion))
    footer = f"ReviewPilot · confidence {comment.confidence:.2f} · react 👍/👎 to rate this comment"
    if comment.source == "secret_scanner":
        footer = "ReviewPilot secret scanner · react 👍/👎 to rate this comment"
    parts.append(f"<sub>{footer}</sub>")
    return "\n\n".join(parts)


def render_suggestion(code: str) -> str:
    """GitHub suggestion block. The fence must be longer than any backtick run inside."""
    longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}suggestion\n{code.rstrip(chr(10))}\n{fence}"
