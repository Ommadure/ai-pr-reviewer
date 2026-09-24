"""Find secrets in the diff, redact them before any LLM call, and flag them.

Two jobs:
1. Replace every secret in the text we send to the LLM with [REDACTED_SECRET]
   (added, removed and context lines alike: a removed secret is still a leak).
2. For secrets on *added* lines, produce findings that become deterministic
   review comments. These work even when the LLM is down, and never repeat
   the secret value.
"""

import re
from dataclasses import dataclass, replace

from app.review.models import DiffLine, FileDiff, Hunk

REDACTED = "[REDACTED_SECRET]"


@dataclass(frozen=True)
class SecretPattern:
    kind: str  # human-readable, used in the review comment
    regex: re.Pattern[str]
    group: int = 0  # which group holds the secret (0 = whole match)


SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    SecretPattern("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    SecretPattern("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    SecretPattern("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    SecretPattern("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    SecretPattern("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    SecretPattern(
        "JSON Web Token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    SecretPattern(
        "hardcoded credential",
        # api_key = "....", "password": "....", SECRET_TOKEN='....' (string literals only)
        re.compile(
            r"(?i)(?:api[_-]?key|secret|token|password|passwd)[\w-]*[\"']?\s*[:=]\s*"
            r"[\"']([^\"'\s]{12,})[\"']"
        ),
        group=1,
    ),
)
PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
# Values that are obviously not real credentials.
PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|dummy|changeme|change_me|your[_-]|xxxx|\*\*\*\*|<|\$\{|\{\{)"
)


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int  # new-file line number (added lines only)
    kind: str


def redact_line(text: str) -> tuple[str, list[str]]:
    """Returns (redacted text, kinds of secrets found)."""
    kinds: list[str] = []
    for pattern in SECRET_PATTERNS:

        def substitute(match: re.Match[str], pattern: SecretPattern = pattern) -> str:
            secret = match.group(pattern.group)
            if REDACTED in secret or (pattern.group and PLACEHOLDER.search(secret)):
                return match.group(0)  # already redacted by a more specific pattern
            kinds.append(pattern.kind)
            start = match.start(pattern.group) - match.start(0)
            whole = match.group(0)
            return whole[:start] + REDACTED + whole[start + len(secret) :]

        text = pattern.regex.sub(substitute, text)
    return text, kinds


def redact_files(files: list[FileDiff]) -> tuple[list[FileDiff], list[SecretFinding]]:
    redacted_files: list[FileDiff] = []
    findings: list[SecretFinding] = []
    for file in files:
        new_hunks: list[Hunk] = []
        in_private_key = False
        for hunk in file.hunks:
            new_lines: list[DiffLine] = []
            for line in hunk.lines:
                kinds: list[str] = []
                if in_private_key or PRIVATE_KEY_BEGIN.search(line.content):
                    if not in_private_key:
                        kinds.append("private key")
                    # Redact the whole block, BEGIN through END.
                    in_private_key = not PRIVATE_KEY_END.search(line.content)
                    content = REDACTED
                else:
                    content, kinds = redact_line(line.content)
                if kinds and line.type == "added" and line.new_line is not None:
                    findings.extend(
                        SecretFinding(file.path, line.new_line, kind)
                        for kind in dict.fromkeys(kinds)
                    )
                new_lines.append(replace(line, content=content))
            new_hunks.append(replace(hunk, lines=tuple(new_lines)))
        redacted_files.append(replace(file, hunks=tuple(new_hunks)))
    return redacted_files, findings
