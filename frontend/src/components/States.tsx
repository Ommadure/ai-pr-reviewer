import { motion } from "motion/react";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import { rise } from "../lib/motion";
import { ScopeGlyph } from "./cockpit/RadarScope";
import { button } from "./ui";

/** Skeleton rows shaped like the strips that are coming, with a scan passing over them. */
export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div key={i} className="relative h-14 overflow-hidden rounded-[6px] border border-line bg-surface" style={{ opacity: 1 - i * 0.25 }}>
          <div className="absolute inset-y-0 left-0 w-16 border-r border-line bg-surface-2" />
          <div className="absolute top-4 left-20 h-2.5 w-1/3 rounded-sm bg-surface-3" />
          <div className="absolute top-8 left-20 h-2 w-1/5 rounded-sm bg-surface-2" />
          <div
            className="absolute inset-0 bg-[linear-gradient(90deg,transparent,var(--route-soft),transparent)]"
            style={{ animation: `scan 1.6s ${i * 120}ms ease-in-out infinite` }}
          />
        </div>
      ))}
    </div>
  );
}

/** Nothing here yet: an empty scope, and what to do about it. */
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <motion.div variants={rise} initial="hidden" animate="show" className="flex flex-col items-center rounded-[8px] border border-dashed border-line-strong bg-surface/60 px-6 py-12 text-center">
      <ScopeGlyph spinning className="size-12 text-line-strong" />
      <p className="mt-4 font-display text-lg font-semibold text-ink">{title}</p>
      {children ? <div className="mx-auto mt-2 max-w-md text-sm text-muted">{children}</div> : null}
    </motion.div>
  );
}

/** A master caution: what went wrong, in plain words, and the way to recover. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const message =
    status === 404
      ? "This doesn't exist, or it belongs to an account you can't access."
      : status === 503
        ? "GitHub didn't answer, so your access couldn't be checked. Try again in a moment."
        : "The dashboard couldn't load this. Check that the API is running, then try again.";
  return (
    <div role="alert" className="flex flex-wrap items-center gap-4 rounded-[6px] border border-red/40 bg-red-soft px-4 py-3">
      <span className="rounded-[3px] bg-red px-2 py-1 font-mono text-[10px] font-bold tracking-[0.1em] text-on-route" style={{ animation: "blink 0.8s steps(2, jump-none) 3" }}>
        {status === 404 ? "NOT FOUND" : "CAUTION"}
      </span>
      <p className="min-w-0 flex-1 text-sm font-medium text-ink">{message}</p>
      {onRetry && status !== 404 ? (
        <button type="button" onClick={onRetry} className={button.secondary}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function PageTitle({ eyebrow, title, children }: { eyebrow?: ReactNode; title: ReactNode; children?: ReactNode }) {
  return (
    <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow ? (
          <p className="placard flex items-center gap-2">
            <span aria-hidden className="h-px w-5 bg-cyan" />
            {eyebrow}
          </p>
        ) : null}
        <h1 className="mt-2 font-display text-[28px] leading-tight font-bold tracking-tight text-ink sm:text-[34px]">{title}</h1>
      </div>
      {children ? <div className="flex flex-wrap items-center gap-2">{children}</div> : null}
    </header>
  );
}
