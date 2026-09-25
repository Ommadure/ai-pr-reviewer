import { screen } from "@testing-library/react";
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
});
