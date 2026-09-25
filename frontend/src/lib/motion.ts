import type { Transition, Variants } from "motion/react";

/*
 * Motion tokens. Things entering decelerate (ease-out); things leaving accelerate and
 * are ~30% quicker. Short moves get short durations. Every animation here also has a
 * reduced-motion path: <MotionConfig reducedMotion="user"> turns transforms off app-wide.
 */
export const DURATION = { instant: 0.1, fast: 0.15, base: 0.25, slow: 0.4, slower: 0.6 } as const;
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;
export const EASE_IN = [0.7, 0, 0.84, 0] as const;

/** A physical needle or switch: settles with a little overshoot. */
export const SPRING: Transition = { type: "spring", stiffness: 140, damping: 16, mass: 0.9 };

/** Page and section entrance: fade plus a short rise. */
export const rise: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: DURATION.slow, ease: EASE_OUT } },
};

/** List rows: 40 ms apart, capped at 8 steps, so a long list never makes anyone wait. */
export const row: Variants = {
  hidden: { opacity: 0, x: -8 },
  show: (i: number = 0) => ({
    opacity: 1,
    x: 0,
    transition: { duration: DURATION.base, ease: EASE_OUT, delay: Math.min(i, 8) * 0.04 },
  }),
};
