import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SeverityBadge, StatusPill } from "./Badges";

describe("badges", () => {
  it("lights critical and high solid, like a warning and a caution", () => {
    render(<SeverityBadge severity="critical" />);
    const badge = screen.getByText("critical");
    expect(badge).toHaveAttribute("data-level", "critical");
    expect(badge.className).toContain("bg-red");
  });

  it("falls back to the info style for unknown severities, still printing the word", () => {
    render(<SeverityBadge severity="catastrophic" />);
    expect(screen.getByText("catastrophic")).toHaveAttribute("data-level", "info");
  });

  it("humanizes status labels", () => {
    render(<StatusPill status="already_posted" />);
    expect(screen.getByText("already posted")).toBeInTheDocument();
  });
});
