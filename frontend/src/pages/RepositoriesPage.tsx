import { Link } from "react-router";
import { useInstallations, useRepositories, useToggleRepository } from "../api/hooks";
import type { Repository } from "../api/types";
import { StatusPill } from "../components/Badges";
import { GutterList, GutterRow } from "../components/Gutter";
import { Empty, ErrorState, Loading, PageTitle } from "../components/States";
import { ago } from "../lib/format";

const CONFIG_LABEL: Record<string, string> = {
  valid: "config ok",
  invalid: "config has errors",
  none: "default config",
  unknown: "not reviewed yet",
};

export function RepositoriesPage() {
  const repositories = useRepositories();
  const installations = useInstallations();

  return (
    <>
      <PageTitle title="Repositories" eyebrow="where ReviewPilot is installed" />
      {repositories.isPending ? (
        <Loading label="Loading repositories" />
      ) : repositories.isError ? (
        <ErrorState error={repositories.error} onRetry={() => repositories.refetch()} />
      ) : repositories.data.length === 0 ? (
        <Empty title="No repositories yet">
          <p>Choose which repositories ReviewPilot can review in its GitHub settings.</p>
          {installations.data ? (
            <a href={installations.data.install_url} className="mt-3 inline-block font-medium text-accent hover:underline">
              Add repositories on GitHub ↗
            </a>
          ) : null}
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
    <div role="listitem">
      <GutterRow
        gutter={repo.open_pulls ? `${repo.open_pulls} open` : "–"}
        marker={repo.enabled ? "add" : "none"}
        aside={
          <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              role="switch"
              className="peer sr-only"
              checked={repo.enabled}
              onChange={(e) => toggle.mutate({ id: repo.id, enabled: e.target.checked })}
              aria-label={`Automatic reviews for ${repo.full_name}`}
            />
            <span
              aria-hidden
              className="relative h-5 w-9 rounded-full bg-line transition-colors peer-checked:bg-accent peer-focus-visible:outline-2 peer-focus-visible:outline-accent after:absolute after:top-0.5 after:left-0.5 after:size-4 after:rounded-full after:bg-surface after:transition-transform peer-checked:after:translate-x-4"
            />
            <span className="hidden w-7 sm:inline">{repo.enabled ? "on" : "off"}</span>
          </label>
        }
      >
        <Link to={`/repositories/${repo.id}`} className="font-medium text-ink hover:text-accent">
          {repo.full_name}
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
          {repo.private ? <span className="font-mono">private</span> : null}
          <StatusPill status={repo.config_status} label={CONFIG_LABEL[repo.config_status]} />
          <span>last review {ago(repo.last_reviewed_at)}</span>
        </div>
      </GutterRow>
    </div>
  );
}
