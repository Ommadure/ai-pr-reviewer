import { useParams } from "react-router";
import { ApiError } from "../api/client";
import { usePull, useRereview } from "../api/hooks";
import { Sha, StatusPill } from "../components/Badges";
import { ScopeGlyph } from "../components/cockpit/RadarScope";
import { GutterList, GutterRow } from "../components/Gutter";
import { Empty, ErrorState, Loading, PageTitle } from "../components/States";
import { button } from "../components/ui";
import { ago, duration, humanize, money } from "../lib/format";

export function PullPage() {
  const id = Number(useParams().id);
  const pull = usePull(id);
  const rereview = useRereview(id);

  if (pull.isPending) return <Loading label="Loading pull request" />;
  if (pull.isError) return <ErrorState error={pull.error} onRetry={() => pull.refetch()} />;
  const pr = pull.data;
  const busy = pr.runs.some((r) => r.status === "queued" || r.status === "running");
  const rereviewError =
    rereview.error instanceof ApiError
      ? rereview.error.status === 429
        ? "Five manual reviews already ran on this pull request in the last hour. Try again later."
        : rereview.error.message
      : null;

  return (
    <>
      <PageTitle eyebrow={`${pr.repository_full_name} · #${pr.number}`} title={pr.title}>
        <a href={pr.html_url} target="_blank" rel="noreferrer" className={button.secondary}>
          Open on GitHub ↗
        </a>
        <button
          type="button"
          onClick={() => rereview.mutate()}
          disabled={pr.state !== "open" || busy || rereview.isPending}
          className={button.primary}
        >
          {busy ? <ScopeGlyph spinning className="size-4" /> : null}
          {busy ? "Review in progress…" : "Review again"}
        </button>
      </PageTitle>
      <p className="-mt-3 mb-7 flex flex-wrap items-center gap-2 text-sm text-muted">
        <StatusPill status={pr.state} /> by {pr.author_login} · head <Sha sha={pr.head_sha} />
        {pr.paused ? <span>· automatic reviews paused</span> : null}
      </p>
      {rereviewError ? (
        <p role="alert" className="mb-5 rounded-[6px] border border-red/40 bg-red-soft px-4 py-3 text-sm text-ink">{rereviewError}</p>
      ) : null}

      <h2 className="placard mb-3">Review runs · newest first</h2>
      {pr.runs.length === 0 ? (
        <Empty title="No reviews yet">This pull request hasn't been reviewed. Use “Review again” to start one.</Empty>
      ) : (
        <GutterList label="Review runs">
          {pr.runs.map((run) => (
            <GutterRow
              key={run.id}
              to={`/runs/${run.id}`}
              gutter={run.head_sha.slice(0, 7)}
              marker={run.status === "failed" ? "del" : run.status === "completed" ? "add" : "accent"}
              aside={<StatusPill status={run.status} />}
            >
              <span className="block text-sm font-medium">
                {humanize(run.trigger)} · {run.mode} review
              </span>
              <span className="mt-0.5 block text-xs text-muted">
                {run.status === "completed"
                  ? `${run.comments_posted} comment${run.comments_posted === 1 ? "" : "s"} posted · ${run.files_reviewed} file${run.files_reviewed === 1 ? "" : "s"} · ${duration(run.latency_ms)} · ${money(run.cost_usd)}`
                  : run.skip_reason
                    ? humanize(run.skip_reason)
                    : run.error_code
                      ? `error: ${humanize(run.error_code)}`
                      : "waiting for a worker"}
                {" · "}
                {ago(run.created_at)}
              </span>
            </GutterRow>
          ))}
        </GutterList>
      )}
    </>
  );
}
