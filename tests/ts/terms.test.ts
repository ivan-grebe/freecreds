import { describe, expect, it } from "vitest";

import { currentTerm, nextTerm, parseTermCode } from "../../functions/_shared/terms";

describe("term helpers", () => {
  it("selects the current term by month", () => {
    expect(currentTerm(new Date("2026-02-15T00:00:00Z")).code).toBe("SP26");
    expect(currentTerm(new Date("2026-06-15T00:00:00Z")).code).toBe("SU26");
    expect(currentTerm(new Date("2026-09-15T00:00:00Z")).code).toBe("FA26");
  });

  it("advances across an academic year", () => {
    const fall = parseTermCode("FA26");
    expect(fall).not.toBeNull();
    expect(nextTerm(fall!).code).toBe("WI27");
    expect(nextTerm(nextTerm(fall!)).code).toBe("SP27");
  });

  it("rejects malformed term codes", () => {
    expect(parseTermCode("fall-2026")).toBeNull();
    expect(parseTermCode("XX26")).toBeNull();
  });
});
