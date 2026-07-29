import type { CourseRow, InstitutionRow } from "../_shared/api-types";
import { allRows, firstRow } from "../_shared/d1";
import { latestIngestVersion } from "../_shared/ingest-version";
import {
  cachedResponse,
  error,
  type Env,
  json,
  requireStringParam,
} from "../_shared/http";

const UNIVERSITY_PARAM = {
  maxLength: 16,
  pattern: /^[A-Z0-9]+$/,
  description: "an institution code such as CSUFULL",
};

const COURSE_LIST_CACHE_SECONDS = 60 * 60 * 24;

export const onRequestGet: PagesFunction<Env> = async ({ env, request, waitUntil }) => {
  const url = new URL(request.url);
  const university = requireStringParam(url, "university", UNIVERSITY_PARAM);
  if (university instanceof Response) {
    return university;
  }
  const version = await latestIngestVersion(env.DB, ["assist"]);

  return cachedResponse(
    `courses/${encodeURIComponent(version)}/${encodeURIComponent(university)}`,
    waitUntil,
    COURSE_LIST_CACHE_SECONDS,
    () => buildCourseResponse(env, university),
  );
};

export async function buildCourseResponse(env: Env, university: string): Promise<Response> {
  const uni = await firstRow<InstitutionRow>(
    env.DB.prepare(
      "SELECT id, code, name FROM institutions WHERE code = ? COLLATE NOCASE",
    ).bind(university),
  );
  if (!uni) {
    return error(404, `Unknown university code ${JSON.stringify(university)}`);
  }

  const courses = await allRows<CourseRow>(
    env.DB.prepare(`
      WITH latest_year AS (
        SELECT MAX(academic_year_id) AS id
        FROM articulations
        WHERE university_id = ?
      )
      SELECT DISTINCT c.prefix, c.number, c.title, c.min_units, c.max_units
      FROM courses c
      WHERE c.institution_id = ?
        AND c.is_terminated = 0
        AND EXISTS (
          SELECT 1 FROM articulations a
          WHERE a.receiving_course_id = c.id
            AND a.university_id = c.institution_id
            AND a.academic_year_id = (SELECT id FROM latest_year)
        )
      ORDER BY c.prefix, c.number
    `).bind(uni.id, uni.id),
  );

  return json({
    university: { code: uni.code.trim(), name: uni.name },
    courses,
  });
}
