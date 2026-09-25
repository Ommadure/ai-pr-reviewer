import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api/client";
import { Layout } from "./components/Layout";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFound } from "./pages/NotFoundPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PullPage } from "./pages/PullPage";
import { RepositoriesPage } from "./pages/RepositoriesPage";
import { RepositoryPage } from "./pages/RepositoryPage";
import { RunPage } from "./pages/RunPage";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 4xx answers won't change on retry; only retry what might be transient.
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failures < 2,
        refetchOnWindowFocus: false,
      },
    },
  });
}

export const routes = [
  { path: "/login", element: <LoginPage /> },
  {
    element: <Layout />,
    children: [
      { path: "/", element: <OverviewPage /> },
      { path: "/repositories", element: <RepositoriesPage /> },
      { path: "/repositories/:id", element: <RepositoryPage /> },
      { path: "/pulls/:id", element: <PullPage /> },
      { path: "/runs/:id", element: <RunPage /> },
      { path: "/analytics", element: <AnalyticsPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];

