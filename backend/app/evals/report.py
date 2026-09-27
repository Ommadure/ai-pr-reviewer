"""Markdown reports: one run, or two runs side by side."""

from app.evals.results import CaseResult, Metrics, RunResult, bad_fixes


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def secs(ms: int | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f} s"


def usd(value: float) -> str:
    return f"${value:.4f}"


def label(run: RunResult) -> str:
    return f"{run.prompt_version} · {run.model}"


def headline_rows(m: Metrics) -> list[tuple[str, str]]:
    return [
        ("Precision", pct(m.precision)),
        ("Recall", pct(m.recall)),
        ("F1", pct(m.f1)),
        ("Bugs found", f"{m.tp} / {m.planted_bugs}"),
        ("False positives", str(m.fp)),
        ("FP per clean case", "-" if m.fp_per_clean_case is None else f"{m.fp_per_clean_case:.1f}"),
        ("Comments per case", f"{m.avg_comments_per_case:.2f}"),
        ("Severity exact", pct(m.severity_exact)),
        ("Severity within 1 level", pct(m.severity_within_one)),
        ("Known-wrong fixes", f"{m.bad_fixes} / {m.fixes_checked}" if m.fixes_checked else "-"),
        ("Fixes offered on found bugs", f"{m.fixes_offered} / {m.tp}"),
        (
            "Unusable suggestions (dropped)",
            f"{m.suggestions_dropped} / {m.suggestions_written}" if m.suggestions_written else "-",
        ),
        ("Cost per case", usd(m.cost_usd_avg)),
        ("Cost, total", usd(m.cost_usd_total)),
        ("Latency p50", secs(m.latency_p50_ms)),
        ("Latency p95", secs(m.latency_p95_ms)),
        ("Tokens in / out", f"{m.input_tokens:,} / {m.output_tokens:,}"),
        ("Cases with errors", str(m.cases_with_errors)),
    ]


def run_report(run: RunResult) -> str:
    m = run.metrics
    out = [
        f"# Eval: prompt {run.prompt_version} · {run.model}",
        "",
        f"{run.created_at:%Y-%m-%d %H:%M} UTC · provider `{run.provider}` · "
        f"prompt sha `{run.prompt_sha}` · temperature {run.temperature} · "
        f"{m.cases} cases ({m.bug_cases} with bugs, {m.clean_cases} clean) · "
        f"match: same path, line ±{run.line_tolerance}, compatible category",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        *(f"| {name} | {value} |" for name, value in headline_rows(m)),
        "",
        "## By category",
        "",
        "Recall counts planted bugs of the category; precision counts comments filed under it.",
        "",
        "| Category | Found / planted | Recall | Correct / comments | Precision | F1 |",
        "| --- | --- | --- | --- | --- | --- |",
        *(
            f"| {name} | {c.found} / {c.planted} | {pct(c.recall)} | {c.correct} / {c.predicted} | "
            f"{pct(c.precision)} | {pct(c.f1)} |"
            for name, c in m.per_category.items()
        ),
        "",
        "## False positives by reason",
        "",
        *(f"- {reason}: {n}" for reason, n in sorted(m.fp_reasons.items())),
        *([] if m.fp_reasons else ["None."]),
        "",
        "## Cases",
        "",
        "| Case | Found | FP | Comments | Cost | LLM time | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *(_case_row(c) for c in run.cases),
        "",
        "## What went wrong",
        "",
        *_failures(run.cases),
    ]
    return "\n".join(out) + "\n"


def _case_row(c: CaseResult) -> str:
    found = f"{len(c.matched)} / {len(c.planted)}" if c.planted else "clean"
    dropped = f"{c.dropped_by_validator} dropped by validator" if c.dropped_by_validator else ""
    notes = "; ".join(e[:80] for e in c.errors) or dropped
    return (
        f"| `{c.id}` | {found} | {len(c.false_positives)} | {len(c.predictions)} | "
        f"{usd(c.cost_usd)} | {secs(c.latency_ms)} | {notes} |"
    )


def _failures(cases: list[CaseResult]) -> list[str]:
    lines: list[str] = []
    for c in cases:
        for b in c.missed:
            bug = c.planted[b]
            lines.append(
                f"- **missed** `{c.id}` {bug.path}:{bug.start}-{bug.end} "
                f"({bug.category}): {bug.description}"
            )
        for p, reason in c.false_positives:
            pred = c.predictions[p]
            lines.append(
                f"- **{reason}** `{c.id}` {pred.path}:{pred.line} "
                f"({pred.severity}, {pred.category}): {pred.title}"
            )
        for p, b, pattern in bad_fixes(c):
            pred = c.predictions[p]
            lines.append(
                f"- **wrong fix** `{c.id}` {pred.path}:{pred.line}: found "
                f'"{c.planted[b].description}" but the suggestion matches `{pattern}`'
            )
    return lines or ["Nothing: every planted bug found, no false positives, no wrong fixes."]


