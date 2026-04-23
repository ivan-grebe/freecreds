import type { CourseRow, InstitutionRow } from "../_shared/api-types";
import { allRows, firstRow } from "../_shared/d1";
import {
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

export const onRequestGet: PagesFunction<Env> = async ({ env, request }) => {
  const url = new URL(request.url);
  const university = requireStringParam(url, "university", UNIVERSITY_PARAM);
  if (university instanceof Response) {
    return university;
  }

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
      SELECT DISTINCT c.prefix, c.number, c.title, c.min_units, c.max_units
      FROM courses c
      WHERE c.institution_id = ?
        AND EXISTS (
          SELECT 1 FROM articulations a
          WHERE a.receiving_course_id = c.id
        )
      ORDER BY c.prefix, c.number
    `).bind(uni.id),
  );

  return json({
    university: { code: uni.code.trim(), name: uni.name },
    courses,
  });
};
