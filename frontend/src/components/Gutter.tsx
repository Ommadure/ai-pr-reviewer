import { motion } from "motion/react";
import { Children, type ReactNode } from "react";
import { Link } from "react-router";
import { row } from "../lib/motion";

type Marker = "add" | "del" | "accent" | "none";

const BAND: Record<Marker, string> = {
  add: "bg-green",
  del: "bg-red",
  accent: "bg-cyan",
  none: "bg-line-strong",
};

/**
 * A flight strip, as air-traffic controllers use to track each aircraft: a coloured
 * band for its state, a boxed callsign (PR number, commit, open count), then the
 * details. On hover the strip lifts toward you and its band lights up.
 */
export function GutterRow({
  gutter,
  marker = "none",
  to,
  children,
  aside,
}: {
  gutter: ReactNode;
  marker?: Marker;
  to?: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  const body = (
    <>
      <span aria-hidden className={`w-1 self-stretch transition-[width,box-shadow] duration-150 group-hover:w-1.5 ${BAND[marker]}`} />
      <span className="flex w-18 shrink-0 items-center justify-end border-r border-dashed border-line-strong bg-surface-2 px-2.5 py-3 font-mono text-xs font-bold text-ink tabular sm:w-22">
        {gutter}
      </span>
      <span className="min-w-0 flex-1 px-4 py-3">{children}</span>
      {aside ? <span className="flex shrink-0 items-center gap-3 pr-4">{aside}</span> : null}
    </>
  );
  const className = "group flex items-stretch bg-surface transition-colors duration-150";
  return to ? (
    <Link to={to} className={`${className} hover:bg-surface-3`}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

/** The strip board: strips slide in one after another, top to bottom. Each child is one list item. */
export function GutterList({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="list" aria-label={label} className="space-y-1.5">
      {Children.map(children, (child, i) => (
        <motion.div role="listitem" variants={row} custom={i} initial="hidden" animate="show" className="overflow-hidden rounded-[5px] border border-line shadow-panel transition-[border-color] duration-150 hover:border-line-strong">
          {child}
        </motion.div>
      ))}
    </div>
  );
}
