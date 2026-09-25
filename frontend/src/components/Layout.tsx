import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useLogout, useMe, useSystems } from "../api/hooks";
import { DURATION, EASE_OUT, rise, SPRING } from "../lib/motion";
import { useCommandHotkey } from "../lib/useCommandHotkey";
import { CommandPalette } from "./CommandPalette";
import { Logo } from "./Logo";
import { ErrorState, Loading } from "./States";
import { ThemeToggle } from "./ThemeToggle";
import { button } from "./ui";

const icon = "size-4 shrink-0";
const NAV: { to: string; label: string; end: boolean; glyph: ReactNode }[] = [
  {
    to: "/",
    label: "Overview",
    end: true,
    // attitude indicator: the overall picture
    glyph: (
      <svg viewBox="0 0 16 16" className={icon} fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
        <circle cx="8" cy="8" r="6.3" />
        <path d="M2 9.2 L14 7" />
        <path d="M6 12.2h4" strokeWidth="1" />
      </svg>
    ),
  },
  {
    to: "/repositories",
    label: "Repositories",
    end: false,
    // flight strips
    glyph: (
      <svg viewBox="0 0 16 16" className={icon} fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
        <rect x="1.7" y="2.5" width="12.6" height="3.2" rx="0.6" />
        <rect x="1.7" y="10.3" width="12.6" height="3.2" rx="0.6" />
        <path d="M5 2.5v3.2M5 10.3v3.2" strokeWidth="1" />
      </svg>
    ),
  },
  {
    to: "/analytics",
    label: "Analytics",
    end: false,
    // a gauge
    glyph: (
      <svg viewBox="0 0 16 16" className={icon} fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
        <path d="M2.3 11.5a6 6 0 1 1 11.4 0" />
        <path d="M8 10.5 L11 5.5" />
        <circle cx="8" cy="10.5" r="0.9" fill="currentColor" />
      </svg>
    ),
  },
];

