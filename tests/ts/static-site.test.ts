import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

function frontendFile(name: string): string {
  return readFileSync(resolve("src/frontend", name), "utf8");
}

describe("static site metadata", () => {
  it("publishes valid crawler resources and a real not-found page", () => {
    expect(frontendFile("robots.txt")).toContain(
      "Sitemap: https://freecreds.pages.dev/sitemap.xml",
    );
    expect(frontendFile("sitemap.xml")).toContain("<loc>https://freecreds.pages.dev/</loc>");
    expect(frontendFile("llms.txt")).toMatch(/^# FreeCreds/m);
    expect(frontendFile("404.html")).toContain('<meta name="robots" content="noindex">');
  });

  it("uses hashes instead of unsafe-inline in the content security policy", () => {
    const html = frontendFile("index.html").replace(/\r\n/g, "\n");
    const inlineScript = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
    const inlineStyle = html.match(/<style>([\s\S]*?)<\/style>/)?.[1];
    expect(inlineScript).toBeDefined();
    expect(inlineStyle).toBeDefined();
    const scriptHash = createHash("sha256").update(inlineScript!).digest("base64");
    const styleHash = createHash("sha256").update(inlineStyle!).digest("base64");
    const headers = frontendFile("_headers");
    expect(headers).not.toContain("'unsafe-inline'");
    expect(headers).toContain(`'sha256-${scriptHash}'`);
    expect(headers).toContain(`'sha256-${styleHash}'`);
    expect(headers).toContain("Strict-Transport-Security: max-age=63072000");
  });
});

describe("refresh authentication", () => {
  it("compares manual refresh tokens without an early secret-dependent exit", async () => {
    const { timingSafeTokenEqual } = await import("../../src/workers/refresh");
    await expect(timingSafeTokenEqual("correct token", "correct token")).resolves.toBe(true);
    await expect(timingSafeTokenEqual("incorrect", "correct token")).resolves.toBe(false);
  });
});
