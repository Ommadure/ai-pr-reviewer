import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Comment, RunDetail } from "../api/types";
import { mockApi, renderRoute } from "../test/utils";
import { RunPage } from "./RunPage";

const comment = (id: number, over: Partial<Comment>): Comment => ({
  id,
  path: "app/users.py",
  line: 10,
  start_line: null,
  severity: "high",
  category: "bug",
  title: `comment ${id}`,
  body: "",
  suggestion: null,
  confidence: 0.9,
  source: "llm",
  posted: true,
  drop_reason: null,
  status: "open",
  thumbs_up: 0,
  thumbs_down: 0,
  github_url: null,
  ...over,
});

const run: RunDetail = {
  id: 3,
  trigger: "synchronize",
  mode: "incremental",
  status: "completed",
  skip_reason: null,
  head_sha: "730a44e99f2a56b4813f0215ded72153f4ae8c97",
  files_reviewed: 1,
  comments_posted: 2,
  cost_usd: 0,
  latency_ms: 31039,
  error_code: null,
  created_at: "2026-09-25T12:05:20Z",
  finished_at: "2026-09-25T12:05:51Z",
  pull_request_id: 2,
  pull_request_number: 2,
  pull_request_title: "Add user lookup",
  repository_full_name: "Ommadure/reviewpilot-playground",
  base_sha: "b".repeat(40),
  from_sha: "ba54a2c" + "0".repeat(33),
  mode_reason: null,
  prompt_version: "v1",
  model: "gemini-3.5-flash",
  files_total: 2,
  files_skipped: [{ path: "yarn.lock", reason: "ignored_path" }],
  comments_generated: 3,
  input_tokens: 1219,
  output_tokens: 674,
  error_message: null,
  summary: { overview: "Adds find_user.", risk_level: "high", key_changes: [], notes: [] },
  github_review_url: null,
  started_at: "2026-09-25T12:05:20Z",
  llm_calls: [
    { id: 1, purpose: "file_review", model: "gemini-3.5-flash", input_tokens: 1219, output_tokens: 674, cost_usd: 0, latency_ms: 12805, status: "success", error: null },
  ],
  comments: [
    comment(1, { title: "SQL injection", severity: "critical", category: "security" }),
    comment(2, { title: "Unclosed file", path: "app/files.py" }),
    comment(3, { title: "Ghost line", posted: false, drop_reason: "invalid_line", line: 99 }),
  ],
};

describe("RunPage", () => {
  it("groups posted comments by file and keeps dropped ones separate", async () => {
    mockApi({ "/runs/3": run });
    renderRoute(<RunPage />, { path: "/runs/:id", url: "/runs/3" });

    expect(await screen.findByRole("heading", { name: "Add user lookup" })).toBeInTheDocument();
    expect(screen.getByText("Adds find_user.")).toBeInTheDocument();
    expect(screen.getByText("Comments (2)")).toBeInTheDocument();
    const usersFile = screen.getByRole("region", { name: "app/users.py" });
    expect(within(usersFile).getByRole("article", { name: "SQL injection" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "app/files.py" })).toBeInTheDocument();
    expect(screen.getByText(/1 dropped by validation/)).toBeInTheDocument();
    expect(screen.getByText("invalid line")).toBeInTheDocument();
    expect(screen.getByText("1,219 / 674")).toBeInTheDocument(); // LLM call tokens
  });

  it("shows a helpful message for runs the user can't see", async () => {
    mockApi({});
    renderRoute(<RunPage />, { path: "/runs/:id", url: "/runs/999" });
    expect(await screen.findByRole("alert")).toHaveTextContent("belongs to an account you can't access");
  });
});
