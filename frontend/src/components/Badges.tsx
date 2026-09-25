import type { Severity } from "../api/types";
import { humanize } from "../lib/format";

const SEVERITY: Record<Severity, string> = {
  critical: "text-sev-critical border-sev-critical",
  high: "text-sev-high border-sev-high",
  medium: "text-sev-medium border-sev-medium",
  low: "text-sev-low border-sev-low",
  info: "text-sev-info border-sev-info",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const tone = SEVERITY[severity as Severity] ?? SEVERITY.info;
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase tracking-wide ${tone}`}
    >
      {severity}
    </span>
  );
}

const STATUS: Record<string, string> = {
  completed: "bg-add text-add-ink",
  running: "bg-accent-soft text-accent",
  queued: "bg-accent-soft text-accent",
  failed: "bg-del text-del-ink",
  superseded: "bg-gutter text-muted",
  skipped: "bg-gutter text-muted",
  open: "bg-add text-add-ink",
  merged: "bg-accent-soft text-accent",
  closed: "bg-gutter text-muted",
  addressed: "bg-add text-add-ink",
  outdated: "bg-gutter text-muted",
  valid: "bg-add text-add-ink",
  invalid: "bg-del text-del-ink",
  none: "bg-gutter text-muted",
  unknown: "bg-gutter text-muted",
};

export function StatusPill({ status, label }: { status: string; label?: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${STATUS[status] ?? "bg-gutter text-muted"}`}
    >
      {label ?? humanize(status)}
    </span>
  );
}

export function Sha({ sha }: { sha: string | null | undefined }) {
  return sha ? (
    <code className="rounded bg-gutter px-1.5 py-0.5 font-mono text-xs text-ink">{sha.slice(0, 7)}</code>
  ) : null;
}
