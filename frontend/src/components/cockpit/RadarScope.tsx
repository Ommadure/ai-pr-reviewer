import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { Severity } from "../../api/types";

export type Contact = {
  id: string;
  /** Bearing in degrees, clockwise from north (the top). */
  bearing: number;
  /** Distance from the centre, 0–1. */
  range: number;
  severity: Severity;
  label: string;
};

const TONE: Record<Severity, string> = {
  critical: "var(--red)",
  high: "var(--amber)",
  medium: "var(--amber)",
  low: "var(--cyan)",
  info: "var(--muted)",
};

const TICKS = Array.from({ length: 36 }, (_, i) => i * 10);
const HEADINGS = [
  { deg: 0, text: "000" },
  { deg: 90, text: "090" },
  { deg: 180, text: "180" },
  { deg: 270, text: "270" },
];

/** Pause the sweep while the scope is off screen or the tab is hidden: it's decoration then. */
function useVisible<T extends Element>() {
  const ref = useRef<T>(null);
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry?.isIntersecting ?? true));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, visible] as const;
}

const polar = (bearing: number, range: number) => {
  const rad = (bearing * Math.PI) / 180;
  return { x: 50 + Math.sin(rad) * range * 44, y: 50 - Math.cos(rad) * range * 44 };
};

/**
 * The signature instrument: a radar scope whose sweep is the reviewer reading the diff.
 * Each finding is a contact that lights up as the sweep passes its bearing, then decays.
 * Contacts are real buttons, so the scope works with a keyboard and a screen reader.
 */
export function RadarScope({
  contacts = [],
  activeId,
  onSelect,
  period = 4,
  paused = false,
  label,
  className = "",
}: {
  contacts?: Contact[];
  activeId?: string;
  onSelect?: (id: string) => void;
  period?: number;
  /** Hold the sweep (a HOLD control, or reduced motion). */
  paused?: boolean;
  label: string;
  className?: string;
}) {
  const [ref, visible] = useVisible<HTMLDivElement>();
  return (
    <div
      ref={ref}
      role="group"
      aria-label={label}
      data-paused={visible && !paused ? undefined : ""}
      className={`scope relative aspect-square rounded-full ${className}`}
      style={{ "--period": `${period}s` } as CSSProperties}
    >
      {/* bezel, range rings, crosshair and graduations */}
      <svg viewBox="0 0 100 100" className="absolute inset-0 size-full" aria-hidden>
        <circle cx="50" cy="50" r="49.5" fill="var(--surface)" stroke="var(--line-strong)" strokeWidth="0.4" />
        <circle cx="50" cy="50" r="44" fill="none" stroke="var(--line)" strokeWidth="0.3" />
        <circle cx="50" cy="50" r="29.3" fill="none" stroke="var(--line)" strokeWidth="0.25" strokeDasharray="0.6 1.2" />
        <circle cx="50" cy="50" r="14.7" fill="none" stroke="var(--line)" strokeWidth="0.25" strokeDasharray="0.6 1.2" />
        <line x1="50" y1="6" x2="50" y2="94" stroke="var(--line)" strokeWidth="0.2" />
        <line x1="6" y1="50" x2="94" y2="50" stroke="var(--line)" strokeWidth="0.2" />
        {TICKS.map((deg) => {
          const major = deg % 30 === 0;
          const a = polar(deg, 1);
          const b = polar(deg, major ? 0.95 : 0.975);
          return (
            <line
              key={deg}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={major ? "var(--muted)" : "var(--faint)"}
              strokeWidth={major ? 0.35 : 0.2}
            />
          );
        })}
        {HEADINGS.map(({ deg, text }) => {
          const p = polar(deg, 1.075);
          return (
            <text
              key={deg}
              x={p.x}
              y={p.y}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize="2.6"
              fill="var(--faint)"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              {text}
            </text>
          );
        })}
      </svg>

      {/* the sweep: a fading wedge with a bright leading edge */}
      <div aria-hidden className="scope-sweep absolute inset-[6%] rounded-full">
        <span className="absolute top-0 left-1/2 h-1/2 w-px -translate-x-1/2 bg-route opacity-80" />
      </div>

      {/* own ship */}
      <svg viewBox="0 0 10 10" aria-hidden className="absolute top-1/2 left-1/2 size-[5%] -translate-x-1/2 -translate-y-1/2">
        <path d="M5 1 L8.5 9 L5 7 L1.5 9 Z" fill="var(--route)" />
      </svg>

      {contacts.map((c) => {
        const p = polar(c.bearing, c.range);
        const active = c.id === activeId;
        return (
          <button
            key={c.id}
            type="button"
            aria-label={c.label}
            aria-pressed={onSelect ? active : undefined}
            onClick={() => onSelect?.(c.id)}
            className="group absolute grid size-9 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full"
            style={{ left: `${p.x}%`, top: `${p.y}%`, color: TONE[c.severity] }}
          >
            <span
              aria-hidden
              className="contact-ping block size-2.5 rounded-full bg-current"
              style={{ "--delay": `${(c.bearing / 360) * period - period}s` } as CSSProperties}
            />
            <span
              aria-hidden
              className={`absolute inset-1.5 rounded-full border border-current transition-[opacity,transform] duration-200 ${
                active ? "scale-100 opacity-100" : "scale-50 opacity-0 group-hover:scale-90 group-hover:opacity-60"
              }`}
            />
          </button>
        );
      })}
    </div>
  );
}

/** The scope at glyph size: the logo mark, and "a review is running" wherever status shows. */
export function ScopeGlyph({ spinning = false, className = "size-4" }: { spinning?: boolean; className?: string }) {
  return (
    <span aria-hidden className={`relative inline-block shrink-0 ${className}`}>
      <svg viewBox="0 0 16 16" className="absolute inset-0 size-full">
        <circle cx="8" cy="8" r="7.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <circle cx="8" cy="8" r="3.4" fill="none" stroke="currentColor" strokeWidth="0.9" opacity="0.5" />
        <circle cx="8" cy="8" r="1.1" fill="currentColor" />
      </svg>
      <span
        className={`absolute inset-[9%] rounded-full ${spinning ? "animate-sweep" : ""}`}
        style={{ background: "conic-gradient(from 0deg, transparent 0 270deg, var(--route) 360deg)", opacity: 0.85 }}
      />
    </span>
  );
}
