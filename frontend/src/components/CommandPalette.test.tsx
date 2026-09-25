import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoute } from "../test/utils";
import { Layout } from "./Layout";

const repo = {
  id: 3,
  installation_id: 1,
  full_name: "octocat/app",
  private: false,
  enabled: true,
  default_branch: "main",
  last_reviewed_at: null,
  config_status: "none",
  open_pulls: 0,
};

describe("command palette", () => {
  it("opens with Ctrl+K, filters as you type, and goes to the chosen repository", async () => {
    mockApi({ "/me": { id: 1, login: "octocat", avatar_url: null }, "/repositories": [repo] });
    const { router } = renderRoute(<Layout />, { path: "*", url: "/" });
    await screen.findByText("octocat");

    await userEvent.keyboard("{Control>}k{/Control}");
    const input = await screen.findByRole("combobox", { name: /search pages, repositories/i });
    expect(input).toHaveFocus();

    await userEvent.type(input, "octo");
    const option = await screen.findByRole("option", { name: "octocat/app, reviews on" });
    expect(option).toHaveAttribute("aria-selected", "true"); // the first match is ready for Enter

    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(router.state.location.pathname).toBe("/repositories/3");
  });

  it("closes on Escape and says when nothing matches", async () => {
    mockApi({ "/me": { id: 1, login: "octocat", avatar_url: null }, "/repositories": [] });
    renderRoute(<Layout />);
    await screen.findByText("octocat");

    await userEvent.click(screen.getByRole("button", { name: /go to…/i }));
    await userEvent.type(await screen.findByRole("combobox"), "zzz");
    expect(screen.getByText(/nothing matches/i)).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
