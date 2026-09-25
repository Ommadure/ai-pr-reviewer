import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoute, status } from "../test/utils";
import { LoginPage } from "./LoginPage";

describe("LoginPage", () => {
  it("offers sign-in and install, and explains a failed sign-in", async () => {
    mockApi({ "/me": status(401) });
    renderRoute(<LoginPage />, { path: "/", url: "/?error=state" });
    expect(screen.getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute("href", "/api/v1/auth/github/login");
    expect(screen.getByRole("link", { name: "Install on GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/apps/reviewpilot-om/installations/new",
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("expired or didn't start here");
    expect(screen.getByRole("figure", { name: /comment on a changed line/i })).toBeInTheDocument();
  });

  it("lets you pick a finding on the scope, which holds the scan on it", async () => {
    mockApi({ "/me": status(401) });
    renderRoute(<LoginPage />);
    const hold = screen.getByRole("button", { name: "HOLD" });
    expect(hold).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(screen.getByRole("button", { name: "Config reloaded for every row" }));

    expect(await screen.findByText("jobs/export.py:31")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "RESUME" })).toHaveAttribute("aria-pressed", "true");
  });
});