def compare_report(a: RunResult, b: RunResult) -> str:
    """Side by side, with B - A deltas, then every case whose outcome changed."""
    rows_a, rows_b = headline_rows(a.metrics), headline_rows(b.metrics)
    out = [
        f"# Compare: {label(a)} → {label(b)}",
        "",
        f"A: `{a.prompt_version}` sha `{a.prompt_sha}`, {a.created_at:%Y-%m-%d %H:%M} UTC · "
        f"B: `{b.prompt_version}` sha `{b.prompt_sha}`, {b.created_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"| Metric | A: {label(a)} | B: {label(b)} | Δ |",
        "| --- | --- | --- | --- |",
    ]
    deltas = _deltas(a.metrics, b.metrics)
    for (name, va), (_, vb) in zip(rows_a, rows_b, strict=True):
        out.append(f"| {name} | {va} | {vb} | {deltas.get(name, '')} |")

    categories = sorted(set(a.metrics.per_category) | set(b.metrics.per_category))
    out += ["", "## Recall by category", "", "| Category | A | B |", "| --- | --- | --- |"]
    for name in categories:
        cells = [
            f"{m.found} / {m.planted}" if m and m.planted else "-"
            for m in (a.metrics.per_category.get(name), b.metrics.per_category.get(name))
        ]
        out.append(f"| {name} | {cells[0]} | {cells[1]} |")

    out += ["", "## Cases that changed", ""]
    by_id = {c.id: c for c in b.cases}
    changed = []
    for case_a in a.cases:
        case_b = by_id.get(case_a.id)
        if case_b is not None and _outcome(case_a) != _outcome(case_b):
            changed.append(f"| `{case_a.id}` | {_outcome(case_a)} | {_outcome(case_b)} |")
    if changed:
        out += ["| Case | A | B |", "| --- | --- | --- |", *changed]
    else:
        out.append("No case changed outcome.")
    return "\n".join(out) + "\n"


def _outcome(case: CaseResult) -> str:
    wrong = len(bad_fixes(case))
    return f"{len(case.matched)} found, {len(case.false_positives)} FP" + (
        f", {wrong} wrong fix" if wrong else ""
    )


def _deltas(a: Metrics, b: Metrics) -> dict[str, str]:
    def points(x: float | None, y: float | None) -> str:
        return "" if x is None or y is None else f"{(y - x) * 100:+.0f} pts"

    def change(x: float | None, y: float | None, fmt: str = "{:+.2f}") -> str:
        return "" if x is None or y is None else fmt.format(y - x)

    return {
        "Precision": points(a.precision, b.precision),
        "Recall": points(a.recall, b.recall),
        "F1": points(a.f1, b.f1),
        "False positives": change(a.fp, b.fp, "{:+.0f}"),
        "FP per clean case": change(a.fp_per_clean_case, b.fp_per_clean_case, "{:+.1f}"),
        "Comments per case": change(a.avg_comments_per_case, b.avg_comments_per_case),
        "Severity exact": points(a.severity_exact, b.severity_exact),
        "Severity within 1 level": points(a.severity_within_one, b.severity_within_one),
        "Known-wrong fixes": change(a.bad_fixes, b.bad_fixes, "{:+.0f}"),
        "Fixes offered on found bugs": change(a.fixes_offered, b.fixes_offered, "{:+.0f}"),
        "Unusable suggestions (dropped)": change(
            a.suggestions_dropped, b.suggestions_dropped, "{:+.0f}"
        ),
        "Cost per case": change(a.cost_usd_avg, b.cost_usd_avg, "{:+.4f}"),
        "Latency p50": change(
            None if a.latency_p50_ms is None else a.latency_p50_ms / 1000,
            None if b.latency_p50_ms is None else b.latency_p50_ms / 1000,
            "{:+.1f} s",
        ),
        "Latency p95": change(
            None if a.latency_p95_ms is None else a.latency_p95_ms / 1000,
            None if b.latency_p95_ms is None else b.latency_p95_ms / 1000,
            "{:+.1f} s",
        ),
    }
