import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-12 animate-pulse rounded-lg bg-gutter motion-reduce:animate-none" />
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface px-6 py-10 text-center">
      <p className="font-display text-lg font-semibold text-ink">{title}</p>
      {children ? <div className="mx-auto mt-2 max-w-md text-sm text-muted">{children}</div> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const message =
    status === 404
      ? "This doesn't exist, or it belongs to an account you can't access."
      : status === 503
        ? "GitHub didn't answer, so your access couldn't be checked. Try again in a moment."
        : "The dashboard couldn't load this. Check that the API is running, then try again.";
  return (
    <div role="alert" className="rounded-lg border border-del-ink/30 bg-del px-5 py-4 text-sm text-del-ink">
      <p className="font-medium">{message}</p>
      {onRetry && status !== 404 ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md border border-del-ink/40 px-3 py-1 text-xs font-medium hover:bg-surface"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function PageTitle({ eyebrow, title, children }: { eyebrow?: ReactNode; title: ReactNode; children?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow ? <p className="font-mono text-xs text-muted">{eyebrow}</p> : null}
        <h1 className="mt-1 font-display text-2xl font-bold tracking-tight text-ink sm:text-3xl">{title}</h1>
      </div>
      {children ? <div className="flex items-center gap-2">{children}</div> : null}
    </header>
  );
}
