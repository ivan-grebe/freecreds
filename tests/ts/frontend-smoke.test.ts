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
      const options = document.querySelectorAll<HTMLInputElement>(
        '#term-filter input[name="term"]',
      );
      expect(options).toHaveLength(1);
      expect(options[0].value).toBe("FA26");
      expect(options[0].parentElement?.textContent).toContain("Fall 2026");
    });

    expect(document.querySelector<HTMLInputElement>("#standalone-only")?.checked).toBe(true);
    const termFilter = document.querySelector("#term-filter")!;
    const searchButton = document.querySelector("#go")!;
    expect(termFilter.compareDocumentPosition(searchButton) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("keeps dropdowns above the content that follows the search form", () => {
    const controls = document.querySelector<HTMLElement>(".controls");

    expect(controls).not.toBeNull();
    expect(getComputedStyle(controls!).position).toBe("relative");
    expect(getComputedStyle(controls!).zIndex).toBe("1");
  });
});
