import type { Comment } from "../api/types";
import { humanize } from "../lib/format";
import { SeverityBadge, StatusPill } from "./Badges";
import { InlineCode } from "./InlineCode";

/** One review comment, drawn the way it sits on GitHub: attached to its lines. */
export function CommentCard({ comment }: { comment: Comment }) {
  const lines = comment.start_line ? `L${comment.start_line}–${comment.line}` : `L${comment.line}`;
  return (
    <article className="flex border-b border-line last:border-b-0" aria-label={comment.title}>
      <span className="w-16 shrink-0 bg-gutter px-2 py-3 text-right font-mono text-xs text-muted tabular sm:w-20">
        {lines}
      </span>
      <div className="min-w-0 flex-1 border-l-2 border-accent bg-surface px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={comment.severity} />
          <span className="text-xs text-muted">{humanize(comment.category)}</span>
          {comment.source === "secret_scanner" ? (
            <span className="text-xs text-muted">· secret scanner</span>
          ) : null}
          {comment.posted && comment.status !== "open" ? <StatusPill status={comment.status} /> : null}
        </div>
        <h3 className="mt-1.5 font-medium text-ink">
          <InlineCode text={comment.title} />
        </h3>
        {comment.body ? (
          <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-muted">
            <InlineCode text={comment.body} />
          </p>
        ) : null}
        {comment.suggestion ? (
          <figure className="mt-3 overflow-hidden rounded-md border border-line">
            <figcaption className="bg-surface-2 px-3 py-1 font-mono text-[11px] text-muted">suggested change</figcaption>
            <pre className="overflow-x-auto bg-add px-3 py-2 font-mono text-xs text-ink">{comment.suggestion}</pre>
          </figure>
        ) : null}
        <footer className="mt-3 flex flex-wrap items-center gap-4 text-xs text-muted">
          {comment.posted ? (
            <>
              <span aria-label={`${comment.thumbs_up} thumbs up, ${comment.thumbs_down} thumbs down`}>
                👍 {comment.thumbs_up} · 👎 {comment.thumbs_down}
              </span>
              {comment.confidence != null ? <span>confidence {comment.confidence.toFixed(2)}</span> : null}
              {comment.github_url ? (
                <a href={comment.github_url} target="_blank" rel="noreferrer" className="font-medium text-accent hover:underline">
                  View on GitHub ↗
                </a>
              ) : null}
            </>
          ) : (
            <span className="font-mono">not posted: {humanize(comment.drop_reason ?? "unknown")}</span>
          )}
        </footer>
      </div>
    </article>
  );
}
