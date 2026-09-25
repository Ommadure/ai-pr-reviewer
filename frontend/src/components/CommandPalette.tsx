import { AnimatePresence, motion } from "motion/react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router";
import { useLogout, useRepositories } from "../api/hooks";
import { DURATION, EASE_IN, EASE_OUT } from "../lib/motion";
import { getTheme, setTheme } from "../lib/theme";

type Command = { id: string; group: "Pages" | "Repositories" | "Actions"; label: string; hint?: string; run: () => void };

/**
 * Jump anywhere by typing, like entering a waypoint on the flight computer: pages,
 * every repository you can see, and a couple of actions. Arrow keys move, Enter goes,
 * Escape closes and puts focus back where it was.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <AnimatePresence>{open ? <Palette onClose={onClose} /> : null}</AnimatePresence>;
}

function Palette({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const repositories = useRepositories();
  const logout = useLogout();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();

  // Return focus to whatever opened the palette.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => opener?.focus?.();
  }, []);

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => navigate(to);
    return [
      { id: "overview", group: "Pages", label: "Overview", run: go("/") },
      { id: "repositories", group: "Pages", label: "Repositories", run: go("/repositories") },
      { id: "analytics", group: "Pages", label: "Analytics", run: go("/analytics") },
      ...(repositories.data ?? []).map<Command>((r) => ({
        id: `repo-${r.id}`,
        group: "Repositories",
        label: r.full_name,
        hint: r.enabled ? "reviews on" : "reviews off",
        run: go(`/repositories/${r.id}`),
      })),
      {
        id: "theme",
        group: "Actions",
        label: getTheme() === "dark" ? "Switch to day lighting" : "Switch to night lighting",
        run: () => setTheme(getTheme() === "dark" ? "light" : "dark"),
      },
      { id: "logout", group: "Actions", label: "Sign out", run: () => logout.mutate(undefined, { onSettled: () => navigate("/login") }) },
    ];
  }, [repositories.data, navigate, logout]);

  const results = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return commands.filter((c) => words.every((w) => `${c.label} ${c.group}`.toLowerCase().includes(w)));
  }, [commands, query]);
  const current = Math.min(active, Math.max(0, results.length - 1));

  const choose = (command: Command | undefined) => {
    if (!command) return;
    onClose();
    command.run();
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + results.length) % Math.max(1, results.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(results[current]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "Tab") {
      e.preventDefault(); // the input is the only stop inside the dialog
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[14vh]">
      <motion.div
        aria-hidden
        className="absolute inset-0 bg-bg/70 backdrop-blur-[3px]"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1, transition: { duration: DURATION.base } }}
        exit={{ opacity: 0, transition: { duration: DURATION.fast } }}
        onClick={onClose}
      />
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="Go to"
        className="relative w-full max-w-xl overflow-hidden rounded-[8px] border border-line-strong bg-surface shadow-[0_24px_64px_-12px_rgb(0_0_0/0.5)]"
        initial={{ opacity: 0, y: -12, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1, transition: { duration: DURATION.base, ease: EASE_OUT } }}
        exit={{ opacity: 0, y: -8, scale: 0.98, transition: { duration: DURATION.fast, ease: EASE_IN } }}
      >
        <div className="flex items-center gap-3 border-b border-line px-4 transition-colors focus-within:border-cyan">
          <span aria-hidden className="font-mono text-xs font-bold text-route">GO&nbsp;TO</span>
          <input
            ref={input}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={results[current] ? `${listId}-${results[current].id}` : undefined}
            aria-autocomplete="list"
            aria-label="Search pages, repositories and actions"
            placeholder="Page, repository or action…"
            className="h-13 min-w-0 flex-1 bg-transparent font-mono text-sm text-ink caret-route outline-none placeholder:text-faint focus-visible:outline-none"
          />
          <kbd className="rounded-[3px] border border-line px-1.5 py-0.5 font-mono text-[10px] text-muted">ESC</kbd>
        </div>
        <ul id={listId} role="listbox" aria-label="Results" className="max-h-[50vh] overflow-y-auto p-2">
          {results.length === 0 ? (
            <li className="px-3 py-6 text-center text-sm text-muted">Nothing matches “{query}”.</li>
          ) : (
            results.map((c, i) => {
              const header = i === 0 || results[i - 1]?.group !== c.group ? c.group : null;
              const selected = i === current;
              return (
                <li key={c.id} role="none">
                  {header ? <p className="placard px-3 pt-3 pb-1.5 !text-[10px]">{header}</p> : null}
                  <div
                    id={`${listId}-${c.id}`}
                    role="option"
                    aria-selected={selected}
                    aria-label={c.hint ? `${c.label}, ${c.hint}` : c.label}
                    onPointerMove={() => setActive(i)}
                    onClick={() => choose(c)}
                    className="relative flex cursor-pointer items-center gap-3 rounded-[5px] px-3 py-2.5 text-sm"
                  >
                    {selected ? (
                      <motion.span
                        layoutId="palette-cursor"
                        className="absolute inset-0 rounded-[5px] border-l-2 border-cyan bg-cyan-soft"
                        transition={{ duration: DURATION.fast, ease: EASE_OUT }}
                      />
                    ) : null}
                    <span className={`relative min-w-0 flex-1 truncate ${selected ? "text-ink" : "text-muted"} ${c.group === "Repositories" ? "font-mono text-[13px]" : ""}`}>
                      {c.label}
                    </span>
                    {c.hint ? <span className="relative font-mono text-[10px] text-faint">{c.hint}</span> : null}
                  </div>
                </li>
              );
            })
          )}
        </ul>
        <p className="flex gap-4 border-t border-line px-4 py-2 font-mono text-[10px] text-faint">
          <span>↑↓ move</span>
          <span>↵ go</span>
          <span>/ or ⌘K open</span>
        </p>
      </motion.div>
    </div>
  );
}
