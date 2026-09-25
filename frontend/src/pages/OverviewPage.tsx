import { useAnalytics, useInstallations, useMe } from "../api/hooks";
import { Charts } from "../components/LazyAnalytics";
import { Empty, ErrorState, Loading, PageTitle } from "../components/States";
import { button } from "../components/ui";

export function OverviewPage() {
  const me = useMe();
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
            className={`${button.primary} mt-5`}
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
      <PageTitle eyebrow={`last 30 days · ${accounts}`} title={me.data ? `${greeting()}, ${me.data.login}` : "Overview"} />
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

function greeting(now: Date = new Date()): string {
  const hour = now.getHours();
  return hour < 5 ? "Night shift" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}
