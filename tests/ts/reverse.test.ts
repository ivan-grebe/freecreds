import { describe, expect, it } from "vitest";

import { matchingBundleModality } from "../../functions/api/reverse";

describe("reverse lookup bundles", () => {
  it.each([
    ["members in different terms do not match", 11, "online_async", null],
    ["all async members in one term match", 10, "online_async", "online_async"],
    ["sync companion fails async-only", 10, "online_sync", null],
  ] as const)("%s", (_name, companionTerm, companionModality, expected) => {
    const offerings = new Map([
      ["1|MATH|1A", new Map([[10, "online_async"]])],
      ["1|MATH|1B", new Map([[companionTerm, companionModality]])],
    ]);
    expect(matchingBundleModality(
      offerings, ["1|MATH|1A", "1|MATH|1B"], [10, 11], true,
    ) ?? null).toBe(expected);
  });
});
