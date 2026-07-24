// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

describe("frontend browser smoke test", () => {
  beforeEach(() => {
    const html = readFileSync(resolve("src/frontend/index.html"), "utf8")
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
    document.open();
    document.write(html);
    document.close();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/api/terms")) {
        return new Response(JSON.stringify({
          terms: [{ code: "FA26", label: "Fall 2026", season: "Fall", year: 2026 }],
        }), { status: 200 });
      }
      if (url.endsWith("/universities.json")) {
        return new Response(JSON.stringify({
          universities: [
            { code: "CSUFULL", name: "Cal State Fullerton", category: "CSU" },
          ],
        }), { status: 200 });
      }
      return new Response(JSON.stringify({ error: "unexpected request" }), { status: 404 });
    }));
  });

  it("boots and populates current terms", async () => {
    await import("../../src/frontend/script.js");

    await vi.waitFor(() => {
      const options = document.querySelectorAll("#term-filter option");
      expect(options.length).toBeGreaterThan(1);
      expect(options[1].textContent).toBe("Fall 2026");
    });
  });
});
