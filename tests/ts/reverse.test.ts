import { describe, expect, it } from "vitest";

import { matchingBundleModality } from "../../functions/api/reverse";

describe("reverse lookup bundle offerings", () => {
  it("requires every bundle member in the same term", () => {
    const offerings = new Map([
      ["1|MATH|1A", new Map([[10, "online_async"]])],
      ["1|MATH|1B", new Map([[11, "online_async"]])],
    ]);

    expect(
      matchingBundleModality(
        offerings,
        ["1|MATH|1A", "1|MATH|1B"],
        [10, 11],
        true,
      ),
    ).toBeUndefined();
  });

  it("accepts a bundle when every member meets the filter together", () => {
    const offerings = new Map([
      ["1|MATH|1A", new Map([[10, "online_async"]])],
      ["1|MATH|1B", new Map([[10, "online_async"]])],
    ]);

    expect(
      matchingBundleModality(
        offerings,
        ["1|MATH|1A", "1|MATH|1B"],
        [10],
        true,
      ),
    ).toBe("online_async");
  });
});
