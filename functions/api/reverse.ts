import type {
  CourseRow,
  InstitutionRow,
  NoArticulationRow,
  OfferingRow,
  ReverseIndexRow,
  TermRow,
} from "../_shared/api-types";
import {
  allRows,
  chunks,
  firstRow,
  placeholders,
  uniqueNumbers,
} from "../_shared/d1";
import {
  error,
  type Env,
  json,
  optionalStringParam,
  parseBooleanParam,
  requireStringParam,
  safeScheduleUrl,
} from "../_shared/http";
import { academicYearLabel, parseTermCode } from "../_shared/terms";

const MODALITY_RANK: Record<string, number> = {
  online_mixed: 1,
  online_sync: 2,
  online_async: 3,
};

const UNIVERSITY_PARAM = {
  maxLength: 16,
  pattern: /^[A-Z0-9]+$/,
  description: "an institution code such as CSUFULL",
};

const PREFIX_PARAM = {
  maxLength: 12,
  pattern: /^[A-Z&/]+$/,
  description: "a course prefix such as MATH",
};

const NUMBER_PARAM = {
  maxLength: 16,
  pattern: /^[A-Z0-9.-]+$/,
  description: "a course number such as 170A",
};

const TERM_PARAM = {
  maxLength: 4,
  pattern: /^(SP|SU|FA|WI)\d{2}$/,
  description: "a canonical term code such as FA26",
};

interface ReverseQuery {
  university: string;
  prefix: string;
  number: string;
  standaloneOnly: boolean;
  asyncOnly: boolean;
  termCode: string | null;
}

interface CoursePayload {
  prefix: string;
  number: string;
  title: string;
  min_units: number | null;
  max_units: number | null;
}

type ReceivingCourseRow = CourseRow & { id: number };

function isReverseIndexRow(row: CourseRow | ReverseIndexRow): row is ReverseIndexRow {
  return typeof (row as ReverseIndexRow).cc_prefix === "string";
}

function courseRowToObj(row: CourseRow | ReverseIndexRow): CoursePayload {
  if (isReverseIndexRow(row)) {
    return {
      prefix: row.cc_prefix,
      number: row.cc_number,
      title: row.cc_title,
      min_units: row.min_units,
      max_units: row.max_units,
    };
  }
  return {
    prefix: row.prefix,
    number: row.number,
    title: row.title,
    min_units: row.min_units,
    max_units: row.max_units,
  };
}

function parseQuery(request: Request): ReverseQuery | Response {
  const url = new URL(request.url);

  const university = requireStringParam(url, "university", UNIVERSITY_PARAM);
  if (university instanceof Response) return university;

  const prefix = requireStringParam(url, "prefix", PREFIX_PARAM);
  if (prefix instanceof Response) return prefix;

  const number = requireStringParam(url, "number", NUMBER_PARAM);
  if (number instanceof Response) return number;

  const termCode = optionalStringParam(url, "term", TERM_PARAM);
  if (termCode instanceof Response) return termCode;

  return {
    university,
    prefix,
    number,
    termCode,
    standaloneOnly: parseBooleanParam(url, "standalone_only"),
    asyncOnly: parseBooleanParam(url, "async_only"),
  };
}

function offeringStatus(modality: string | null | undefined): string {
  if (modality === "online_async") return "async_online";
  if (modality === "online_sync" || modality === "online_mixed") return "online_sync";
  return "unknown";
}

function preferredModality(existing: string | undefined, candidate: string): string {
  if (!existing) return candidate;
  if ((MODALITY_RANK[candidate] || 0) > (MODALITY_RANK[existing] || 0)) {
    return candidate;
  }
  return existing;
}

function parseCompanionIds(value: string | null): number[] {
  if (!value) return [];
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is number => (
      typeof item === "number" && Number.isInteger(item)
    ));
  } catch {
    return [];
  }
}

async function getReceivingCourse(
  env: Env,
  uni: InstitutionRow,
  query: ReverseQuery,
): Promise<ReceivingCourseRow | Response> {
  const course = await firstRow<ReceivingCourseRow>(
    env.DB.prepare(`
      SELECT id, prefix, number, title, min_units, max_units
      FROM courses
      WHERE institution_id = ? AND prefix = ? COLLATE NOCASE
        AND number = ? COLLATE NOCASE
    `).bind(uni.id, query.prefix, query.number),
  );
  if (course) {
    return course;
  }

  const candidates = await allRows<CourseRow>(
    env.DB.prepare(`
      SELECT prefix, number, title, min_units, max_units
      FROM courses
      WHERE institution_id = ? AND prefix = ? COLLATE NOCASE
      ORDER BY number
      LIMIT 20
    `).bind(uni.id, query.prefix),
  );

  return json({
    error: `No ${query.prefix} ${query.number} at ${uni.code.trim()}`,
    did_you_mean: candidates,
  }, { status: 404 });
}

