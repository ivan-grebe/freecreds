import { describe, expect, it } from "vitest";

import { buildCourseResponse } from "../../functions/api/courses";
import {
  buildReverseResponse,
  parseCompanionIds,
  parseQuery,
  type ReverseQuery,
} from "../../functions/api/reverse";
import { latestIngestVersion } from "../../functions/_shared/ingest-version";
import { fakeD1 } from "./d1-fake";

const query: ReverseQuery = {
  university: "CSUFULL",
  prefix: "MATH",
  number: "170A",
  standaloneOnly: true,
  asyncOnly: false,
  termCodes: [],
};

describe("production D1 API implementations", () => {
  it("lists only current, non-terminated courses", async () => {
    const db = fakeD1((sql, params) => {
      if (sql.includes("FROM institutions")) {
        return [{ id: 1, code: "CSUFULL", name: "Cal State Fullerton" }];
      }
      if (sql.includes("WITH latest_year")) {
        expect(sql).toContain("c.is_terminated = 0");
        expect(sql).toContain("a.academic_year_id = (SELECT id FROM latest_year)");
        expect(params).toEqual([1, 1]);
        return [{
          prefix: "MATH",
          number: "170A",
          title: "Calculus",
          min_units: 4,
          max_units: 4,
        }];
      }
      throw new Error(`Unexpected SQL: ${sql}`);
    });

    const response = await buildCourseResponse({ DB: db }, "CSUFULL");

    expect(response.status).toBe(200);
    expect((await response.json()).courses).toHaveLength(1);
  });

  it("rejects a course that is absent from the university's current year", async () => {
    const db = fakeD1((sql) => {
      if (sql.includes("FROM institutions")) {
        return [{ id: 1, code: "CSUFULL", name: "Cal State Fullerton" }];
      }
      if (sql.includes("MAX(academic_year_id)")) return [{ year_id: 76 }];
      if (sql.includes("FROM courses")) {
        expect(sql).toContain("is_terminated = 0");
        expect(sql).toContain("a.academic_year_id = ?");
        return [];
      }
      throw new Error(`Unexpected SQL: ${sql}`);
    });

    const response = await buildReverseResponse({ DB: db }, {
      ...query,
      prefix: "HIST",
      number: "101",
    });

    expect(response.status).toBe(404);
    expect((await response.json()).error).toContain("No HIST 101");
  });

  it("builds a reverse response through the production query path", async () => {
    const db = fakeD1((sql) => {
      if (sql.includes("FROM institutions") && !sql.includes("JOIN institutions")) {
        return [{ id: 1, code: "CSUFULL", name: "Cal State Fullerton" }];
      }
      if (sql.includes("MAX(academic_year_id)")) return [{ year_id: 76 }];
      if (sql.includes("FROM courses") && sql.includes("number = ?")) {
        return [{
          id: 10,
          prefix: "MATH",
          number: "170A",
          title: "Calculus",
          min_units: 4,
          max_units: 4,
        }];
      }
      if (sql.includes("cc_course_id")) {
        return [{
          cc_code: "TESTCC",
          cc_name: "Test College",
          cc_course_id: 20,
          cc_prefix: "MATH",
          cc_number: "1A",
          cc_title: "Calculus I",
          min_units: 4,
          max_units: 4,
          is_standalone_equivalent: 1,
          companion_course_ids: "[]",
          receiving_companion_course_ids: null,
          cc_institution_id: 2,
          sources_csv: "AllDepartments",
          academic_year_id: 76,
        }];
      }
      if (sql.includes("no_articulation_reason")) {
        return [{
          cc_code: "NOART",
          cc_name: "No Articulation College",
          no_articulation_reason: "No Course Articulated",
        }];
      }
      throw new Error(`Unexpected SQL: ${sql}`);
    });

    const response = await buildReverseResponse({ DB: db }, query);
    const payload = await response.json();

    expect(response.status).toBe(200);
    expect(payload.query.academic_year_id).toBe(76);
    expect(payload.results).toHaveLength(1);
    expect(payload.results[0].cc_code).toBe("TESTCC");
    expect(payload.no_articulation).toEqual([{
      cc_code: "NOART",
      cc_name: "No Articulation College",
      reason: "No Course Articulated",
    }]);
  });

  it("rejects malformed and excessive term filters", async () => {
    const invalid = parseQuery(new Request(
      "https://freecreds.pages.dev/api/reverse"
      + "?university=CSUFULL&prefix=MATH&number=170A&term=BAD",
    ));
    expect(invalid).toBeInstanceOf(Response);
    expect((invalid as Response).status).toBe(400);

    const excessive = parseQuery(new Request(
      "https://freecreds.pages.dev/api/reverse"
      + "?university=CSUFULL&prefix=MATH&number=170A"
      + "&term=FA26&term=WI27&term=SP27&term=SU27&term=FA27",
    ));
    expect(excessive).toBeInstanceOf(Response);
    expect((excessive as Response).status).toBe(400);
  });

  it("ignores malformed and non-integer companion metadata", () => {
    expect(parseCompanionIds("not json")).toEqual([]);
    expect(parseCompanionIds('[true, 12, 13.5, "14"]')).toEqual([12]);
  });

  it("builds cache versions from completed ingestion timestamps", async () => {
    const db = fakeD1(() => [
      { kind: "assist", finished_at: "2026-07-01T10:00:00Z" },
      { kind: "cvc", finished_at: "2026-07-15T10:00:00Z" },
    ]);

    await expect(latestIngestVersion(db, ["assist", "cvc"]))
      .resolves.toBe(
        "assist:2026-07-01T10:00:00Z|cvc:2026-07-15T10:00:00Z",
      );
  });
});
