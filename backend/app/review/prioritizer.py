"""When a PR is too big to review fully, review the riskiest files first."""

import re
from collections.abc import Sequence

from app.review.models import FileDiff, SkippedFile

SENSITIVE_PATH = re.compile(
    r"(auth|login|session|password|token|secret|payment|billing|crypto|sql|"
    r"migration|api|config|settings|permission|\.env)",
    re.IGNORECASE,
)
LOW_RISK_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|docs?)/|(_test|\.test|\.spec)\.\w+$|^test_|/test_|\.md$|\.rst$",
    re.IGNORECASE,
)


def risk_tier(file: FileDiff) -> int:
    """0 = review first, 2 = review last."""
    if SENSITIVE_PATH.search(file.path):
        return 0
    if LOW_RISK_PATH.search(file.path):
        return 2
    return 1


def prioritize(files: Sequence[FileDiff]) -> list[FileDiff]:
    """Riskiest first; within a tier, the biggest change first. Stable for ties."""
    return sorted(files, key=lambda file: (risk_tier(file), -file.changed_lines))


def limit_files(
    files: Sequence[FileDiff], max_files: int
) -> tuple[list[FileDiff], list[SkippedFile]]:
    ordered = prioritize(files)
    kept = ordered[:max_files]
    skipped = [SkippedFile(file.path, "over_file_limit") for file in ordered[max_files:]]
    return kept, skipped
