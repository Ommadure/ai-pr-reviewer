import { useState } from "react";
import { Navigate, NavLink, Outlet, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useLogout, useMe } from "../api/hooks";
import { ErrorState, Loading } from "./States";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/repositories", label: "Repositories", end: false },
  { to: "/analytics", label: "Analytics", end: false },
];

export function Layout() {
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  if (me.isPending) {
    return (
      <div className="mx-auto max-w-5xl p-8">
        <Loading label="Checking your session" />
      </div>
    );
  }
  if (me.error instanceof ApiError && me.error.status === 401) return <Navigate to="/login" replace />;
  if (me.isError) {
    return (
      <div className="mx-auto max-w-5xl p-8">
        <ErrorState error={me.error} onRetry={() => me.refetch()} />
      </div>
    );
  }

  return (
    <div className="min-h-screen md:grid md:grid-cols-[15rem_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-10 focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <aside className="border-b border-line bg-surface md:sticky md:top-0 md:h-screen md:border-r md:border-b-0">
        <div className="flex items-center justify-between px-5 py-4">
          <span className="font-display text-lg font-bold tracking-tight">
            Review<span className="text-accent">Pilot</span>
          </span>
          <button
            type="button"
            className="rounded-md border border-line px-2 py-1 text-sm md:hidden"
            aria-expanded={open}
            aria-controls="primary-nav"
            onClick={() => setOpen((v) => !v)}
          >
            Menu
          </button>
        </div>
        <nav id="primary-nav" aria-label="Primary" className={`${open ? "block" : "hidden"} px-3 pb-4 md:block`}>
          <ul className="space-y-0.5">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  onClick={() => setOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
                      isActive ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-ink"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
          <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-line px-3 pt-4">
            {me.data.avatar_url ? (
              <img src={me.data.avatar_url} alt="" className="size-6 rounded-full" />
            ) : null}
            <span className="min-w-0 flex-1 truncate font-mono text-xs">{me.data.login}</span>
            <ThemeToggle />
            <button
              type="button"
              onClick={() => logout.mutate(undefined, { onSettled: () => navigate("/login") })}
              className="w-full rounded-md px-3 py-1.5 text-left text-xs text-muted hover:bg-surface-2 hover:text-ink"
            >
              Sign out
            </button>
          </div>
        </nav>
      </aside>
      <main id="main" className="min-w-0 px-4 py-6 sm:px-8 sm:py-8">
        <div className="mx-auto max-w-6xl">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
