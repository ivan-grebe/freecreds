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
  placeholders,
  uniqueNumbers,
} from "../_shared/d1";
import {
  cachedResponse,
  error,
  type Env,
  json,
  parseBooleanParam,
  requireStringParam,
} from "../_shared/http";
import { latestIngestVersion } from "../_shared/ingest-version";
import { academicYearLabel, parseTermCode, upcomingTerms } from "../_shared/terms";

const MODALITY_RANK: Record<string, number> = {
  online_mixed: 1,
  online_sync: 2,
  online_async: 3,
};
const REVERSE_CACHE_SECONDS = 60 * 60 * 12;

interface OfferingIndexes {
  modalities: Map<string, Map<number, string>>;
  sourceRefs: Map<string, Map<number, string>>;
}

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

export interface ReverseQuery {
  university: string;
  prefix: string;
  number: string;
  standaloneOnly: boolean;
  asyncOnly: boolean;
  termCodes: string[];
}

interface ResolvedTerm {
  id: number | null;
  code: string;
  label: string;
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

export function parseQuery(request: Request): ReverseQuery | Response {
  const url = new URL(request.url);

  const university = requireStringParam(url, "university", UNIVERSITY_PARAM);
  if (university instanceof Response) return university;

  const prefix = requireStringParam(url, "prefix", PREFIX_PARAM);
  if (prefix instanceof Response) return prefix;

  const number = requireStringParam(url, "number", NUMBER_PARAM);
  if (number instanceof Response) return number;

  const termCodes: string[] = [];
  for (const value of url.searchParams.getAll("term")) {
    const normalized = value.trim().toUpperCase();
    if (!normalized) continue;
    if (normalized.length > TERM_PARAM.maxLength || !TERM_PARAM.pattern.test(normalized)) {
      return error(400, `Invalid term; expected ${TERM_PARAM.description}`);
    }
    if (!termCodes.includes(normalized)) termCodes.push(normalized);
  }
  if (termCodes.length > 4) {
    return error(400, "Select no more than four terms");
  }

  return {
    university,
    prefix,
    number,
    termCodes,
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

function tuplePlaceholders(count: number): string {
  if (!Number.isInteger(count) || count <= 0) {
    throw new Error(`Invalid tuple placeholder count: ${count}`);
  }
  return Array.from({ length: count }, () => "(?, ?, ?)").join(",");
}

export function parseCompanionIds(value: string | null): number[] {
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
  yearId: number | null,
): Promise<ReceivingCourseRow | Response> {
  const course = await env.DB.prepare(`
    SELECT id, prefix, number, title, min_units, max_units
    FROM courses c
    WHERE c.institution_id = ? AND c.prefix = ? COLLATE NOCASE
      AND c.number = ? COLLATE NOCASE
      AND c.is_terminated = 0
      AND EXISTS (
        SELECT 1 FROM articulations a
        WHERE a.receiving_course_id = c.id
          AND a.university_id = ?
          AND a.academic_year_id = ?
      )
  `).bind(uni.id, query.prefix, query.number, uni.id, yearId).first<ReceivingCourseRow>();
  if (course) {
    return course;
  }

  const candidates = await allRows<CourseRow>(
    env.DB.prepare(`
      SELECT prefix, number, title, min_units, max_units
      FROM courses c
      WHERE c.institution_id = ? AND c.prefix = ? COLLATE NOCASE
        AND c.is_terminated = 0
        AND EXISTS (
          SELECT 1 FROM articulations a
          WHERE a.receiving_course_id = c.id
            AND a.university_id = ?
            AND a.academic_year_id = ?
        )
      ORDER BY c.number
      LIMIT 20
    `).bind(uni.id, query.prefix, uni.id, yearId),
  );

  return json({
    error: `No ${query.prefix} ${query.number} at ${uni.code.trim()}`,
    did_you_mean: candidates,
  }, { status: 404 });
}

async function resolveTerms(
  env: Env,
  requestedCodes: string[],
): Promise<ResolvedTerm[]> {
  if (!requestedCodes.length) return [];

  const rows = await allRows<TermRow & { code: string }>(
    env.DB.prepare(
      `SELECT id, code, label FROM terms WHERE code IN (${placeholders(requestedCodes.length)})`,
    ).bind(...requestedCodes),
  );
  const byCode = new Map(rows.map((row) => [row.code, row]));

  return requestedCodes.map((code) => {
    const parsed = parseTermCode(code);
    if (!parsed) throw new Error(`Validated term could not be parsed: ${code}`);
    const row = byCode.get(parsed.code);
    return { id: row?.id ?? null, code: parsed.code, label: row?.label ?? parsed.label };
  });
}

async function queryReverseRows(
  env: Env,
  courseId: number,
  yearId: number | null,
  standaloneOnly: boolean,
): Promise<ReverseIndexRow[]> {
  let sql = `
    SELECT cc.code AS cc_code, cc.name AS cc_name,
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
  selectedTermIds: number[],
  asyncOnly: boolean,
): Promise<number[]> {
  if (selectedTermIds.length) return selectedTermIds;
  if (!asyncOnly) {
    return [];
  }
  const codes = upcomingTerms(new Date(), 4).map((term) => term.code);
  const rows = await allRows<{ id: number }>(
    env.DB.prepare(
      `SELECT id FROM terms WHERE code IN (${placeholders(codes.length)})`,
    ).bind(...codes),
  );
  return rows.map((row) => row.id);
}

async function queryOfferings(
  env: Env,
  termIds: number[],
  lookupKeys: Array<{ institutionId: number; prefix: string; number: string }>,
): Promise<OfferingIndexes> {
  const modalities = new Map<string, Map<number, string>>();
  const sourceRefs = new Map<string, Map<number, string>>();
  if (!termIds.length || !lookupKeys.length) {
    return { modalities, sourceRefs };
  }

  const seen = new Map<string, { institutionId: number; prefix: string; number: string }>();
  for (const lookup of lookupKeys) {
    const prefix = lookup.prefix.toUpperCase();
    const number = lookup.number.toUpperCase();
    const key = offeringKey(lookup.institutionId, prefix, number);
    if (!seen.has(key)) {
      seen.set(key, { institutionId: lookup.institutionId, prefix, number });
    }
  }

  const uniqueKeys = [...seen.values()];
  const maxKeysPerQuery = Math.max(1, Math.floor((90 - termIds.length) / 3));
  for (const keyChunk of chunks(uniqueKeys, maxKeysPerQuery)) {
    const termMarks = placeholders(termIds.length);
    const keyMarks = tuplePlaceholders(keyChunk.length);
    const params: Array<string | number> = [...termIds];
    for (const key of keyChunk) {
      params.push(key.institutionId, key.prefix, key.number);
    }
    const offRows = await allRows<OfferingRow & { term_id: number }>(
      env.DB.prepare(`
        SELECT institution_id, UPPER(prefix) AS prefix,
               UPPER(number) AS number, term_id, modality, source_ref
        FROM class_offerings
        WHERE term_id IN (${termMarks})
          AND (institution_id, prefix, number) IN (VALUES ${keyMarks})
      `).bind(...params),
    );
    for (const offering of offRows) {
      const key = offeringKey(offering.institution_id, offering.prefix, offering.number);
      let byTerm = modalities.get(key);
      if (!byTerm) {
        byTerm = new Map<number, string>();
        modalities.set(key, byTerm);
      }
      const existingModality = byTerm.get(offering.term_id);
      const preferred = preferredModality(existingModality, offering.modality);
      byTerm.set(offering.term_id, preferred);

      if (!offering.source_ref) continue;
      let refsByTerm = sourceRefs.get(key);
      if (!refsByTerm) {
        refsByTerm = new Map<number, string>();
        sourceRefs.set(key, refsByTerm);
      }
      const existingRef = refsByTerm.get(offering.term_id);
      if (
        preferred === offering.modality
        && (preferred !== existingModality || !existingRef || offering.source_ref < existingRef)
      ) {
        refsByTerm.set(offering.term_id, offering.source_ref);
      }
    }
  }

  return { modalities, sourceRefs };
}

export function matchingBundleModality(
  offerings: Map<string, Map<number, string>>,
  keys: string[],
  termIds: number[],
  asyncOnly: boolean,
): string | undefined {
  for (const termId of termIds) {
    const modalities = keys.map((key) => offerings.get(key)?.get(termId));
    if (modalities.some((modality) => !modality)) continue;
    if (asyncOnly && modalities.some((modality) => modality !== "online_async")) continue;
    return modalities.reduce<string | undefined>(
      (chosen, modality) => modality ? preferredModality(chosen, modality) : chosen,
      undefined,
    );
  }
  return undefined;
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

export const onRequestGet: PagesFunction<Env> = async ({ env, request, waitUntil }) => {
  const query = parseQuery(request);
  if (query instanceof Response) {
    return query;
  }
  const version = await latestIngestVersion(env.DB, ["assist", "cvc"]);

  return cachedResponse(
    `${encodeURIComponent(version)}/${reverseCacheKey(query)}`,
    waitUntil,
    REVERSE_CACHE_SECONDS,
    () => buildReverseResponse(env, query),
  );
};

function reverseCacheKey(query: ReverseQuery): string {
  const params = new URLSearchParams({
    university: query.university,
    prefix: query.prefix,
    number: query.number,
    standalone: query.standaloneOnly ? "1" : "0",
    async: query.asyncOnly ? "1" : "0",
  });
  for (const termCode of [...query.termCodes].sort()) params.append("term", termCode);
  return `reverse-v2?${params.toString()}`;
}

export async function buildReverseResponse(env: Env, query: ReverseQuery): Promise<Response> {
  const uni = await env.DB.prepare(
    "SELECT id, code, name FROM institutions WHERE code = ? COLLATE NOCASE",
  ).bind(query.university).first<InstitutionRow>();
  if (!uni) {
    return error(404, `Unknown university code ${JSON.stringify(query.university)}`);
  }

  const year = await env.DB.prepare(`
    SELECT MAX(academic_year_id) AS year_id FROM articulations
    WHERE university_id = ?
  `).bind(uni.id).first<{ year_id: number | null }>();
  const yearId = year?.year_id ?? null;

  const course = await getReceivingCourse(env, uni, query, yearId);
  if (course instanceof Response) {
    return course;
  }

  const terms = await resolveTerms(env, query.termCodes);
  const selectedTermIds = terms.flatMap((term) => term.id == null ? [] : [term.id]);
  const rows = query.termCodes.length && !selectedTermIds.length
    ? []
    : await queryReverseRows(env, course.id, yearId, query.standaloneOnly);
  const offeringTermIds = await resolveOfferingTermIds(env, selectedTermIds, query.asyncOnly);

  const parsedRows = rows.map((row) => ({
    row,
    companionIds: parseCompanionIds(row.companion_course_ids),
    receivingCompanionIds: parseCompanionIds(row.receiving_companion_course_ids),
  }));
  const companionMap = await queryCompanions(env, parsedRows);
  const lookupKeys = rows.map((row) => ({
    institutionId: row.cc_institution_id,
    prefix: row.cc_prefix,
    number: row.cc_number,
  }));
  for (const { row, companionIds } of parsedRows) {
    for (const id of companionIds) {
      const companion = companionMap.get(id);
      if (companion) {
        lookupKeys.push({
          institutionId: row.cc_institution_id,
          prefix: companion.prefix,
          number: companion.number,
        });
      }
    }
  }
  const { modalities: offerings, sourceRefs } = await queryOfferings(
    env,
    offeringTermIds,
    lookupKeys,
  );

  const results = [];
  for (const { row, companionIds, receivingCompanionIds } of parsedRows) {
    const bundleKeys = [offeringKey(row.cc_institution_id, row.cc_prefix, row.cc_number)];
    for (const id of companionIds) {
      const companion = companionMap.get(id);
      if (companion) {
        bundleKeys.push(offeringKey(row.cc_institution_id, companion.prefix, companion.number));
      }
    }
    const offeringTerms = bundleKeys.length === companionIds.length + 1
      ? terms.flatMap((term) => {
        if (term.id == null) return [];
        const modality = matchingBundleModality(offerings, bundleKeys, [term.id], query.asyncOnly);
        return modality ? [{
          code: term.code,
          label: term.label,
          status: offeringStatus(modality),
          source_ref: sourceRefs.get(bundleKeys[0])?.get(term.id) ?? null,
        }] : [];
      })
      : [];
    const modality = query.termCodes.length
      ? offeringTerms.reduce<string | undefined>(
        (chosen, term) => preferredModality(
          chosen,
          term.status === "async_online" ? "online_async" : "online_sync",
        ),
        undefined,
      )
      : offeringTermIds.length && bundleKeys.length === companionIds.length + 1
        ? matchingBundleModality(offerings, bundleKeys, offeringTermIds, query.asyncOnly)
        : undefined;

    if (query.termCodes.length && !offeringTerms.length) {
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
      offering_status: query.termCodes.length ? offeringStatus(modality) : "unknown",
      offering_terms: offeringTerms,
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
      term: terms.length === 1 ? { code: terms[0].code, label: terms[0].label } : null,
      terms: terms.map((term) => ({ code: term.code, label: term.label })),
      async_only: query.asyncOnly,
    },
    results,
    no_articulation: noArtRows.map((row) => ({
      cc_code: row.cc_code.trim(),
      cc_name: row.cc_name,
      reason: row.no_articulation_reason,
    })),
  });
}
