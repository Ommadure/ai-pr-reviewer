import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

type Handler = unknown | ((init: RequestInit | undefined) => Response | Promise<Response>);

/** Replace fetch: keys are "METHOD /path" or "/path" (GET), values are JSON or a function. */
export function mockApi(routes: Record<string, Handler>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api\/v1/, "") + url.search;
    const method = (init?.method ?? "GET").toUpperCase();
    const handler = routes[`${method} ${path}`] ?? (method === "GET" ? routes[path] : undefined);
    if (handler === undefined) return new Response(JSON.stringify({ detail: "Not found" }), { status: 404 });
    if (typeof handler === "function") return (handler as (i?: RequestInit) => Response)(init);
    return new Response(JSON.stringify(handler), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const status = (code: number, detail = "error") =>
  () => new Response(JSON.stringify({ detail }), { status: code });

/** A never-resolving response, to observe loading states. */
export const pending = () => new Promise<Response>(() => {});

export function renderRoute(element: ReactElement, { path = "/", url = path }: { path?: string; url?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path, element },
      { path: "/login", element: <p>login page</p> },
    ],
    { initialEntries: [url] },
  );
  const result = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, router };
}
