import { describe, expect, it } from "vitest";
import { mockApi, status } from "../test/utils";
import { ApiError, api } from "./client";

describe("api client", () => {
  it("returns JSON and sends the session cookie", async () => {
    const fetchMock = mockApi({ "/me": { id: 1, login: "octocat", avatar_url: null } });
    await expect(api("/me")).resolves.toEqual({ id: 1, login: "octocat", avatar_url: null });
    expect(fetchMock.mock.calls[0]?.[1]?.credentials).toBe("same-origin");
  });

  it("raises ApiError with the server's detail message", async () => {
    mockApi({ "/runs/9": status(404, "Not found") });
    const error = await api("/runs/9").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: "Not found" });
  });

  it("handles 204 No Content", async () => {
    mockApi({ "POST /auth/logout": () => new Response(null, { status: 204 }) });
    await expect(api("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });
});
