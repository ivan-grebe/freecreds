import { describe, expect, it } from "vitest";

import { buildStatusPayload } from "../../functions/api/status";

describe("data refresh status", () => {
  it("keeps the latest completed time for each source", () => {
    expect(buildStatusPayload([
      { kind: "assist", last_updated: "2026-04-22T11:00:00Z" },
      { kind: "cvc", last_updated: "2026-04-23T11:00:00Z" },
    ])).toEqual({
      updated_at: {
        assist: "2026-04-22T11:00:00Z",
        cvc: "2026-04-23T11:00:00Z",
      },
    });
  });

  it("returns null when a source has no completed refresh", () => {
    expect(buildStatusPayload([])).toEqual({
      updated_at: { assist: null, cvc: null },
    });
  });
});
