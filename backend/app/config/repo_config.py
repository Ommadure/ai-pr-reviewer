""".reviewpilot.yml: per-repository settings.

Read from the repo's *default branch* (Phase 3), never the PR branch, so a PR
author can't switch the reviewer off for their own PR. Its contents come from
users, so it's parsed defensively: safe_load only, size limits, and an invalid
file falls back to defaults with the problem reported instead of failing the run.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.review.models import Category, Severity

MAX_CONFIG_BYTES = 20 * 1024
MAX_CUSTOM_RULES = 20
MAX_RULE_CHARS = 300


class RepoConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = 1
    enabled: bool = True
    review_drafts: bool = False
    post_when_clean: bool = False
    comment_on_context_lines: bool = False
    min_severity: Severity = "low"
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    max_comments: int = Field(default=15, ge=0, le=50)
    focus: list[Category] = Field(default_factory=list)  # empty = every category
    ignore_paths: list[str] = Field(default_factory=list, max_length=100)
    # These go into the prompt verbatim, so they're capped in number and length.
    custom_rules: list[str] = Field(default_factory=list, max_length=MAX_CUSTOM_RULES)
    summary_language: str = Field(default="en", max_length=16)

    @field_validator("custom_rules")
    @classmethod
    def _rule_length(cls, rules: list[str]) -> list[str]:
        for rule in rules:
            if len(rule) > MAX_RULE_CHARS:
                raise ValueError(f"custom rules must be at most {MAX_RULE_CHARS} characters")
        return [rule.strip() for rule in rules if rule.strip()]


@dataclass
class ParsedRepoConfig:
    config: RepoConfig
    is_valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_repo_config(raw: str | None) -> ParsedRepoConfig:
    """Never raises: a broken config means defaults plus a reported error."""
    if raw is None or not raw.strip():
        return ParsedRepoConfig(RepoConfig(), is_valid=True)
    if len(raw.encode()) > MAX_CONFIG_BYTES:
        return _invalid(f".reviewpilot.yml is larger than {MAX_CONFIG_BYTES // 1024} KB")
    try:
        # safe_load: plain data only. yaml.load could construct arbitrary Python objects.
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return _invalid(f"YAML syntax error: {exc}")
    if data is None:
        return ParsedRepoConfig(RepoConfig(), is_valid=True)
    if not isinstance(data, dict):
        return _invalid("the top level of .reviewpilot.yml must be a mapping")

    warnings = [
        f"unknown key '{key}' ignored" for key in data if str(key) not in RepoConfig.model_fields
    ]
    try:
        config = RepoConfig.model_validate(data)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
        return ParsedRepoConfig(RepoConfig(), is_valid=False, warnings=warnings, errors=errors)
    return ParsedRepoConfig(config, is_valid=True, warnings=warnings)


def _invalid(error: str) -> ParsedRepoConfig:
    return ParsedRepoConfig(RepoConfig(), is_valid=False, errors=[error])