async function resolveTerm(
  env: Env,
  requestedCode: string | null,
): Promise<{ id: number | null; code: string | null; label: string | null } | Response> {
  if (!requestedCode) {
    return { id: null, code: null, label: null };
  }

  const parsed = parseTermCode(requestedCode);
  if (!parsed) {
    return error(400, `Invalid term code: ${JSON.stringify(requestedCode)}`);
  }

  const term = await firstRow<TermRow>(
    env.DB.prepare("SELECT id, label FROM terms WHERE code = ?").bind(parsed.code),
  );

  return {
    id: term?.id ?? null,
    code: parsed.code,
    label: term?.label ?? parsed.label,
  };
}

async function queryReverseRows(
  env: Env,
  courseId: number,
  yearId: number | null,
  standaloneOnly: boolean,
): Promise<ReverseIndexRow[]> {
  let sql = `
    SELECT cc.code AS cc_code, cc.name AS cc_name,
           cc.schedule_url AS cc_schedule_url,
           c_cc.id AS cc_course_id,
           c_cc.prefix AS cc_prefix, c_cc.number AS cc_number,
           c_cc.title AS cc_title,
           c_cc.min_units, c_cc.max_units,
           ri.is_standalone_equivalent, ri.companion_course_ids,
           ri.receiving_companion_course_ids,
           ri.sending_cc_id AS cc_institution_id,
           GROUP_CONCAT(DISTINCT ri.source_context) AS sources_csv,
           ri.academic_year_id AS academic_year_id
    FROM reverse_index ri
    JOIN institutions cc ON cc.id = ri.sending_cc_id
    JOIN courses c_cc ON c_cc.id = ri.sending_course_id
    WHERE ri.receiving_course_id = ?
  `;
  const params: Array<string | number> = [courseId];

  if (yearId != null) {
    sql += " AND ri.academic_year_id = ?";
    params.push(yearId);
  }
  if (standaloneOnly) {
    sql += " AND ri.is_standalone_equivalent = 1";
  }
  sql += `
    GROUP BY cc.id, c_cc.id, ri.is_standalone_equivalent,
             ri.companion_course_ids, ri.receiving_companion_course_ids
    ORDER BY cc.name, c_cc.prefix, c_cc.number
  `;

  return await allRows<ReverseIndexRow>(env.DB.prepare(sql).bind(...params));
}

async function resolveOfferingTermIds(
  env: Env,
  termId: number | null,
  asyncOnly: boolean,
): Promise<number[]> {
  if (termId != null) {
    return [termId];
  }
  if (!asyncOnly) {
    return [];
  }
  const rows = await allRows<{ id: number }>(env.DB.prepare("SELECT id FROM terms"));
  return rows.map((row) => row.id);
}

async function queryOfferings(
  env: Env,
  termIds: number[],
  rows: ReverseIndexRow[],
): Promise<Map<string, string>> {
  const offerings = new Map<string, string>();
  if (!termIds.length || !rows.length) {
    return offerings;
  }

  const institutionIds = uniqueNumbers(rows.map((row) => row.cc_institution_id));
  for (const instChunk of chunks(institutionIds, 80)) {
    const termMarks = placeholders(termIds.length);
    const instMarks = placeholders(instChunk.length);
    const offRows = await allRows<OfferingRow>(
      env.DB.prepare(`
        SELECT institution_id, UPPER(prefix) AS prefix,
               UPPER(number) AS number, modality
        FROM class_offerings
        WHERE term_id IN (${termMarks}) AND institution_id IN (${instMarks})
      `).bind(...termIds, ...instChunk),
    );
    for (const offering of offRows) {
      const key = offeringKey(offering.institution_id, offering.prefix, offering.number);
      offerings.set(key, preferredModality(offerings.get(key), offering.modality));
    }
  }

  return offerings;
}

async function queryCompanions(
  env: Env,
  parsedRows: Array<{
    row: ReverseIndexRow;
    companionIds: number[];
    receivingCompanionIds: number[];
  }>,
): Promise<Map<number, CoursePayload>> {
  const companionMap = new Map<number, CoursePayload>();
  const allIds = uniqueNumbers(
    parsedRows.flatMap((item) => [...item.companionIds, ...item.receivingCompanionIds]),
  );

  for (const idChunk of chunks(allIds, 80)) {
    const rows = await allRows<ReceivingCourseRow>(
      env.DB.prepare(`
        SELECT id, prefix, number, title, min_units, max_units
        FROM courses
        WHERE id IN (${placeholders(idChunk.length)})
      `).bind(...idChunk),
    );
    for (const row of rows) {
      companionMap.set(row.id, courseRowToObj(row));
    }
  }

  return companionMap;
}

