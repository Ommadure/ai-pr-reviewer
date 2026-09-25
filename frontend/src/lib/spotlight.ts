import type { PointerEvent } from "react";

/**
 * Pointer position → --mx/--my on the element, for the `.spotlight` glow. Written
 * straight to the style (no React state), so moving the mouse never re-renders.
 */
export function spotlight(event: PointerEvent<HTMLElement>) {
  if (event.pointerType !== "mouse") return;
  const el = event.currentTarget;
  const box = el.getBoundingClientRect();
  el.style.setProperty("--mx", `${event.clientX - box.left}px`);
  el.style.setProperty("--my", `${event.clientY - box.top}px`);
}