export function Layout() {
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const togglePalette = useCallback((update: (open: boolean) => boolean) => setPaletteOpen(update), []);
  useCommandHotkey(togglePalette);
  const panel = useRef<HTMLElement>(null);
  useHiddenDrawer(panel, menuOpen, () => setMenuOpen(false));

  // A new page closes the mobile menu.
  const [lastPath, setLastPath] = useState(location.pathname);
  if (lastPath !== location.pathname) {
    setLastPath(location.pathname);
    setMenuOpen(false);
  }

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

  const openPalette = () => setPaletteOpen(true);

  return (
    <div className="min-h-screen md:grid md:grid-cols-[15.5rem_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[60] focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>

      {/* mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between gap-2 border-b border-line bg-bg/85 px-4 py-2.5 backdrop-blur md:hidden">
        <Link to="/" aria-label="ReviewPilot overview">
          <Logo />
        </Link>
        <div className="flex items-center gap-1.5">
          <button type="button" onClick={openPalette} className={`${button.ghost} min-h-10 px-3`} aria-label="Go to a page or repository">
            <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <circle cx="7" cy="7" r="4.5" />
              <path d="M10.5 10.5 14 14" />
            </svg>
          </button>
          <button
            type="button"
            className={`${button.secondary} min-h-10 px-3 font-mono text-xs tracking-[0.08em]`}
            aria-expanded={menuOpen}
            aria-controls="panel"
            onClick={() => setMenuOpen((v) => !v)}
          >
            Menu
          </button>
        </div>
      </header>

      <AnimatePresence>
        {menuOpen ? (
          <motion.div
            aria-hidden
            className="fixed inset-0 z-30 bg-bg/70 backdrop-blur-[2px] md:hidden"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: DURATION.base }}
            onClick={() => setMenuOpen(false)}
          />
        ) : null}
      </AnimatePresence>

      <aside
        ref={panel}
        id="panel"
        className={`fixed inset-y-0 left-0 z-40 flex w-[16.5rem] flex-col border-r border-line bg-surface transition-transform duration-300 ease-out md:sticky md:top-0 md:z-auto md:h-screen md:w-auto md:translate-x-0 ${
          menuOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-5 pt-5 pb-4">
          <Link to="/" aria-label="ReviewPilot overview">
            <Logo />
          </Link>
          <button type="button" onClick={() => setMenuOpen(false)} className={`${button.ghost} min-h-9 px-2 md:hidden`} aria-label="Close menu">
            ✕
          </button>
        </div>

        <div className="px-3">
          <button
            type="button"
            onClick={openPalette}
            className="flex w-full items-center gap-2.5 rounded-[5px] border border-line bg-surface-2 px-3 py-2 text-left text-sm text-muted transition-colors hover:border-cyan hover:text-ink"
          >
            <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <circle cx="7" cy="7" r="4.5" />
              <path d="M10.5 10.5 14 14" />
            </svg>
            <span className="flex-1">Go to…</span>
            <kbd className="rounded-[3px] border border-line px-1 font-mono text-[10px] text-faint">⌘K</kbd>
          </button>
        </div>

        <nav aria-label="Primary" className="mt-5 px-3">
          <p className="placard mb-2 px-3 !text-[10px]">Navigate</p>
          <ul className="space-y-0.5">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `relative flex min-h-10 items-center gap-3 rounded-[5px] px-3 text-sm transition-colors duration-150 ${
                      isActive ? "font-medium text-ink" : "text-muted hover:bg-surface-2 hover:text-ink"
                    }`
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive ? (
                        <motion.span layoutId="nav-bug" transition={SPRING} className="absolute inset-0 rounded-[5px] bg-route-soft">
                          <span className="absolute top-2 bottom-2 left-0 w-[3px] rounded-r bg-route" />
                        </motion.span>
                      ) : null}
                      <span className={`relative ${isActive ? "text-route" : ""}`}>{item.glyph}</span>
                      <span className="relative">{item.label}</span>
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="mt-auto space-y-4 px-5 pb-5">
          <SystemLights />
          <div className="flex items-center gap-2.5 border-t border-line pt-4">
            {me.data.avatar_url ? (
              <img src={me.data.avatar_url} alt="" className="size-7 rounded-full border border-line-strong" />
            ) : (
              <span aria-hidden className="grid size-7 place-items-center rounded-full border border-line-strong font-mono text-[11px] text-muted">
                {me.data.login.slice(0, 1).toUpperCase()}
              </span>
            )}
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{me.data.login}</span>
            <ThemeToggle />
          </div>
          <button
            type="button"
            onClick={() => logout.mutate(undefined, { onSettled: () => navigate("/login") })}
            className={`${button.ghost} min-h-8 w-full justify-start px-2 text-xs`}
          >
            Sign out
          </button>
        </div>
      </aside>

      <main id="main" className="min-w-0 px-4 py-6 sm:px-8 sm:py-9 lg:px-12">
        <motion.div key={location.pathname} variants={rise} initial="hidden" animate="show" className="mx-auto max-w-6xl">
          <Outlet />
        </motion.div>
      </main>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}

/**
 * On a phone the panel is an off-screen drawer: while it's closed, make it inert so
 * keyboard and screen-reader users don't tab into links they can't see. Escape closes it.
 */
function useHiddenDrawer(panel: React.RefObject<HTMLElement | null>, open: boolean, close: () => void) {
  useEffect(() => {
    const el = panel.current;
    if (!el || typeof window.matchMedia !== "function") return;
    const desktop = window.matchMedia("(min-width: 768px)");
    const sync = () => el.toggleAttribute("inert", !desktop.matches && !open);
    sync();
    desktop.addEventListener("change", sync);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && open && close();
    window.addEventListener("keydown", onKey);
    return () => {
      desktop.removeEventListener("change", sync);
      window.removeEventListener("keydown", onKey);
    };
  }, [panel, open, close]);
}

/** Panel lights for the three systems a review depends on, plus the time in UTC. */
function SystemLights() {
  const systems = useSystems();
  const lights = [
    { key: "api", label: "API" },
    { key: "database", label: "DB" },
    { key: "redis", label: "QUEUE" },
  ] as const;
  const known = systems.data;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <p className="placard !text-[10px]">Systems</p>
        <UtcClock />
      </div>
      <ul className="grid grid-cols-3 gap-1.5" aria-label="System status">
        {lights.map(({ key, label }) => {
          const ok = known?.[key];
          const state = known == null ? "checking" : ok ? "ok" : "down";
          return (
            <li
              key={key}
              title={`${label}: ${state}`}
              className={`rounded-[3px] border py-1 text-center font-mono text-[10px] font-bold tracking-[0.08em] transition-colors duration-300 ${
                state === "ok"
                  ? "border-green/35 bg-green-soft text-green"
                  : state === "down"
                    ? "border-red/40 bg-red-soft text-red"
                    : "border-line bg-surface-2 text-faint"
              }`}
            >
              {label}
              <span className="sr-only"> {state}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function UtcClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <motion.time
      dateTime={now.toISOString()}
      className="font-mono text-[10px] text-muted tabular"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: DURATION.slow, ease: EASE_OUT }}
    >
      {now.toISOString().slice(11, 19)}Z
    </motion.time>
  );
}
