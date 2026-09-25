import { animate, motion, useInView, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";
import { duration } from "../../lib/format";
import { EASE_OUT, SPRING } from "../../lib/motion";

/**
 * A number that rolls up to its value the first time it comes into view, like a
 * counter spinning up. Screen readers get the final value only (the rolling copy is
 * aria-hidden), so nothing is announced digit by digit.
 */
export function Readout({ value, format, className = "" }: { value: number; format: (n: number) => string; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const reduced = useReducedMotion();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (reduced || !inView) {
      el.textContent = format(reduced ? value : 0);
      return;
    }
    const controls = animate(0, value, {
      duration: 0.9,
      ease: EASE_OUT,
      onUpdate: (v) => {
        el.textContent = format(v);
      },
    });
    return () => controls.stop();
  }, [value, format, inView, reduced]);
  return (
    <span className={`tabular ${className}`}>
      <span ref={ref} aria-hidden />
      <span className="sr-only">{format(value)}</span>
    </span>
  );
}

const SWEEP = 240; // degrees of arc on the dial
const START = -120;
const polar = (deg: number, r: number) => {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [60 + r * Math.cos(rad), 60 + r * Math.sin(rad)] as const;
};
const arc = (from: number, to: number, r: number) => {
  const [x1, y1] = polar(from, r);
  const [x2, y2] = polar(to, r);
  return `M ${x1} ${y1} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
};

/**
 * A round gauge for a 0–1 rate. The needle is on a spring, so it swings to its value
 * and settles, the way a real instrument does. `null` parks the needle and says why.
 */
export function DialGauge({ value, label, children }: { value: number | null; label: string; children?: React.ReactNode }) {
  const ref = useRef<SVGSVGElement>(null);
  const inView = useInView(ref, { once: true });
  const v = value == null ? 0 : Math.max(0, Math.min(1, value));
  const angle = START + v * SWEEP;
  const ticks = Array.from({ length: 11 }, (_, i) => i);

  return (
    <figure className="flex flex-col items-center">
      <svg
        ref={ref}
        viewBox="0 0 120 104"
        className="w-full max-w-[13rem]"
        role="img"
        aria-label={`${label}: ${value == null ? "no data yet" : `${Math.round(v * 100)}%`}`}
      >
        <path d={arc(START, START + SWEEP, 50)} fill="none" stroke="var(--line)" strokeWidth="6" strokeLinecap="butt" />
        {value != null ? (
          <motion.path
            d={arc(START, START + SWEEP, 50)}
            fill="none"
            stroke="var(--green)"
            strokeWidth="6"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: inView ? v : 0 }}
            transition={{ duration: 0.9, ease: EASE_OUT }}
          />
        ) : null}
        {ticks.map((i) => {
          const deg = START + (i / 10) * SWEEP;
          const [x1, y1] = polar(deg, 41);
          const [x2, y2] = polar(deg, i % 5 === 0 ? 35 : 38);
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={i % 5 === 0 ? "var(--muted)" : "var(--faint)"} strokeWidth={i % 5 === 0 ? 1.1 : 0.7} />;
        })}
        {[0, 50, 100].map((n) => {
          const [x, y] = polar(START + (n / 100) * SWEEP, 27);
          return (
            <text key={n} x={x} y={y} textAnchor="middle" dominantBaseline="central" fontSize="7" fill="var(--faint)" style={{ fontFamily: "var(--font-mono)" }}>
              {n}
            </text>
          );
        })}
        <motion.g
          style={{ originX: 0.5, originY: 1 }} // pivot at the needle's base, the dial centre
          initial={{ rotate: START }}
          animate={{ rotate: inView ? angle : START }}
          transition={SPRING}
          opacity={value == null ? 0.35 : 1}
        >
          <path d="M 58.4 60 L 60 16 L 61.6 60 Z" fill="var(--route)" />
        </motion.g>
        <circle cx="60" cy="60" r="5" fill="var(--surface-3)" stroke="var(--line-strong)" strokeWidth="1" />
        <text x="60" y="88" textAnchor="middle" fontSize="15" fill="var(--ink)" style={{ fontFamily: "var(--font-mono)", fontWeight: 700 }}>
          {value == null ? "––" : `${Math.round(v * 100)}%`}
        </text>
      </svg>
      <figcaption className="-mt-1 text-center">
        <span className="placard">{label}</span>
        {children ? <span className="mt-1.5 block text-xs text-muted">{children}</span> : null}
      </figcaption>
    </figure>
  );
}

/**
 * A horizontal tape, like an airspeed tape laid flat: where the average and the p95
 * review times sit on one scale, so the gap between them (the slow tail) is visible.
 */
export function LatencyTape({ avg, p95 }: { avg: number | null; p95: number | null }) {
  const top = Math.max(60_000, Math.ceil(((p95 ?? avg ?? 0) * 1.25) / 30_000) * 30_000);
  const pos = (ms: number | null) => (ms == null ? null : Math.min(100, (ms / top) * 100));
  const marks = Array.from({ length: 7 }, (_, i) => (top / 6) * i);
  const markers = [
    // labels lean away from each other (avg to the left, p95 to the right) so close values never collide
    { key: "avg", at: pos(avg), label: `avg ${duration(avg)}`, color: "var(--cyan)", lean: "items-end -translate-x-full" },
    { key: "p95", at: pos(p95), label: `p95 ${duration(p95)}`, color: "var(--route)", lean: "items-start" },
  ];
  return (
    <figure aria-label={`Review time: average ${duration(avg)}, 95th percentile ${duration(p95)}`}>
      <div className="relative h-14" aria-hidden>
        <div className="absolute inset-x-0 top-6 h-2 rounded-sm border border-line bg-surface-2" />
        {avg != null && p95 != null ? (
          <motion.div
            className="absolute top-6 h-2 origin-left bg-[linear-gradient(90deg,var(--cyan),var(--route))] opacity-40"
            style={{ left: `${pos(avg)}%`, width: `${(pos(p95) ?? 0) - (pos(avg) ?? 0)}%` }}
            initial={{ scaleX: 0 }}
            whileInView={{ scaleX: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.7, ease: EASE_OUT, delay: 0.3 }}
          />
        ) : null}
        {marks.map((ms, i) => (
          <span key={i} className="absolute top-9 -translate-x-1/2 font-mono text-[10px] text-faint" style={{ left: `${(ms / top) * 100}%` }}>
            <span className="mx-auto mb-0.5 block h-1.5 w-px bg-line-strong" />
            {Math.round(ms / 1000)}s
          </span>
        ))}
        {markers.map((m) =>
          m.at == null ? null : (
            <motion.span
              key={m.key}
              className={`absolute top-0 flex flex-col ${m.lean}`}
              style={{ left: `${m.at}%` }}
              initial={{ opacity: 0, y: -6 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ ...SPRING, delay: m.key === "p95" ? 0.15 : 0 }}
            >
              <span className="font-mono text-[10px] font-bold whitespace-nowrap" style={{ color: m.color }}>
                {m.label}
              </span>
              <span className="h-4 w-0.5" style={{ background: m.color }} />
            </motion.span>
          ),
        )}
      </div>
    </figure>
  );
}
