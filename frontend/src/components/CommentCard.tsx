import type { Comment } from "../api/types";
import { humanize } from "../lib/format";
import { SeverityBadge, StatusPill } from "./Badges";
import { InlineCode } from "./InlineCode";

const EDGE: Record<string, string> = {
  critical: "border-l-red",
  high: "border-l-amber",
  medium: "border-l-amber/60",
  low: "border-l-cyan",
};

/** One review comment, drawn the way it sits on GitHub: attached to its lines. */
export function CommentCard({ comment }: { comment: Comment }) {
  const lines = comment.start_line ? `L${comment.start_line}–${comment.line}` : `L${comment.line}`;
  return (
    <article className="group flex border-b border-line last:border-b-0" aria-label={comment.title}>
      <span className="w-18 shrink-0 border-r border-dashed border-line-strong bg-surface-2 px-2.5 py-4 text-right font-mono text-xs font-bold text-route tabular sm:w-22">
        {lines}
      </span>
      <div className={`min-w-0 flex-1 border-l-2 bg-surface px-5 py-4 ${EDGE[comment.severity] ?? "border-l-line-strong"}`}>
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={comment.severity} />
          <span className="font-mono text-[11px] text-muted">{humanize(comment.category)}</span>
          {comment.source === "secret_scanner" ? <span className="font-mono text-[11px] text-muted">· secret scanner</span> : null}
          {comment.posted && comment.status !== "open" ? <StatusPill status={comment.status} /> : null}
        </div>
        <h3 className="mt-2 font-display text-[17px] leading-snug font-semibold text-ink">
          <InlineCode text={comment.title} />
        </h3>
        {comment.body ? (
          <p className="mt-1.5 max-w-3xl text-sm leading-relaxed whitespace-pre-wrap text-muted">
            <InlineCode text={comment.body} />
          </p>
        ) : null}
        {comment.suggestion ? (
          <figure className="mt-3 max-w-3xl overflow-hidden rounded-[5px] border border-green/30">
            <figcaption className="placard flex items-center gap-2 border-b border-green/30 bg-green-soft px-3 py-1.5 !text-[10px] !text-green">
              <span aria-hidden>+</span> suggested change
            </figcaption>
            <pre className="overflow-x-auto bg-surface-2 px-3 py-2.5 font-mono text-xs leading-relaxed text-ink">{comment.suggestion}</pre>
          </figure>
        ) : null}
        <footer className="mt-3.5 flex flex-wrap items-center gap-4 font-mono text-[11px] text-muted">
          {comment.posted ? (
            <>
              <span aria-label={`${comment.thumbs_up} thumbs up, ${comment.thumbs_down} thumbs down`}>
                👍 {comment.thumbs_up} · 👎 {comment.thumbs_down}
              </span>
              {comment.confidence != null ? (
                <span className="flex items-center gap-1.5" title="How sure the model was">
                  conf
                  <span aria-hidden className="h-1 w-10 overflow-hidden rounded-full bg-surface-3">
                    <span className="block h-full bg-cyan" style={{ width: `${Math.round(comment.confidence * 100)}%` }} />
                  </span>
                  {comment.confidence.toFixed(2)}
                </span>
              ) : null}
              {comment.github_url ? (
                <a href={comment.github_url} target="_blank" rel="noreferrer" className="font-sans text-xs font-medium text-cyan hover:underline">
                  View on GitHub ↗
                </a>
              ) : null}
            </>
          ) : (
            <span>not posted: {humanize(comment.drop_reason ?? "unknown")}</span>
          )}
        </footer>
      </div>
    </article>
  );
}
