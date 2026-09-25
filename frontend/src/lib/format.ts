export const shortSha = (sha: string | null | undefined): string => (sha ? sha.slice(0, 7) : "");

export function money(usd: number, inrRate?: number | null): string {
  if (inrRate) return `₹${(usd * inrRate).toFixed(usd * inrRate < 10 ? 2 : 0)}`;
  return `$${usd.toFixed(usd < 1 ? 4 : 2)}`;
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  return seconds < 60 ? `${seconds.toFixed(1)} s` : `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
}

export function percent(rate: number | null | undefined): string {
  return rate == null ? "–" : `${Math.round(rate * 100)}%`;
}

export const count = (n: number): string => new Intl.NumberFormat("en").format(n);

const RELATIVE = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

export function ago(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const seconds = (new Date(iso).getTime() - now.getTime()) / 1000;
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return RELATIVE.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

/** "drop_reason" → "drop reason" */
export const humanize = (value: string): string => value.replaceAll("_", " ");
