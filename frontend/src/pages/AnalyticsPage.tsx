import { useSearchParams } from "react-router";
import { useAnalytics, useRepositories } from "../api/hooks";
import type { Range } from "../api/types";
import { Charts } from "../components/LazyAnalytics";
import { ErrorState, Loading, PageTitle } from "../components/States";

const RANGES: Range[] = ["7d", "30d", "90d"];

export function AnalyticsPage() {
  const [params, setParams] = useSearchParams();
  const range = (RANGES.find((r) => r === params.get("range")) ?? "30d") as Range;
  const repositoryId = Number(params.get("repository")) || undefined;
  const repositories = useRepositories();
  const analytics = useAnalytics(range, repositoryId);

  const update = (next: Record<string, string>) => {
    const merged = { range, ...(repositoryId ? { repository: String(repositoryId) } : {}), ...next };
    setParams(Object.fromEntries(Object.entries(merged).filter(([, v]) => v)), { replace: true });
  };

  return (
    <>
      <PageTitle title="Analytics" eyebrow="reviews, cost and usefulness" />
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div role="radiogroup" aria-label="Time range" className="inline-flex overflow-hidden rounded-md border border-line bg-surface">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={r === range}
              onClick={() => update({ range: r })}
              className={`px-3 py-1.5 font-mono text-xs ${r === range ? "bg-accent text-white" : "text-muted hover:text-ink"}`}
            >
              {r}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">
          Repository
          <select
            value={repositoryId ?? ""}
            onChange={(e) => update({ repository: e.target.value })}
            className="rounded-md border border-line bg-surface px-2 py-1 text-ink"
          >
            <option value="">all repositories</option>
            {repositories.data?.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {analytics.isPending ? (
        <Loading label="Loading analytics" />
      ) : analytics.isError ? (
        <ErrorState error={analytics.error} onRetry={() => analytics.refetch()} />
      ) : (
        <div className={analytics.isPlaceholderData ? "opacity-60 transition-opacity" : ""}>
          <Charts data={analytics.data} />
        </div>
      )}
    </>
  );
}
