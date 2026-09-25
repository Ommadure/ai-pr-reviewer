import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SeverityBadge, StatusPill } from "./Badges";

describe("badges", () => {
  it("shows each severity with its own colour token", () => {
    render(<SeverityBadge severity="critical" />);
    const badge = screen.getByText("critical");
    expect(badge.className).toContain("text-sev-critical");
  });

  it("falls back to the info style for unknown severities", () => {
    render(<SeverityBadge severity="catastrophic" />);
    expect(screen.getByText("catastrophic").className).toContain("text-sev-info");
  });

  it("humanizes status labels", () => {
    render(<StatusPill status="already_posted" />);
    expect(screen.getByText("already posted")).toBeInTheDocument();
  });
});
