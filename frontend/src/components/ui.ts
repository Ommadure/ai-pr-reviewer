/*
 * Button styles, shared so every action looks and behaves the same. Magenta is the
 * route colour: only the one primary action on a screen gets it.
 */
const base =
  "inline-flex min-h-9 items-center justify-center gap-2 rounded-[5px] px-4 text-sm font-medium whitespace-nowrap transition-[background-color,border-color,color,box-shadow,transform] duration-150 active:translate-y-px disabled:pointer-events-none disabled:opacity-45";

export const button = {
  primary: `${base} bg-route text-on-route shadow-[var(--glow)] hover:brightness-110`,
  secondary: `${base} border border-line-strong bg-surface text-ink hover:border-cyan hover:text-cyan`,
  ghost: `${base} text-muted hover:bg-surface-2 hover:text-ink`,
};

export const field =
  "min-h-9 rounded-[5px] border border-line bg-surface px-2.5 text-sm text-ink transition-colors hover:border-line-strong focus-visible:border-cyan";
