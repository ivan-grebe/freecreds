// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

describe("frontend browser smoke test", () => {
  beforeEach(() => {
    vi.resetModules();
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
      if (url.endsWith("/api/status")) {
        return new Response(JSON.stringify({
          updated_at: {
            assist: "2026-04-22T11:00:00Z",
            cvc: "2026-04-23T11:00:00Z",
          },
        }), { status: 200 });
      }
      if (url.endsWith("/universities.json")) {
        return new Response(JSON.stringify({
          universities: [
            { code: "CSUFULL", name: "Cal State Fullerton", category: "CSU" },
          ],
        }), { status: 200 });
      }
      if (url.startsWith("/api/courses?")) {
        return Response.json({ courses: [{ prefix: "MATH", number: "170A", title: "Mathematical Structures I" }] });
      }
      if (url.startsWith("/api/reverse?")) {
        const params = new URL(url, "https://example.test").searchParams;
        return Response.json({
          query: {
            university: { name: "Cal State Fullerton", code: "CSUFULL" },
            course: { prefix: "MATH", number: "170A", title: "Mathematical Structures I" },
            terms: params.has("term") ? [{ code: "FA26", label: "Fall 2026" }] : [],
            async_only: params.has("async_only"),
          },
          results: [], no_articulation: [],
        });
      }
      return new Response(JSON.stringify({ error: "unexpected request" }), { status: 404 });
    }));
  });

  async function chooseCourse() {
    await import("../../src/frontend/script.js");
    const university = document.querySelector<HTMLInputElement>("#uni-input")!;
    await vi.waitFor(() => expect(university.disabled).toBe(false));
    university.focus();
    university.value = "CSUFULL";
    university.dispatchEvent(new Event("input", { bubbles: true }));
    university.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    const course = document.querySelector<HTMLInputElement>("#course-input")!;
    await vi.waitFor(() => expect(course.disabled).toBe(false));
    course.focus();
    course.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    return { university, course };
  }

  function search() {
    document.querySelector("#search-form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  }

  it("removes online restrictions when recovering from an empty online search", async () => {
    await chooseCourse();
    document.querySelector<HTMLInputElement>("#online-matches")!.click();
    document.querySelector<HTMLInputElement>('#term-filter input[value="FA26"]')!.click();
    document.querySelector<HTMLInputElement>("#async-only")!.click();
    search();
    await vi.waitFor(() => expect(document.querySelector("#output h2")).not.toBeNull());
    expect(String(vi.mocked(fetch).mock.calls.at(-1)?.[0])).toContain("async_only=true");
    const showAll = [...document.querySelectorAll<HTMLButtonElement>("#output button")]
      .find(button => button.textContent === "Show all transfer matches")!;
    showAll.click();
    await vi.waitFor(() => expect(document.querySelector("#output h2")).not.toBeNull());
    const request = new URL(String(vi.mocked(fetch).mock.calls.at(-1)?.[0]), "https://example.test");
    expect(request.searchParams.has("term")).toBe(false);
    expect(request.searchParams.has("async_only")).toBe(false);
    expect(document.activeElement?.id).toBe("output");
  });

  it("clears the old course when the university is edited and closes its list on Tab", async () => {
    const { university, course } = await chooseCourse();
    university.focus();
    university.value = "Fullerton";
    university.dispatchEvent(new Event("input", { bubbles: true }));
    expect(course.value).toBe("");
    expect(course.disabled).toBe(true);
    const activeId = university.getAttribute("aria-activedescendant");
    expect(document.getElementById(activeId!)?.getAttribute("aria-selected")).toBe("true");
    university.blur();
    expect(university.getAttribute("aria-expanded")).toBe("false");
    expect(university.hasAttribute("aria-activedescendant")).toBe(false);
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
    await vi.waitFor(() => {
      expect(document.querySelector("#assist-updated")?.textContent).toContain("Apr 22, 2026");
      expect(document.querySelector("#cvc-updated")?.textContent).toContain("Apr 23, 2026");
    });
  });

});
