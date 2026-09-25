import { useAnalytics, useInstallations } from "../api/hooks";
import { Charts } from "../components/LazyAnalytics";
import { Empty, ErrorState, Loading, PageTitle } from "../components/States";

export function OverviewPage() {
  const installations = useInstallations();
  const analytics = useAnalytics("30d");

  if (installations.isPending) return <Loading label="Loading your installations" />;
  if (installations.isError) return <ErrorState error={installations.error} onRetry={() => installations.refetch()} />;

  if (installations.data.installations.length === 0) {
    return (
      <>
        <PageTitle title="Overview" />
        <Empty title="Install ReviewPilot to get started">
          <p>No repositories you can see have ReviewPilot installed yet.</p>
          <a
            href={installations.data.install_url}
            className="mt-4 inline-block rounded-md bg-ink px-4 py-2 text-sm font-medium text-bg hover:opacity-90"
          >
            Install on GitHub
          </a>
        </Empty>
      </>
    );
  }

  const accounts = installations.data.installations.map((i) => i.account_login).join(", ");
  return (
    <>
      <PageTitle eyebrow={`last 30 days · ${accounts}`} title="Overview" />
      {analytics.isPending ? (
        <Loading label="Loading analytics" />
      ) : analytics.isError ? (
        <ErrorState error={analytics.error} onRetry={() => analytics.refetch()} />
      ) : (
        <Charts data={analytics.data} />
      )}
    </>
  );
}
