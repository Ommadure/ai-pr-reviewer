import { motion } from "motion/react";
import { useSearchParams } from "react-router";
import { useAnalytics, useRepositories } from "../api/hooks";
import type { Range } from "../api/types";
import { Charts } from "../components/LazyAnalytics";
import { ErrorState, Loading, PageTitle } from "../components/States";
import { field } from "../components/ui";
import { SPRING } from "../lib/motion";

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
      <div className="mb-6 flex flex-wrap items-center gap-4">
        <div role="radiogroup" aria-label="Time range" className="inline-flex rounded-[5px] border border-line bg-surface-2 p-0.5">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={r === range}
              onClick={() => update({ range: r })}
              className={`relative min-h-8 px-3.5 font-mono text-xs font-bold transition-colors duration-150 ${r === range ? "text-on-route" : "text-muted hover:text-ink"}`}
            >
              {r === range ? <motion.span layoutId="range-thumb" transition={SPRING} className="absolute inset-0 rounded-[3px] bg-route" /> : null}
              <span className="relative">{r}</span>
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">
          Repository
          <select
            value={repositoryId ?? ""}
            onChange={(e) => update({ repository: e.target.value })}
            className={field}
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
        <div className={`transition-opacity duration-200 ${analytics.isPlaceholderData ? "opacity-50" : ""}`}>
          <Charts data={analytics.data} />
        </div>
      )}
    </>
  );
}
