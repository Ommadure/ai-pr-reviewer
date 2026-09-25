import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { MotionGlobalConfig } from "motion/react";
import { afterEach, vi } from "vitest";

// jsdom never paints, so animations would never finish (exits would hang): jump to the end.
MotionGlobalConfig.skipAnimations = true;

// jsdom has no layout engine; Recharts' ResponsiveContainer needs this to exist.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// Nor IntersectionObserver: Motion's in-view animations and the radar scope's
// pause-when-hidden use it. Report everything as visible.
class IntersectionObserverStub {
  constructor(private readonly callback: IntersectionObserverCallback) {}
  observe(target: Element) {
    this.callback([{ isIntersecting: true, target } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

function stubLayout() {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  vi.stubGlobal("IntersectionObserver", IntersectionObserverStub);
}
stubLayout();

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  stubLayout();
});
