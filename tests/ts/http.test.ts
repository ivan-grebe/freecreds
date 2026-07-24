import { describe, expect, it } from "vitest";

import { chunks, placeholders } from "../../functions/_shared/d1";
import {
  parseBooleanParam,
  requireStringParam,
} from "../../functions/_shared/http";

describe("HTTP helpers", () => {
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

  it("builds SQL placeholders and chunks arrays", () => {
    expect(placeholders(3)).toBe("?,?,?");
    expect(chunks([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });
});