async function queryNoArticulation(
  env: Env,
  courseId: number,
  yearId: number | null,
): Promise<NoArticulationRow[]> {
  let sql = `
    SELECT DISTINCT cc.code AS cc_code, cc.name AS cc_name,
           a.no_articulation_reason
    FROM articulations a
    JOIN institutions cc ON cc.id = a.sending_cc_id
    WHERE a.receiving_course_id = ?
      AND a.no_articulation_reason IS NOT NULL
  `;
  const params: number[] = [courseId];

  if (yearId != null) {
    sql += " AND a.academic_year_id = ?";
    params.push(yearId);
  }
  sql += `
      AND NOT EXISTS (
        SELECT 1 FROM reverse_index ri
        WHERE ri.receiving_course_id = a.receiving_course_id
          AND ri.sending_cc_id = a.sending_cc_id
          AND ri.academic_year_id = a.academic_year_id
      )
    ORDER BY cc.name
  `;

  return await allRows<NoArticulationRow>(env.DB.prepare(sql).bind(...params));
}

function offeringKey(institutionId: number, prefix: string, number: string): string {
  return `${institutionId}|${prefix.toUpperCase()}|${number.toUpperCase()}`;
}

export const onRequestGet: PagesFunction<Env> = async ({ env, request }) => {
  const query = parseQuery(request);
  if (query instanceof Response) {
    return query;
  }

  const uni = await firstRow<InstitutionRow>(
    env.DB.prepare(
      "SELECT id, code, name FROM institutions WHERE code = ? COLLATE NOCASE",
    ).bind(query.university),
  );
  if (!uni) {
    return error(404, `Unknown university code ${JSON.stringify(query.university)}`);
  }

  const course = await getReceivingCourse(env, uni, query);
  if (course instanceof Response) {
    return course;
  }

  const year = await firstRow<{ year_id: number | null }>(
    env.DB.prepare(`
      SELECT MAX(academic_year_id) AS year_id FROM articulations
      WHERE receiving_course_id = ?
    `).bind(course.id),
  );
  const yearId = year?.year_id ?? null;

  const term = await resolveTerm(env, query.termCode);
  if (term instanceof Response) {
    return term;
  }

  const rows = term.code && term.id == null
    ? []
    : await queryReverseRows(env, course.id, yearId, query.standaloneOnly);
  const offeringTermIds = await resolveOfferingTermIds(env, term.id, query.asyncOnly);
  const offerings = await queryOfferings(env, offeringTermIds, rows);

  const parsedRows = rows.map((row) => ({
    row,
    companionIds: parseCompanionIds(row.companion_course_ids),
    receivingCompanionIds: parseCompanionIds(row.receiving_companion_course_ids),
  }));
  const companionMap = await queryCompanions(env, parsedRows);

  const results = [];
  for (const { row, companionIds, receivingCompanionIds } of parsedRows) {
    const modality = offeringTermIds.length
      ? offerings.get(offeringKey(row.cc_institution_id, row.cc_prefix, row.cc_number))
      : undefined;

    if (term.id != null && !modality) {
      continue;
    }
    if (query.asyncOnly && modality !== "online_async") {
      continue;
    }

    const sources = (row.sources_csv || "").split(",").filter(Boolean);
    results.push({
      cc_code: row.cc_code.trim(),
      cc_name: row.cc_name,
      cc_course: courseRowToObj(row),
      is_standalone: Boolean(row.is_standalone_equivalent),
      companion_courses: companionIds.map((id) => companionMap.get(id) || { id }),
      receiving_companion_courses: receivingCompanionIds.map(
        (id) => companionMap.get(id) || { id },
      ),
      offering_status: term.id != null ? offeringStatus(modality) : "unknown",
      schedule_url: safeScheduleUrl(row.cc_schedule_url),
      sources,
      academic_year_id: row.academic_year_id,
      academic_year: academicYearLabel(row.academic_year_id),
    });
  }

  const noArtRows = await queryNoArticulation(env, course.id, yearId);

  return json({
    query: {
      university: { code: uni.code.trim(), name: uni.name },
      course: courseRowToObj(course),
      academic_year_id: yearId,
      term: term.code ? { code: term.code, label: term.label } : null,
      async_only: query.asyncOnly,
    },
    results,
    no_articulation: noArtRows.map((row) => ({
      cc_code: row.cc_code.trim(),
      cc_name: row.cc_name,
      reason: row.no_articulation_reason,
    })),
  });
};
