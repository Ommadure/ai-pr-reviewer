import type { Severity } from "../api/types";
import { humanize } from "../lib/format";
import { ScopeGlyph } from "./cockpit/RadarScope";

/*
 * Severities follow cockpit alerting: a WARNING (red) and a CAUTION (amber) are lit
 * solid because they need action; advisories are outlined. The word is always printed,
 * so the level never depends on colour alone.
 */
const SEVERITY: Record<Severity, string> = {
  critical: "bg-red text-on-route border-red",
  high: "bg-amber text-on-route border-amber",
  medium: "text-amber border-amber/70 bg-amber-soft",
  low: "text-cyan border-cyan/60 bg-cyan-soft",
  info: "text-muted border-line-strong",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const level: Severity = Object.hasOwn(SEVERITY, severity) ? (severity as Severity) : "info";
  return (
    <span
      data-level={level}
      className={`inline-flex items-center rounded-[3px] border px-1.5 py-[3px] font-mono text-[10px] leading-none font-bold tracking-[0.08em] uppercase ${SEVERITY[level]}`}
    >
      {severity}
    </span>
  );
}

type Tone = "green" | "cyan" | "red" | "amber" | "off";

const TONE: Record<Tone, string> = {
  green: "text-green bg-green-soft border-green/35",
  cyan: "text-cyan bg-cyan-soft border-cyan/35",
  red: "text-red bg-red-soft border-red/40",
  amber: "text-amber bg-amber-soft border-amber/40",
  off: "text-muted bg-surface-2 border-line",
};

const STATUS: Record<string, Tone> = {
  completed: "green",
  running: "cyan",
  queued: "cyan",
  failed: "red",
  superseded: "off",
  skipped: "off",
  open: "green",
  merged: "cyan",
  closed: "off",
  addressed: "green",
  outdated: "off",
  valid: "green",
  invalid: "red",
  none: "off",
  unknown: "off",
};

/**
 * An annunciator: a small backlit cap with its state printed on it. Live states
 * (queued, running) carry the scope glyph, sweeping, so "in progress" reads at a glance.
 */
export function StatusPill({ status, label }: { status: string; label?: string }) {
  const tone = STATUS[status] ?? "off";
  const live = status === "running" || status === "queued";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-[3px] border px-1.5 py-[3px] font-mono text-[10px] leading-none font-bold tracking-[0.08em] whitespace-nowrap uppercase ${TONE[tone]}`}
    >
      {live ? <ScopeGlyph spinning className="size-2.5" /> : tone !== "off" ? <span aria-hidden className="size-1.5 rounded-full bg-current shadow-[0_0_6px_currentColor]" /> : null}
      {label ?? humanize(status)}
    </span>
  );
}

export function Sha({ sha }: { sha: string | null | undefined }) {
  return sha ? (
    <code className="rounded-[3px] border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-cyan">{sha.slice(0, 7)}</code>
  ) : null;
}
