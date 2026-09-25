import { ScopeGlyph } from "./cockpit/RadarScope";

/** Wordmark: the scope glyph plus the name, "Pilot" in the route colour. */
export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`group inline-flex items-center gap-2 font-display text-[17px] font-bold tracking-tight ${className}`}>
      <ScopeGlyph className="size-5 text-ink transition-transform duration-500 group-hover:rotate-90" />
      <span>
        Review<span className="text-route">Pilot</span>
      </span>
    </span>
  );
}
