import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { ThemeToggle } from "./ThemeToggle";

describe("ThemeToggle", () => {
  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it("switches panel lighting and keeps every switch in sync", async () => {
    document.documentElement.dataset.theme = "light";
    render(
      <>
        <ThemeToggle />
        <ThemeToggle />
      </>,
    );
    const [first, second] = screen.getAllByRole("button", { name: "Switch to dark theme" });
    await userEvent.click(first!);

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(second).toHaveAccessibleName("Switch to light theme");
  });
});
