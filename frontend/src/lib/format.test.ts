import { describe, expect, it } from "vitest";
import { ago, duration, humanize, money, percent } from "./format";

describe("format", () => {
  it("shows cost in rupees when a rate is configured", () => {
    expect(money(0.002, 83)).toBe("₹0.17");
    expect(money(0.0021)).toBe("$0.0021");
    expect(money(12.5)).toBe("$12.50");
  });

  it("formats durations", () => {
    expect(duration(850)).toBe("850 ms");
    expect(duration(30_300)).toBe("30.3 s");
    expect(duration(61_000)).toBe("1 m 1 s");
    expect(duration(null)).toBe("–");
  });

  it("formats rates and relative time", () => {
    expect(percent(0.5)).toBe("50%");
    expect(percent(null)).toBe("–");
    const now = new Date("2026-09-25T12:00:00Z");
    expect(ago("2026-09-25T09:00:00Z", now)).toBe("3 hours ago");
    expect(ago(null, now)).toBe("never");
    expect(humanize("already_posted")).toBe("already posted");
  });
});
