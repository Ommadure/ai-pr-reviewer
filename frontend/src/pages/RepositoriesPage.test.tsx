import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Repository } from "../api/types";
import { mockApi, pending, renderRoute, status } from "../test/utils";
import { RepositoriesPage } from "./RepositoriesPage";

const repo: Repository = {
  id: 3,
  installation_id: 1,
  full_name: "octocat/app",
  private: true,
  enabled: true,
  default_branch: "main",
  last_reviewed_at: null,
  config_status: "invalid",
  open_pulls: 2,
};
const installations = { installations: [], install_url: "https://github.com/apps/x/installations/new" };

describe("RepositoriesPage", () => {
  it("shows a loading state", () => {
    mockApi({ "/repositories": pending, "/installations": installations });
    renderRoute(<RepositoriesPage />);
    expect(screen.getByRole("status", { name: "Loading repositories" })).toHaveAttribute("aria-busy", "true");
  });

  it("invites the user to add repositories when there are none", async () => {
    mockApi({ "/repositories": [], "/installations": installations });
    renderRoute(<RepositoriesPage />);
    expect(await screen.findByText("No repositories yet")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /add repositories on github/i })).toHaveAttribute(
      "href",
      installations.install_url,
    );
  });

  it("shows an error with a retry button", async () => {
    mockApi({ "/repositories": status(500), "/installations": installations });
    renderRoute(<RepositoriesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn't load");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("lists repositories and toggles automatic reviews optimistically", async () => {
    let enabled = true;
    const fetchMock = mockApi({
      "/repositories": () => new Response(JSON.stringify([{ ...repo, enabled }])),
      "/installations": installations,
      "PATCH /repositories/3": (init: RequestInit | undefined) => {
        enabled = JSON.parse(String(init?.body)).enabled;
        return new Response(JSON.stringify({ ...repo, enabled }));
      },
    });
    renderRoute(<RepositoriesPage />);

    expect(await screen.findByRole("link", { name: "octocat/app" })).toHaveAttribute("href", "/repositories/3");
    expect(screen.getByText("config has errors")).toBeInTheDocument();
    const toggle = screen.getByRole("switch", { name: "Automatic reviews for octocat/app" });
    expect(toggle).toBeChecked();

    await userEvent.click(toggle);

    expect(toggle).not.toBeChecked();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/repositories/3", expect.objectContaining({ method: "PATCH" })),
    );
  });
});
