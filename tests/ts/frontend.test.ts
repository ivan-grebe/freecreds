import { describe, expect, it } from "vitest";

import { dedupeBundleRows, renderSourceLabel } from "../../src/frontend/ui-logic.js";

describe("frontend result logic", () => {
  it("combines duplicate representations of the same AND bundle", () => {
    const shared = {
      cc_code: "TEST",
      academic_year_id: 76,
      is_standalone: false,
      receiving_companion_courses: [],
      schedule_url: null,
    };
    const first = {
      ...shared,
      cc_course: { prefix: "MATH", number: "1A", title: "A" },
      companion_courses: [{ prefix: "MATH", number: "1B", title: "B" }],
      sources: ["AllDepartments"],
      offering_status: "online_sync",
    };
    const second = {
      ...shared,
      cc_course: { prefix: "MATH", number: "1B", title: "B" },
      companion_courses: [{ prefix: "MATH", number: "1A", title: "A" }],
      sources: ["Major: Mathematics"],
      offering_status: "async_online",
      schedule_url: "https://college.example/schedule",
    };

    const result = dedupeBundleRows([first, second]);

    expect(result.hidden).toBe(1);
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0].offering_status).toBe("async_online");
    expect(result.rows[0].sources).toEqual(["AllDepartments", "Major: Mathematics"]);
  });

  it("renders a concise major-specific source label", () => {
    expect(renderSourceLabel(["Major: Biology", "Major: Chemistry"])).toEqual({
      text: "Major-specific: Biology, Chemistry",
      title: "Biology\nChemistry",
    });
  });
});
