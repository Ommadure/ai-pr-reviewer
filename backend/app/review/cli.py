"""Review a local diff from the terminal: the engine without GitHub.

    git diff main > change.patch
    uv run python -m app.review.cli change.patch
    uv run python -m app.review.cli change.patch --provider fake      # offline
    uv run python -m app.review.cli change.patch --config .reviewpilot.yml --json

Reads LLM settings (LLM_PROVIDER, LLM_MODEL, GEMINI_API_KEY, LLM_PRICING, ...)
from the environment / backend/.env, like the server does.
"""

import argparse
import asyncio
import dataclasses
import json
import sys
from collections import defaultdict
from pathlib import Path

import httpx

from app.config.repo_config import RepoConfig, parse_repo_config
from app.core.config import ReviewSettings
from app.review.diff_parser import parse_unified_diff
from app.review.engine import run_review
from app.review.llm.factory import (
    LLMConfigurationError,
    build_provider,
    price_table,
    review_budget,
    review_model,
)
from app.review.models import FileDiff, PRContext, ReviewComment, ReviewResult
from app.review.pricing import usd_to_inr
from app.review.prompt_builder import DEFAULT_PROMPT_VERSION, available_prompt_versions
from app.review.sanitizer import SEVERITY_LABEL


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    overrides: dict[str, object] = {}
    if args.provider:
        overrides["llm_provider"] = args.provider
    if args.model:
        overrides["llm_model"] = args.model
    settings = ReviewSettings(**overrides)  # type: ignore[arg-type]

    diff_text = Path(args.patch).read_text(encoding="utf-8", errors="replace")
    files = parse_unified_diff(diff_text)
    if not files:
        print("No file changes found. Is this a `git diff` output?", file=sys.stderr)
        return 2
    parsed_config = parse_repo_config(
        Path(args.config).read_text(encoding="utf-8") if args.config else None
    )
    for message in parsed_config.errors + parsed_config.warnings:
        print(f"config: {message}", file=sys.stderr)

    try:
        result = asyncio.run(_review(settings, files, parsed_config.config, args))
    except LLMConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(dataclasses.asdict(result) | _totals(result), indent=2, default=str))
    else:
        _print_report(result, settings.usd_to_inr)
    return 0


async def _review(
    settings: ReviewSettings, files: list[FileDiff], config: RepoConfig, args: argparse.Namespace
) -> ReviewResult:
    async with httpx.AsyncClient() as http:
        llm = build_provider(settings, http)
        return await run_review(
            files,
            config,
            PRContext(title=args.title, description=args.description),
            llm,
            model=review_model(settings),
            summary_model=settings.llm_summary_model or None,
            budget=review_budget(settings),
            prices=price_table(settings),
            prompt_version=args.prompt,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.review.cli", description="Review a unified diff with ReviewPilot."
    )
    parser.add_argument("patch", help="path to a `git diff` / .patch file")
    parser.add_argument("--provider", help="override LLM_PROVIDER (gemini, fake)")
    parser.add_argument("--model", help="override LLM_MODEL")
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT_VERSION,
        choices=available_prompt_versions(),
        help="prompt version",
    )
    parser.add_argument("--config", help="path to a .reviewpilot.yml")
    parser.add_argument("--title", default="Local change", help="PR title to show the model")
    parser.add_argument("--description", default="", help="PR description to show the model")
    parser.add_argument("--json", action="store_true", help="print the raw result as JSON")
    return parser


def _totals(result: ReviewResult) -> dict[str, object]:
    return {
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
    }


def _print_report(result: ReviewResult, inr_rate: float | None) -> None:
    out: list[str] = []
    if result.summary:
        out += [f"Summary (risk: {result.summary.risk_level})", f"  {result.summary.overview}"]
        out += [f"  • {change}" for change in result.summary.key_changes]
        out += [f"  ! {note}" for note in result.summary.notes]
        out.append("")

    out.append(f"{len(result.comments)} comment(s)")
    by_file: defaultdict[str, list[ReviewComment]] = defaultdict(list)
    for comment in result.comments:
        by_file[comment.path].append(comment)
    for path, comments in by_file.items():
        out.append(f"\n{path}")
        for c in sorted(comments, key=lambda c: c.line):
            where = f"{c.start_line}-{c.line}" if c.start_line else str(c.line)
            out.append(f"  L{where}  {SEVERITY_LABEL[c.severity]} · {c.category} · {c.title}")
            out += [f"      {line}" for line in c.body.splitlines()]
            if c.suggestion:
                out.append("      suggestion:")
                out += [f"        {line}" for line in c.suggestion.splitlines()]

    if result.dropped:
        out.append(f"\nDropped {len(result.dropped)} comment(s):")
        out += [f"  {d.path}:{d.line}  {d.reason:<18} {d.title}" for d in result.dropped]
    if result.skipped_files:
        out.append(f"\nSkipped {len(result.skipped_files)} file(s):")
        out += [f"  {s.path}  ({s.reason})" for s in result.skipped_files]
    if result.errors:
        out.append("\nErrors:")
        out += [f"  {error}" for error in result.errors]

    inr = usd_to_inr(result.cost_usd, inr_rate)
    cost = f"${result.cost_usd:.5f}" + (f" (₹{inr:.3f})" if inr is not None else "")
    latency = sum(call.latency_ms for call in result.llm_calls)
    out.append(
        f"\n{result.files_reviewed}/{result.files_total} files reviewed · "
        f"{len(result.llm_calls)} LLM call(s) · {result.input_tokens} in / "
        f"{result.output_tokens} out tokens · {cost} · {latency} ms LLM time · "
        f"prompt {result.prompt_version} · {result.model}"
    )
    print("\n".join(out))


if __name__ == "__main__":
    raise SystemExit(main())
