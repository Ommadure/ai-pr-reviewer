// @vitest-environment node
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = import.meta.dirname;
const vercel = JSON.parse(readFileSync(resolve(root, "vercel.json"), "utf8")) as {
  rewrites: { source: string; destination: string }[];
  headers: { source: string; headers: { key: string; value: string }[] }[];
};
const csp = vercel.headers[0]!.headers.find((h) => h.key === "Content-Security-Policy")!.value;

describe("vercel.json", () => {
  it("allows exactly the inline theme script in index.html (edit it, and this fails until the hash is updated)", () => {
    const html = readFileSync(resolve(root, "index.html"), "utf8");
    const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]!);
    expect(inline).toHaveLength(1);
    const hash = createHash("sha256").update(inline[0]!).digest("base64");
    expect(csp).toContain(`'sha256-${hash}'`);
    expect(csp).not.toMatch(/script-src[^;]*'unsafe-inline'/);
  });

  it("proxies /api to the backend over https, then falls back to the SPA", () => {
    const [api, spa] = vercel.rewrites;
    expect(api!.source).toBe("/api/:path*");
    expect(api!.destination).toMatch(/^https:\/\/.+\/api\/:path\*$/);
    expect(spa).toEqual({ source: "/(.*)", destination: "/index.html" });
  });
});
