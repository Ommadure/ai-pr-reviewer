import { motion } from "motion/react";
import { useId } from "react";
import { SPRING } from "../lib/motion";
import { setTheme, useTheme, type Theme } from "../lib/theme";

/** Panel lighting, as on a flight deck: DAY or NIGHT. The lit segment is the one in use. */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const theme = useTheme();
  const next: Theme = theme === "dark" ? "light" : "dark";
  const id = useId(); // each switch animates its own thumb
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} theme`}
      title="Panel lighting"
      className={`relative inline-grid h-7 grid-cols-2 items-center rounded-[4px] border border-line bg-surface-2 p-0.5 font-mono text-[10px] font-bold tracking-[0.08em] ${className}`}
    >
      {(["light", "dark"] as Theme[]).map((t) => (
        <span key={t} className={`relative z-10 px-2 transition-colors duration-150 ${theme === t ? "text-ink" : "text-faint"}`}>
          {theme === t ? (
            <motion.span layoutId={`lighting-${id}`} transition={SPRING} className="absolute inset-0 -z-10 rounded-[3px] border border-line-strong bg-surface shadow-panel" />
          ) : null}
          {t === "light" ? "DAY" : "NGT"}
        </span>
      ))}
    </button>
  );
}
