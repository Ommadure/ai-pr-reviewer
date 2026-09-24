"""Turn diffs + PR metadata into LLM messages, using versioned prompt files.

The diff is rendered with explicit new-file line numbers on every line:

    <file path="app/users.py" status="modified" language="python">
    @@ -10,6 +10,8 @@ def get_user
      10 |     def get_user(user_id):
    + 11 |         query = f"SELECT * FROM users WHERE id = {user_id}"
    -    |         query = "SELECT * FROM users WHERE id = %s"
      12 |         return db.execute(query)
    </file>

Models are bad at counting lines inside hunks; copying a number they can see is
much more reliable, which cuts hallucinated line numbers sharply.
"""

import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from app.config.repo_config import RepoConfig
from app.review.llm.base import Message
from app.review.models import FileDiff, FileSummary, PRContext

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_PROMPT_VERSION = "v1"
PROMPT_VERSION_FORMAT = re.compile(r"^v\d+$")
MAX_DESCRIPTION_CHARS = 2_000
# Our structural tags. PR content that contains them could otherwise "close"
# the untrusted section early and smuggle text outside it.
DELIMITER_TAGS = re.compile(
    r"<(/?)(pr_metadata|diff|file|review_notes|repository_rules)\b", re.IGNORECASE
)
LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".sql": "sql",
    ".sh": "shell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".md": "markdown",
}


def available_prompt_versions() -> list[str]:
    return sorted(p.name for p in PROMPTS_DIR.iterdir() if PROMPT_VERSION_FORMAT.match(p.name))


@lru_cache
def load_template(version: str, name: str) -> str:
    if not PROMPT_VERSION_FORMAT.match(version):
        raise ValueError(f"invalid prompt version {version!r}")  # also blocks "../"
    return (PROMPTS_DIR / version / f"{name}.md").read_text()


def fill(template: str, **values: str) -> str:
    """`{{name}}` placeholders; values are inserted verbatim (no format-string pitfalls)."""
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    if "{{" in template:
        raise ValueError(f"unfilled placeholder in prompt: {template[template.index('{{') :][:40]}")
    return template


def neutralize(text: str) -> str:
    return DELIMITER_TAGS.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", text)


def language_for(path: str) -> str:
    return LANGUAGES.get(Path(path).suffix.lower(), "text")


def render_file(file: FileDiff) -> str:
    attrs = f'path="{_attr(file.path)}" status="{file.status}" language="{language_for(file.path)}"'
    if file.previous_path:
        attrs += f' previous_path="{_attr(file.previous_path)}"'
    numbers = [line.new_line for hunk in file.hunks for line in hunk.lines if line.new_line]
    width = len(str(max(numbers, default=0)))
    out = [f"<file {attrs}>"]
    for hunk in file.hunks:
        out.append(neutralize(hunk.header))
        for line in hunk.lines:
            content = neutralize(line.content)
            if line.type == "added":
                out.append(f"+ {line.new_line:>{width}} | {content}")
            elif line.type == "removed":
                out.append(f"- {'':>{width}} | {content}")
            else:
                out.append(f"  {line.new_line:>{width}} | {content}")
    out.append("</file>")
    return "\n".join(out)


def render_pr_metadata(pr: PRContext) -> str:
    description = pr.description.strip() or "(no description)"
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS] + " …(truncated)"
    lines = [
        f"Title: {pr.title}",
        f"Repository: {pr.repo_full_name}" if pr.repo_full_name else "",
        f"Author: {pr.author}" if pr.author else "",
        f"Branch: {pr.head_ref} → {pr.base_ref}" if pr.head_ref and pr.base_ref else "",
        f"Description:\n{description}",
    ]
    return neutralize("\n".join(line for line in lines if line))


def render_repo_rules(config: RepoConfig) -> str:
    """Rules from the repo's default-branch config: maintainers' voice, not the PR author's."""
    rules: list[str] = []
    if config.focus:
        rules.append(f"Only report issues in these categories: {', '.join(config.focus)}.")
    rules.extend(config.custom_rules)
    if not rules:
        return ""
    body = "\n".join(f"- {neutralize(rule)}" for rule in rules)
    intro = "The repository maintainers ask you to follow these rules:"
    return f"<repository_rules>\n{intro}\n{body}\n</repository_rules>\n"


def build_review_messages(
    files: Sequence[FileDiff],
    pr: PRContext,
    config: RepoConfig,
    *,
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[Message]:
    user = fill(
        load_template(version, "file_review"),
        repo_rules=render_repo_rules(config),
        pr_metadata=render_pr_metadata(pr),
        diff="\n\n".join(render_file(file) for file in files),
    )
    return [Message("system", load_template(version, "system")), Message("user", user)]


def build_summary_messages(
    pr: PRContext,
    file_summaries: Sequence[FileSummary],
    issue_lines: Sequence[str],
    *,
    language: str,
    version: str = DEFAULT_PROMPT_VERSION,
) -> list[Message]:
    notes = ["File summaries:"]
    notes += [f"- {s.path}: {s.summary}" for s in file_summaries] or ["- (none)"]
    notes += ["", "Issues found:"]
    notes += [f"- {line}" for line in issue_lines] or ["- (none)"]
    user = fill(
        load_template(version, "summary"),
        pr_metadata=render_pr_metadata(pr),
        review_notes=neutralize("\n".join(notes)),
        language=language,
    )
    return [Message("user", user)]


REPAIR_INSTRUCTION = (
    "Your previous response could not be used: {problem}. "
    "Reply again with only the corrected JSON object, matching the required shape exactly."
)


def build_repair_messages(
    original: Sequence[Message], raw_response: str, problem: str
) -> list[Message]:
    return [
        *original,
        Message("assistant", raw_response[:8_000]),
        Message("user", REPAIR_INSTRUCTION.format(problem=problem)),
    ]


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
