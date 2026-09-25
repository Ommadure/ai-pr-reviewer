import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Comment } from "../api/types";
import { CommentCard } from "./CommentCard";

const base: Comment = {
  id: 1,
  path: "app.py",
  line: 60,
  start_line: 58,
  severity: "critical",
  category: "security",
  title: "SQL injection",
  body: "username goes straight into the query.",
  suggestion: 'query = "SELECT * FROM users WHERE name = ?"',
  confidence: 1,
  source: "llm",
  posted: true,
  drop_reason: null,
  status: "addressed",
  thumbs_up: 1,
  thumbs_down: 0,
  github_url: "https://github.com/o/r/pull/2#discussion_r1",
};

describe("CommentCard", () => {
  it("renders a posted comment with its lines, suggestion, feedback and link", () => {
    render(<CommentCard comment={base} />);
    expect(screen.getByRole("article", { name: "SQL injection" })).toBeInTheDocument();
    expect(screen.getByText("L58–60")).toBeInTheDocument();
    expect(screen.getByText(/SELECT \* FROM users WHERE name = \?/)).toBeInTheDocument();
    expect(screen.getByText("addressed")).toBeInTheDocument();
    expect(screen.getByLabelText("1 thumbs up, 0 thumbs down")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view on github/i })).toHaveAttribute("href", base.github_url);
  });

  it("explains why a comment was not posted", () => {
    render(<CommentCard comment={{ ...base, posted: false, drop_reason: "github_rejected", github_url: null, suggestion: null }} />);
    expect(screen.getByText("not posted: github rejected")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
