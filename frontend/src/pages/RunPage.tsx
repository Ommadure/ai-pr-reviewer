import { Link, useParams } from "react-router";
import { useRun } from "../api/hooks";
import type { Comment } from "../api/types";
import { Sha, StatusPill } from "../components/Badges";
import { CommentCard } from "../components/CommentCard";
import { InlineCode } from "../components/InlineCode";
import { ErrorState, Loading, PageTitle } from "../components/States";
import { button } from "../components/ui";
import { count, duration, humanize, money } from "../lib/format";

function byFile(comments: Comment[]): [string, Comment[]][] {
  const files = new Map<string, Comment[]>();
  for (const c of comments) files.set(c.path, [...(files.get(c.path) ?? []), c]);
  return [...files.entries()];
}

export function RunPage() {
  const id = Number(useParams().id);
  const run = useRun(id);
  if (run.isPending) return <Loading label="Loading review run" />;
  if (run.isError) return <ErrorState error={run.error} onRetry={() => run.refetch()} />;
  const r = run.data;
  const posted = r.comments.filter((c) => c.posted || c.drop_reason === "github_rejected" || c.drop_reason === "not_posted");
  const dropped = r.comments.filter((c) => !posted.includes(c));
  const summary = r.summary as { overview?: string; risk_level?: string; key_changes?: string[]; notes?: string[] } | null;

  const facts = [
    ["files", `${r.files_reviewed} of ${r.files_total}`],
    ["comments", `${r.comments_posted} posted · ${r.comments_generated} generated`],
    ["tokens", `${count(r.input_tokens)} in · ${count(r.output_tokens)} out`],
    ["cost", money(r.cost_usd)],
    ["time", duration(r.latency_ms)],
    ["model", `${r.model ?? "–"} · prompt ${r.prompt_version ?? "–"}`],
  ];

  return (
    <>
      <PageTitle
        eyebrow={
          <>
            <Link to={`/pulls/${r.pull_request_id}`} className="hover:text-cyan">
              {r.repository_full_name} #{r.pull_request_number}
            </Link>{" "}
            · run {r.id}
          </>
        }
        title={r.pull_request_title}
      >
        {r.github_review_url ? (
          <a href={r.github_review_url} target="_blank" rel="noreferrer" className={button.secondary}>
            Review on GitHub ↗
          </a>
        ) : null}
      </PageTitle>
      <p className="-mt-3 mb-5 flex flex-wrap items-center gap-2 text-sm text-muted">
        <StatusPill status={r.status} /> {humanize(r.trigger)} · {r.mode} review of <Sha sha={r.head_sha} />
        {r.from_sha ? <>since <Sha sha={r.from_sha} /></> : null}
        {r.mode_reason ? <span>({humanize(r.mode_reason)})</span> : null}
        {r.skip_reason ? <span>· {humanize(r.skip_reason)}</span> : null}
      </p>
      {r.error_message ? (
        <p role="alert" className="mb-5 rounded-[6px] border border-red/40 bg-red-soft px-4 py-3 text-sm text-ink">
          {r.error_code ? <strong className="font-mono text-red">{r.error_code}: </strong> : null}
          {r.error_message}
        </p>
      ) : null}

      {/* a flight-computer page: fixed label/value pairs on one dark panel */}
      <dl className="mb-6 grid grid-cols-1 gap-px overflow-hidden rounded-[8px] border border-line bg-line shadow-panel sm:grid-cols-2 lg:grid-cols-3">
        {facts.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-4 bg-surface px-4 py-3">
            <dt className="placard !text-[10px]">{label}</dt>
            <dd className="text-right font-mono text-[13px] text-cyan tabular">{value}</dd>
          </div>
        ))}
      </dl>

      {summary?.overview ? (
        <section className="mb-8 rounded-[8px] border border-line border-l-2 border-l-route bg-surface p-5 shadow-panel" aria-label="Summary">
          <p className="placard flex flex-wrap items-center gap-3">
            Summary
            {summary.risk_level ? <RiskLight level={summary.risk_level} /> : null}
          </p>
          <p className="mt-3 max-w-3xl leading-relaxed">
            <InlineCode text={summary.overview} />
          </p>
          {summary.key_changes?.length ? (
            <ul className="mt-3 max-w-3xl list-[square] space-y-1 pl-5 text-sm text-muted marker:text-route">
              {summary.key_changes.map((k) => (
                <li key={k}>
                  <InlineCode text={k} />
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      <h2 className="placard mb-3">Comments ({posted.length})</h2>
      {posted.length === 0 ? (
        <p className="mb-8 rounded-[8px] border border-dashed border-line-strong bg-surface/60 px-4 py-4 text-sm text-muted">
          No comments in this run: nothing on the changed lines needed one.
        </p>
      ) : (
        <div className="mb-8 space-y-4">
          {byFile(posted).map(([path, comments]) => (
            <section key={path} className="overflow-hidden rounded-[8px] border border-line shadow-panel" aria-label={path}>
              <h3 className="flex items-center justify-between gap-3 border-b border-line bg-surface-2 px-4 py-2.5 font-mono text-xs">
                <span className="truncate text-ink">{path}</span>
                <span className="shrink-0 text-faint">{comments.length}</span>
              </h3>
              {comments.map((c) => (
                <CommentCard key={c.id} comment={c} />
              ))}
            </section>
          ))}
        </div>
      )}

      {dropped.length ? (
        <details className="mb-4 rounded-[8px] border border-line bg-surface">
          <summary className="cursor-pointer px-4 py-3 text-sm hover:text-cyan">
            {dropped.length} dropped by validation <span className="text-muted">(never posted: why each one failed)</span>
          </summary>
          <table className="w-full text-sm">
            <tbody>
              {dropped.map((c) => (
                <tr key={c.id} className="border-t border-line">
                  <td className="px-4 py-2 font-mono text-xs text-muted">{c.path}:{c.line}</td>
                  <td className="px-2 py-2">{c.title}</td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-red">{humanize(c.drop_reason ?? "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}

      {r.files_skipped.length ? (
        <details className="mb-8 rounded-[8px] border border-line bg-surface">
          <summary className="cursor-pointer px-4 py-3 text-sm hover:text-cyan">
            {r.files_skipped.length} file{r.files_skipped.length === 1 ? "" : "s"} skipped
          </summary>
          <ul className="border-t border-line px-4 py-2 text-sm">
            {r.files_skipped.map((f) => (
              <li key={f.path} className="flex justify-between gap-4 py-1">
                <span className="truncate font-mono text-xs">{f.path}</span>
                <span className="shrink-0 text-xs text-muted">{humanize(f.reason)}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <h2 className="placard mb-3">LLM calls</h2>
      <div className="overflow-x-auto rounded-[8px] border border-line bg-surface shadow-panel">
        <table className="w-full min-w-[36rem] text-sm">
          <thead>
            <tr className="border-b border-line bg-surface-2 text-left [&>th]:placard [&>th]:!text-[10px] [&>th]:font-normal">
              <th className="px-4 py-2.5">purpose</th>
              <th className="px-2 py-2.5">model</th>
              <th className="px-2 py-2.5 text-right">tokens in / out</th>
              <th className="px-2 py-2.5 text-right">cost</th>
              <th className="px-2 py-2.5 text-right">time</th>
              <th className="px-4 py-2.5">status</th>
            </tr>
          </thead>
          <tbody>
            {r.llm_calls.map((c) => (
              <tr key={c.id} className="border-t border-line first:border-t-0 hover:bg-surface-2">
                <td className="px-4 py-2">{humanize(c.purpose)}</td>
                <td className="px-2 py-2 font-mono text-xs">{c.model}</td>
                <td className="px-2 py-2 text-right font-mono text-xs tabular">{count(c.input_tokens)} / {count(c.output_tokens)}</td>
                <td className="px-2 py-2 text-right font-mono text-xs tabular">{money(c.cost_usd)}</td>
                <td className="px-2 py-2 text-right font-mono text-xs tabular">{duration(c.latency_ms)}</td>
                <td className="px-4 py-2">
                  <StatusPill status={c.status === "success" ? "completed" : "failed"} label={c.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

const RISK: Record<string, string> = {
  high: "border-red/40 bg-red-soft text-red",
  medium: "border-amber/40 bg-amber-soft text-amber",
  low: "border-green/35 bg-green-soft text-green",
};

/** The model's overall risk call, as an annunciator: WARNING, CAUTION or normal. */
function RiskLight({ level }: { level: string }) {
  return (
    <span className={`rounded-[3px] border px-1.5 py-[3px] font-mono text-[10px] leading-none font-bold tracking-[0.08em] ${RISK[level] ?? "border-line bg-surface-2 text-muted"}`}>
      RISK {level.toUpperCase()}
    </span>
  );
}
