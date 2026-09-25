import { useState } from "react";

type Theme = "light" | "dark";

function current(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(current);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => {
        document.documentElement.dataset.theme = next;
        try {
          localStorage.setItem("rp-theme", next);
        } catch {
          // private mode: the choice just won't persist
        }
        setTheme(next);
      }}
      aria-label={`Switch to ${next} theme`}
      className="rounded-md border border-line px-2.5 py-1.5 font-mono text-xs text-muted hover:text-ink"
    >
      {theme === "dark" ? "◐ dark" : "◑ light"}
    </button>
  );
}
