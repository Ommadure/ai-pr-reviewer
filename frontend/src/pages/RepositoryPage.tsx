import { motion } from "motion/react";
import { useParams, useSearchParams } from "react-router";
import { useAnalytics, usePulls, useRepositories, useRepositoryConfig } from "../api/hooks";
import { StatusPill } from "../components/Badges";
import { Charts } from "../components/LazyAnalytics";
import { GutterList, GutterRow } from "../components/Gutter";
import { Empty, ErrorState, Loading, PageTitle } from "../components/States";
import { button, field } from "../components/ui";
import { ago, humanize } from "../lib/format";
import { SPRING } from "../lib/motion";

const TABS = [
  { id: "pulls", label: "Pull requests" },
  { id: "config", label: "Config" },
  { id: "stats", label: "Stats" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export function RepositoryPage() {
  const id = Number(useParams().id);
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "pulls") as Tab;
  const repositories = useRepositories();
  const repo = repositories.data?.find((r) => r.id === id);

  if (repositories.isPending) return <Loading label="Loading repository" />;
  if (repositories.isError) return <ErrorState error={repositories.error} />;
  if (!repo) return <Empty title="Repository not found">It may have been removed from ReviewPilot, or you can't access it.</Empty>;

  return (
    <>
      <PageTitle eyebrow={repo.enabled ? "automatic reviews on" : "automatic reviews off"} title={<span className="font-mono text-[0.8em] tracking-tight">{repo.full_name}</span>} />
      <div role="tablist" aria-label="Repository sections" className="mb-6 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            onClick={() => setParams({ tab: t.id }, { replace: true })}
            className={`relative -mb-px min-h-10 px-3 text-sm transition-colors duration-150 ${
              tab === t.id ? "font-medium text-ink" : "text-muted hover:text-ink"
            }`}
          >
            {t.label}
            {tab === t.id ? (
              <motion.span layoutId="repo-tab" transition={SPRING} className="absolute inset-x-2 -bottom-px h-0.5 bg-route" />
            ) : null}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "pulls" ? <PullsTab repositoryId={id} /> : null}
        {tab === "config" ? <ConfigTab repositoryId={id} /> : null}
        {tab === "stats" ? <StatsTab repositoryId={id} /> : null}
      </div>
    </>
  );
}

function PullsTab({ repositoryId }: { repositoryId: number }) {
  const [params, setParams] = useSearchParams();
  const state = params.get("state") ?? "open";
  const pulls = usePulls(repositoryId, state);
  const items = pulls.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <label className="mb-4 flex items-center gap-2 text-sm text-muted">
        Show
        <select
          value={state}
          onChange={(e) => setParams({ tab: "pulls", state: e.target.value }, { replace: true })}
          className={field}
        >
          <option value="open">open</option>
          <option value="merged">merged</option>
          <option value="closed">closed</option>
          <option value="all">all</option>
        </select>
        pull requests
      </label>
      {pulls.isPending ? (
        <Loading label="Loading pull requests" />
      ) : pulls.isError ? (
        <ErrorState error={pulls.error} onRetry={() => pulls.refetch()} />
      ) : items.length === 0 ? (
        <Empty title={`No ${state === "all" ? "" : state + " "}pull requests`}>
          ReviewPilot lists a pull request here once GitHub tells it about one.
        </Empty>
      ) : (
        <>
          <GutterList label="Pull requests">
            {items.map((pr) => (
              <GutterRow
                key={pr.id}
                to={`/pulls/${pr.id}`}
                gutter={`#${pr.number}`}
                marker={pr.last_run?.status === "failed" ? "del" : pr.last_run ? "accent" : "none"}
                aside={pr.last_run ? <StatusPill status={pr.last_run.status} /> : <StatusPill status="none" label="not reviewed" />}
              >
                <span className="block truncate font-medium">{pr.title}</span>
                <span className="mt-0.5 block text-xs text-muted">
                  {pr.author_login} · {humanize(pr.state)}
                  {pr.draft ? " · draft" : ""}
                  {pr.paused ? " · reviews paused" : ""} · updated {ago(pr.updated_at)}
                </span>
              </GutterRow>
            ))}
          </GutterList>
          {pulls.hasNextPage ? (
            <button
              type="button"
              onClick={() => pulls.fetchNextPage()}
              disabled={pulls.isFetchingNextPage}
              className={`${button.secondary} mt-4`}
            >
              {pulls.isFetchingNextPage ? "Loading…" : "Load more"}
            </button>
          ) : null}
        </>
      )}
    </>
  );
}

function ConfigTab({ repositoryId }: { repositoryId: number }) {
  const config = useRepositoryConfig(repositoryId);
  if (config.isPending) return <Loading label="Loading configuration" />;
  if (config.isError) return <ErrorState error={config.error} onRetry={() => config.refetch()} />;
  if (!config.data) {
    return (
      <Empty title="Not checked yet">
        ReviewPilot reads <code className="font-mono">.reviewpilot.yml</code> from the default branch on the
        repository's first review.
      </Empty>
    );
  }
  const c = config.data;
  return (
    <div className="space-y-4">
      <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
        <StatusPill status={!c.has_file ? "none" : c.is_valid ? "valid" : "invalid"} label={!c.has_file ? "no config file" : c.is_valid ? "valid" : "has errors"} />
        read from <code className="font-mono text-xs">{c.commit_sha.slice(0, 7)}</code> {ago(c.fetched_at)}
      </p>
      {[...c.errors, ...c.warnings].length ? (
        <ul className="space-y-1.5 rounded-[8px] border border-line bg-surface p-4 text-sm">
          {c.errors.map((e) => (
            <li key={e} className="text-red">
              <span className="font-mono text-[10px] font-bold">ERROR</span> {e}
            </li>
          ))}
          {c.warnings.map((w) => (
            <li key={w} className="text-amber">
              <span className="font-mono text-[10px] font-bold">WARN</span> {w}
            </li>
          ))}
        </ul>
      ) : null}
      {c.has_file ? (
        <figure className="overflow-hidden rounded-[8px] border border-line bg-surface shadow-panel">
          <figcaption className="placard border-b border-line bg-surface-2 px-4 py-2.5 !text-[10px]">.reviewpilot.yml</figcaption>
          <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed">{c.raw_yaml}</pre>
        </figure>
      ) : (
        <p className="text-sm text-muted">This repository has no .reviewpilot.yml, so the defaults apply.</p>
      )}
      <details className="rounded-[8px] border border-line bg-surface p-4 text-sm">
        <summary className="cursor-pointer text-muted hover:text-ink">Settings in effect</summary>
        <pre className="mt-3 overflow-x-auto font-mono text-xs">{JSON.stringify(c.parsed, null, 2)}</pre>
      </details>
    </div>
  );
}

function StatsTab({ repositoryId }: { repositoryId: number }) {
  const analytics = useAnalytics("30d", repositoryId);
  if (analytics.isPending) return <Loading label="Loading stats" />;
  if (analytics.isError) return <ErrorState error={analytics.error} onRetry={() => analytics.refetch()} />;
  return <Charts data={analytics.data} />;
}
