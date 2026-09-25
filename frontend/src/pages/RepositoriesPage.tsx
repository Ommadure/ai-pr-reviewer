import { motion } from "motion/react";
import { Link } from "react-router";
import { useInstallations, useRepositories, useToggleRepository } from "../api/hooks";
import type { Repository } from "../api/types";
import { StatusPill } from "../components/Badges";
import { GutterList, GutterRow } from "../components/Gutter";
import { Empty, ErrorState, Loading, PageTitle } from "../components/States";
import { ago } from "../lib/format";
import { SPRING } from "../lib/motion";

const CONFIG_LABEL: Record<string, string> = {
  valid: "config ok",
  invalid: "config has errors",
  none: "default config",
  unknown: "not reviewed yet",
};

export function RepositoriesPage() {
  const repositories = useRepositories();
  const installations = useInstallations();
  const armed = repositories.data?.filter((r) => r.enabled).length ?? 0;

  return (
    <>
      <PageTitle
        title="Repositories"
        eyebrow={repositories.data ? `${armed} of ${repositories.data.length} with automatic reviews on` : "where ReviewPilot is installed"}
      >
        {installations.data ? (
          <a href={installations.data.install_url} className="font-mono text-xs text-cyan hover:underline">
            Add repositories on GitHub ↗
          </a>
        ) : null}
      </PageTitle>
      {repositories.isPending ? (
        <Loading label="Loading repositories" />
      ) : repositories.isError ? (
        <ErrorState error={repositories.error} onRetry={() => repositories.refetch()} />
      ) : repositories.data.length === 0 ? (
        <Empty title="No repositories yet">
          <p>Choose which repositories ReviewPilot can review in its GitHub settings.</p>
        </Empty>
      ) : (
        <GutterList label="Repositories">
          {repositories.data.map((repo) => (
            <RepositoryRow key={repo.id} repo={repo} />
          ))}
        </GutterList>
      )}
    </>
  );
}

function RepositoryRow({ repo }: { repo: Repository }) {
  const toggle = useToggleRepository();
  return (
    <GutterRow
      gutter={repo.open_pulls ? `${repo.open_pulls} open` : "–"}
      marker={repo.enabled ? "add" : "none"}
      aside={<AutoReviewSwitch repo={repo} onChange={(enabled) => toggle.mutate({ id: repo.id, enabled })} />}
    >
      <Link to={`/repositories/${repo.id}`} className="font-mono text-[14px] font-bold text-ink transition-colors hover:text-cyan">
        {repo.full_name}
      </Link>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
        <StatusPill status={repo.config_status} label={CONFIG_LABEL[repo.config_status]} />
        {repo.private ? <span className="font-mono text-[11px] text-faint">PRIVATE</span> : null}
        <span>last review {ago(repo.last_reviewed_at)}</span>
      </div>
    </GutterRow>
  );
}

/**
 * The automatic-review switch, drawn like a panel toggle: the thumb springs across and
 * the track lights green while reviews are on. It's a real checkbox underneath.
 */
function AutoReviewSwitch({ repo, onChange }: { repo: Repository; onChange: (enabled: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-center gap-2.5">
      <input
        type="checkbox"
        role="switch"
        className="peer sr-only"
        checked={repo.enabled}
        onChange={(e) => onChange(e.target.checked)}
        aria-label={`Automatic reviews for ${repo.full_name}`}
      />
      <span
        aria-hidden
        className={`flex h-6 w-11 items-center rounded-[4px] border p-0.5 transition-colors duration-200 peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-cyan ${
          repo.enabled ? "justify-end border-green/50 bg-green-soft" : "justify-start border-line-strong bg-surface-2"
        }`}
      >
        <motion.span
          layout
          transition={SPRING}
          className={`block h-full w-4 rounded-[2px] ${repo.enabled ? "bg-green shadow-[0_0_10px_var(--green)]" : "bg-line-strong"}`}
        />
      </span>
      <span className={`hidden w-7 font-mono text-[10px] font-bold tracking-[0.08em] sm:inline ${repo.enabled ? "text-green" : "text-faint"}`}>
        {repo.enabled ? "ON" : "OFF"}
      </span>
    </label>
  );
}
