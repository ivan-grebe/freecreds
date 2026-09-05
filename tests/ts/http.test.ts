import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cachedResponse,
  json,
  parseBooleanParam,
  requireStringParam,
} from "../../functions/_shared/http";

describe("HTTP helpers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("preserves caller Headers alongside the default response headers", () => {
    const response = json({}, { headers: new Headers({ "cache-control": "no-store" }) });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("parses supported boolean values", () => {
    expect(parseBooleanParam(new URL("https://example.test/?flag=1"), "flag")).toBe(true);
    expect(parseBooleanParam(new URL("https://example.test/?flag=TRUE"), "flag")).toBe(true);
    expect(parseBooleanParam(new URL("https://example.test/?flag=no"), "flag")).toBe(false);
  });

  it("normalizes and validates required strings", () => {
    const result = requireStringParam(
      new URL("https://example.test/?course=%20math101%20"),
      "course",
      { maxLength: 12, pattern: /^[A-Z0-9]+$/, description: "a course code" },
    );
    expect(result).toBe("MATH101");
  });

  it("keeps browser caching short while retaining versioned edge responses", async () => {
    const pending: Promise<unknown>[] = [];
    const put = vi.fn(async () => undefined);
    vi.stubGlobal("caches", {
      default: {
        match: async () => undefined,
        put,
      },
    });

    const response = await cachedResponse(
      "test/versioned-key",
      (promise) => pending.push(promise),
      3600,
      async () => json({ ok: true }),
    );
    await Promise.all(pending);

    expect(response.headers.get("cache-control"))
      .toBe("public, max-age=300, s-maxage=3600");
    expect(put).toHaveBeenCalledOnce();
  });
});
