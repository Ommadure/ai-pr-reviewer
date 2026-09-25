import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RadarScope } from "./RadarScope";

const contacts = [
  { id: "a", bearing: 40, range: 0.5, severity: "critical" as const, label: "SQL built from user input" },
  { id: "b", bearing: 200, range: 0.8, severity: "low" as const, label: "Unused import" },
];

describe("RadarScope", () => {
  it("makes every contact a button, so the scope works without a mouse", async () => {
    const onSelect = vi.fn();
    render(<RadarScope label="Findings" contacts={contacts} activeId="a" onSelect={onSelect} />);

    expect(screen.getByRole("group", { name: "Findings" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SQL built from user input" })).toHaveAttribute("aria-pressed", "true");

    const other = screen.getByRole("button", { name: "Unused import" });
    other.focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("holds the sweep when paused", () => {
    render(<RadarScope label="Findings" contacts={contacts} paused />);
    expect(screen.getByRole("group", { name: "Findings" })).toHaveAttribute("data-paused");
  });
});
