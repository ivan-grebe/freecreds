import { describe, expect, it } from "vitest";

import { chunks, placeholders } from "../../functions/_shared/d1";
import {
  parseBooleanParam,
  requireStringParam,
  safeScheduleUrl,
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

  it("accepts HTTPS schedule links only", () => {
    expect(safeScheduleUrl("https://college.example/schedule")).toBe(
      "https://college.example/schedule",
    );
    expect(safeScheduleUrl("http://college.example/schedule")).toBeNull();
    expect(safeScheduleUrl("javascript:alert(1)")).toBeNull();
  });

  it("builds SQL placeholders and chunks arrays", () => {
    expect(placeholders(3)).toBe("?,?,?");
    expect(chunks([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });
});
