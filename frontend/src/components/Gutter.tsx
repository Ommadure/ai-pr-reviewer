import type { ReactNode } from "react";
import { Link } from "react-router";

type Marker = "add" | "del" | "accent" | "none";

const MARKER: Record<Marker, string> = {
  add: "bg-add-ink",
  del: "bg-del-ink",
  accent: "bg-accent",
  none: "bg-transparent",
};

/**
 * The signature element: a row laid out like a diff line. The mono gutter on the
 * left carries a real index (PR number, line number, commit), and the thin marker
 * says what kind of row it is, the way + / − do in a diff.
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
      <span aria-hidden className={`w-0.5 self-stretch ${MARKER[marker]}`} />
      <span className="w-16 shrink-0 bg-gutter px-2 py-3 text-right font-mono text-xs text-muted tabular sm:w-20">
        {gutter}
      </span>
      <span className="min-w-0 flex-1 px-4 py-3">{children}</span>
      {aside ? <span className="flex shrink-0 items-center gap-3 pr-4">{aside}</span> : null}
    </>
  );
  const className =
    "flex items-stretch border-b border-line last:border-b-0 bg-surface transition-colors";
  return to ? (
    <Link to={to} className={`${className} hover:bg-surface-2`}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

export function GutterList({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="list" aria-label={label} className="overflow-hidden rounded-lg border border-line">
      {children}
    </div>
  );
}
