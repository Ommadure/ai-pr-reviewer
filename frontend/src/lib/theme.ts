import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

// index.html sets data-theme before first paint; this module keeps every switch
// (sidebar, landing page, command palette) in sync with it afterwards.
const listeners = new Set<() => void>();

export const getTheme = (): Theme => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("rp-theme", theme);
  } catch {
    // private mode: the choice just won't persist
  }
  listeners.forEach((notify) => notify());
}

const subscribe = (notify: () => void) => {
  listeners.add(notify);
  return () => listeners.delete(notify);
};

export const useTheme = () => useSyncExternalStore(subscribe, getTheme);
