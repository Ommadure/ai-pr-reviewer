import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InlineCode } from "./InlineCode";

describe("InlineCode", () => {
  it("renders backtick spans as code and everything else as plain text", () => {
    const { container } = render(<p><InlineCode text="Use `db.execute(q, (uid,))` not <b>this</b>" /></p>);
    expect(container.querySelector("code")?.textContent).toBe("db.execute(q, (uid,))");
    expect(container.querySelector("b")).toBeNull(); // HTML stays text
    expect(container.textContent).toContain("<b>this</b>");
  });
});
