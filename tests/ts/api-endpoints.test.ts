import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { buildCourseResponse } from "../../functions/api/courses";
import { onRequestGet as getStatus } from "../../functions/api/status";
import {
  buildReverseResponse,
  parseCompanionIds,
  parseQuery,
  type ReverseQuery,
} from "../../functions/api/reverse";
import { latestIngestVersion } from "../../functions/_shared/ingest-version";
import { createTestDatabase } from "./d1-sqlite";

const query: ReverseQuery = {
  university: "CSUFULL",
  prefix: "MATH",
  number: "170A",
  standaloneOnly: true,
  asyncOnly: false,
  termCodes: [],
};

let database: ReturnType<typeof createTestDatabase>;

beforeEach(() => {
  database = createTestDatabase();
  database.sqlite.exec(`
    INSERT INTO institutions (id, assist_id, code, name, category, term_type) VALUES
      (1, 100, 'CSUFULL', 'Cal State Fullerton', 'CSU', 'Semester'),
      (2, 200, 'TESTCC', 'Test College', 'CCC', 'Semester'),
      (3, 300, 'NOART', 'No Articulation College', 'CCC', 'Semester');
    INSERT INTO courses
      (id, institution_id, course_identifier_parent_id, prefix, number, title, is_terminated)
    VALUES
      (10, 1, 10, 'MATH', '170A', 'Calculus', 0),
      (11, 1, 11, 'HIST', '101', 'Old History', 0),
      (12, 1, 12, 'MATH', '999', 'Terminated Math', 1),
      (20, 2, 20, 'MATH', '1A', 'Calculus I', 0);
    INSERT INTO articulations
      (receiving_course_id, sending_cc_id, university_id, academic_year_id, no_articulation_reason)
    VALUES (10, 2, 1, 76, NULL), (11, 2, 1, 75, NULL),
      (12, 2, 1, 76, NULL), (10, 3, 1, 76, 'No Course Articulated');
    INSERT INTO reverse_index
      (receiving_course_id, sending_cc_id, sending_course_id,
       is_standalone_equivalent, companion_course_ids, academic_year_id)
    VALUES (10, 2, 20, 1, '[]', 76);
  `);
});

afterEach(() => database.sqlite.close());

describe("D1 API queries executed against SQLite", () => {
  it("lists only current, non-terminated courses", async () => {
    const response = await buildCourseResponse({ DB: database.db }, "csufull");
    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload.courses.map((course) => [course.prefix, course.number]))
      .toEqual([["MATH", "170A"]]);
  });

  it.each([['HIST', '101'], ['MATH', '999']])(
    "rejects unavailable or terminated course %s %s",
    async (prefix, number) => {
      const response = await buildReverseResponse({ DB: database.db }, {
        ...query, prefix, number,
      });
      expect(response.status).toBe(404);
      const payload = await response.json();
      expect(payload.results).toBeUndefined();
    },
  );

  it("returns articulations and colleges with no articulation", async () => {
    const response = await buildReverseResponse({ DB: database.db }, query);
    const payload = await response.json();
    expect(response.status).toBe(200);
    expect(payload.query.academic_year_id).toBe(76);
    expect(payload.results.map((row) => row.cc_code)).toEqual(["TESTCC"]);
    const assistUrl = new URL(payload.results[0].assist_url);
    expect(assistUrl.origin + assistUrl.pathname).toBe("https://assist.org/transfer/results");
    expect(Object.fromEntries(assistUrl.searchParams)).toEqual({
      year: "76", institution: "200", agreement: "100", agreementType: "to",
      viewAgreementsOptions: "true", view: "agreement",
    });
    expect(payload.no_articulation.map((row) => row.cc_code)).toEqual(["NOART"]);
  });

  it("only includes CVC offerings for the selected term", async () => {
    database.sqlite.exec(`
      INSERT INTO terms (id, code, label, season, year) VALUES
        (101, 'FA26', 'Fall 2026', 'Fall', 2026),
        (102, 'SP27', 'Spring 2027', 'Spring', 2027);
      INSERT INTO class_offerings
        (institution_id, prefix, number, term_id, modality, source, source_ref, fetched_at)
      VALUES (2, 'MATH', '1A', 101, 'online_async', 'cvc',
        'https://search.cvc.edu/courses/1842959', '2026-07-01'),
        (2, 'MATH', '1A', 102, 'online_sync', 'cvc',
        'https://search.cvc.edu/courses/other-term', '2026-07-01');
    `);
    const response = await buildReverseResponse({ DB: database.db }, {
      ...query, termCodes: ["FA26"],
    });
    const payload = await response.json();
    expect(payload.results[0].offering_terms).toEqual([{
      code: "FA26",
      label: "Fall 2026",
      status: "async_online",
      source_ref: "https://search.cvc.edu/courses/1842959",
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

  it("invalidates cache versions only after a completed refresh", async () => {
    const version = () => latestIngestVersion(database.db, ["assist", "cvc"]);
    const initial = await version();
    database.sqlite.exec(`
      INSERT INTO ingest_jobs (id, kind, status, requested_by, started_at, finished_at)
      VALUES ('job', 'cvc', 'failed', 'test', '2026-07-15', '2026-07-15T10:00:00Z');
    `);
    expect(await version()).toBe(initial);
    database.sqlite.exec("UPDATE ingest_jobs SET status = 'completed' WHERE id = 'job'");
    expect(await version()).not.toBe(initial);
  });

  it("reports the latest successful refresh, ignoring newer failures", async () => {
    database.sqlite.exec(`
      INSERT INTO ingest_jobs (id, kind, status, requested_by, started_at, finished_at)
      VALUES ('older', 'cvc', 'completed', 'test', '2026-07-01', '2026-07-01T10:00:00Z'),
        ('latest', 'cvc', 'completed', 'test', '2026-07-15', '2026-07-15T10:00:00Z'),
        ('failed', 'cvc', 'failed', 'test', '2026-08-01', '2026-08-01T10:00:00Z');
    `);
    const response = await getStatus({
      env: { DB: database.db },
    } as Parameters<typeof getStatus>[0]);
    expect((await response.json()).updated_at).toEqual({
      assist: null,
      cvc: "2026-07-15T10:00:00Z",
    });
  });
});
