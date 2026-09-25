import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoute, status } from "../test/utils";
import { Layout } from "./Layout";

describe("Layout", () => {
  it("sends signed-out visitors to the login page", async () => {
    mockApi({ "/me": status(401, "Not signed in") });
    renderRoute(<Layout />);
    expect(await screen.findByText("login page")).toBeInTheDocument();
  });

  it("shows navigation and the signed-in user", async () => {
    mockApi({ "/me": { id: 1, login: "octocat", avatar_url: null } });
    renderRoute(<Layout />);
    expect(await screen.findByText("octocat")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Repositories" })).toHaveAttribute("href", "/repositories");
  });
});
