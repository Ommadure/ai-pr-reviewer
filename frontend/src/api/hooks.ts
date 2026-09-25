import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ApiError, api } from "./client";
import type {
  Analytics,
  InstallationsResponse,
  Me,
  PullDetail,
  PullPage,
  Range,
  Repository,
  RepositoryConfig,
  RunDetail,
} from "./types";

// Review data changes when PRs are pushed, not every second: 30 s of freshness keeps
// navigation instant without showing stale results for long.
const FRESH = 30_000;

export const keys = {
  me: ["me"] as const,
  installations: ["installations"] as const,
  repositories: ["repositories"] as const,
  config: (id: number) => ["repositories", id, "config"] as const,
  pulls: (id: number, state: string) => ["repositories", id, "pulls", state] as const,
  pull: (id: number) => ["pulls", id] as const,
  run: (id: number) => ["runs", id] as const,
  analytics: (range: Range, repositoryId?: number) =>
    ["analytics", range, repositoryId ?? "all"] as const,
};

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => api<Me>("/me"),
    staleTime: 5 * 60_000,
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 2,
  });
}

export function useInstallations() {
  return useQuery({
    queryKey: keys.installations,
    queryFn: () => api<InstallationsResponse>("/installations"),
    staleTime: 5 * 60_000, // mirrors the server's 5-minute access cache
  });
}

export function useRepositories() {
  return useQuery({
    queryKey: keys.repositories,
    queryFn: () => api<Repository[]>("/repositories"),
    staleTime: FRESH,
  });
}

export function useRepositoryConfig(id: number) {
  return useQuery({
    queryKey: keys.config(id),
    queryFn: () => api<RepositoryConfig | null>(`/repositories/${id}/config`),
    staleTime: FRESH,
  });
}

export function usePulls(repositoryId: number, state: string) {
  return useInfiniteQuery({
    queryKey: keys.pulls(repositoryId, state),
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: "20" });
      if (state !== "all") params.set("state", state);
      if (pageParam) params.set("cursor", pageParam);
      return api<PullPage>(`/repositories/${repositoryId}/pulls?${params}`);
    },
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: FRESH,
  });
}

export function usePull(id: number) {
  return useQuery({
    queryKey: keys.pull(id),
    queryFn: () => api<PullDetail>(`/pulls/${id}`),
    staleTime: FRESH,
    // Poll while a review is queued or running, so the timeline updates by itself.
    refetchInterval: (query) =>
      query.state.data?.runs.some((r) => r.status === "queued" || r.status === "running")
        ? 5_000
        : false,
  });
}

export function useRun(id: number) {
  return useQuery({
    queryKey: keys.run(id),
    queryFn: () => api<RunDetail>(`/runs/${id}`),
    staleTime: FRESH,
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.status ?? "") ? 5_000 : false,
  });
}

export function useAnalytics(range: Range, repositoryId?: number) {
  return useQuery({
    queryKey: keys.analytics(range, repositoryId),
    queryFn: () => {
      const params = new URLSearchParams({ range });
      if (repositoryId) params.set("repository_id", String(repositoryId));
      return api<Analytics>(`/analytics/overview?${params}`);
    },
    staleTime: FRESH,
    placeholderData: keepPreviousData, // switching range keeps the old charts until new data lands
  });
}

export function useToggleRepository() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api<Repository>(`/repositories/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
    // Optimistic: the switch flips immediately and rolls back if the request fails.
    onMutate: async ({ id, enabled }) => {
      await client.cancelQueries({ queryKey: keys.repositories });
      const previous = client.getQueryData<Repository[]>(keys.repositories);
      client.setQueryData<Repository[]>(keys.repositories, (repos) =>
        repos?.map((r) => (r.id === id ? { ...r, enabled } : r)),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => client.setQueryData(keys.repositories, context?.previous),
    onSettled: () => client.invalidateQueries({ queryKey: keys.repositories }),
  });
}

export function useRereview(pullId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ run_id: number }>(`/pulls/${pullId}/rereview`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.pull(pullId) }),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST" }),
    onSettled: () => client.clear(),
  });
}
