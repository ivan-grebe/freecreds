import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

import { matchingBundleModality } from "../../functions/api/reverse";

const cases = JSON.parse(
  readFileSync(new URL("../contracts/bundle_offerings.json", import.meta.url), "utf8"),
);

describe("reverse lookup bundle contract", () => {
  for (const contract of cases) {
    it(contract.name, () => {
      const offerings = new Map(
        Object.entries(contract.offerings).map(([key, terms]) => [
          key,
          new Map(
            Object.entries(terms).map(([termId, modality]) => [Number(termId), modality]),
          ),
        ]),
      ) as Map<string, Map<number, string>>;

      expect(
        matchingBundleModality(
          offerings,
          contract.keys,
          contract.term_ids,
          contract.async_only,
        ) ?? null,
      ).toBe(contract.expected);
    });
  }
});
