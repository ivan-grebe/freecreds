"""FastAPI app serving reverse-lookup queries + the static frontend."""
from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .terms import academic_year_label, parse_code, upcoming_terms

DB_PATH = Path(__file__).parent.parent / "data" / "assist.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

MODALITY_RANK = {"online_mixed": 1, "online_sync": 2, "online_async": 3}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _run_startup_tasks() -> None:
    """Run lightweight migrations and ensure upcoming terms exist in the DB.

    The terms upsert matters for the reverse-lookup endpoint: it resolves
    a term code to an ID to join against class_offerings. If a term is
    missing from the table, the endpoint silently drops the term filter
    — which makes the UI's "Offered" column disappear.

    Both steps are idempotent — safe to run on every startup.
    """
    if not DB_PATH.parent.exists():
        return
    conn = _conn()
    try:
        db.init_db(conn)
        for t in upcoming_terms(date.today(), count=4):
            db.upsert_term(conn, code=t.code, label=t.label, season=t.season, year=t.year)
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    _run_startup_tasks()
    yield


app = FastAPI(title="Reverse ASSIST Search", lifespan=_lifespan)


@app.get("/api/terms")
def list_terms() -> dict[str, Any]:
    """Four upcoming terms, starting from today. Clients populate the
    term-filter dropdown from this. Four covers the full CCC calendar
    (Spring / Summer / Fall / Winter intersession) from any start date.
    """
    terms = [t.to_dict() for t in upcoming_terms(date.today(), count=4)]
    return {"terms": terms}


@app.get("/api/courses")
def list_courses(
    university: str = Query(..., description="Institution code, e.g. CSUFULL"),
) -> dict[str, Any]:
    conn = _conn()
    try:
        uni = conn.execute(
            "SELECT id, code, name FROM institutions WHERE code = ? COLLATE NOCASE",
            (university.strip(),),
        ).fetchone()
        if not uni:
            raise HTTPException(404, f"Unknown university code {university!r}")
        rows = conn.execute(
            """SELECT DISTINCT c.prefix, c.number, c.title, c.min_units, c.max_units
               FROM courses c
               WHERE c.institution_id = ?
                 AND EXISTS (
                   SELECT 1 FROM articulations a
                   WHERE a.receiving_course_id = c.id
                 )
               ORDER BY c.prefix, c.number""",
            (uni["id"],),
        ).fetchall()
        return {
            "university": {"code": uni["code"].strip(), "name": uni["name"]},
            "courses": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _course_row_to_obj(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "prefix": row["prefix"],
        "number": row["number"],
        "title": row["title"],
        "min_units": row["min_units"],
        "max_units": row["max_units"],
    }


def _offering_status(modality: str | None) -> str:
    """Map internal modality to the status string surfaced in the API.

    'online_mixed' collapses to 'online_sync' for the purpose of the
    async-only filter — we only surface 'async_online' when we're
    confident it's actually async.
    """
    if modality == "online_async":
        return "async_online"
    if modality in ("online_sync", "online_mixed"):
        return "online_sync"
    return "unknown"


def _preferred_modality(existing: str | None, candidate: str) -> str:
    """Keep the most useful modality when duplicate offerings exist."""
    if existing is None:
        return candidate
    if MODALITY_RANK.get(candidate, 0) > MODALITY_RANK.get(existing, 0):
        return candidate
    return existing


def _resolve_requested_term(
    conn: sqlite3.Connection,
    requested_term: str | None,
) -> tuple[str | None, int | None, str | None]:
    """Return normalized (code, id, label) for a requested term.

    Unknown-but-valid term codes are inserted on demand so subsequent
    offering lookups can use the normal terms join path.
    """
    if not requested_term:
        return None, None, None

    code_norm = requested_term.strip().upper()
    term_row = conn.execute(
        "SELECT id, label FROM terms WHERE code = ?", (code_norm,)
    ).fetchone()
    if term_row:
        return code_norm, term_row["id"], term_row["label"]

    try:
        parsed = parse_code(code_norm)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    term_id = db.upsert_term(
        conn,
        code=parsed.code,
        label=parsed.label,
        season=parsed.season,
        year=parsed.year,
    )
    conn.commit()
    return parsed.code, term_id, parsed.label


ResolvedTerm = tuple[str, int, str]


def _resolve_requested_terms(
    conn: sqlite3.Connection,
    requested_terms: list[str] | None,
) -> list[ResolvedTerm]:
    unique_terms: list[str] = []
    for value in requested_terms or []:
        normalized = value.strip().upper()
        if normalized and normalized not in unique_terms:
            unique_terms.append(normalized)
    if len(unique_terms) > 4:
        raise HTTPException(400, "Select no more than four terms")

    resolved: list[ResolvedTerm] = []
    for value in unique_terms:
        code, term_id, label = _resolve_requested_term(conn, value)
        if code is not None and term_id is not None and label is not None:
            resolved.append((code, term_id, label))
    return resolved


def _offering_key(row: sqlite3.Row) -> tuple[int, str, str]:
    return (row["cc_institution_id"], row["cc_prefix"].upper(), row["cc_number"].upper())


def _query_offerings(
    conn: sqlite3.Connection,
    offering_keys: list[tuple[int, str, str]],
    query_term_ids: list[int],
) -> dict[tuple[int, str, str], dict[int, str]]:
    """Fetch offering modality for all reverse rows without per-row queries."""
    offerings_map: dict[tuple[int, str, str], dict[int, str]] = {}
    if not query_term_ids or not offering_keys:
        return offerings_map

    offering_keys = sorted(set(offering_keys))
    t_marks = ",".join(["?"] * len(query_term_ids))
    # Keep under SQLite's common 999-variable limit while still letting the
    # composite offerings index do point lookups.
    max_keys_per_query = max(1, (900 - len(query_term_ids)) // 3)
    for start in range(0, len(offering_keys), max_keys_per_query):
        key_chunk = offering_keys[start:start + max_keys_per_query]
        key_marks = ",".join(["(?, ?, ?)"] * len(key_chunk))
        params: list[Any] = [*query_term_ids]
        for inst_id, prefix_key, number_key in key_chunk:
            params.extend([inst_id, prefix_key, number_key])
        off_rows = conn.execute(
            f"""SELECT institution_id, UPPER(prefix) AS prefix,
                       UPPER(number) AS number, term_id, modality
                FROM class_offerings
                WHERE term_id IN ({t_marks})
                  AND (institution_id, prefix, number) IN (VALUES {key_marks})""",
            params,
        ).fetchall()
        for o in off_rows:
            key = (o["institution_id"], o["prefix"], o["number"])
            by_term = offerings_map.setdefault(key, {})
            by_term[o["term_id"]] = _preferred_modality(
                by_term.get(o["term_id"]), o["modality"]
            )
    return offerings_map


def _matching_bundle_modality(
    offerings: dict[tuple[int, str, str], dict[int, str]],
    keys: list[tuple[int, str, str]],
    term_ids: list[int],
    async_only: bool,
) -> str | None:
    """Return a modality only when every course is available in one term."""
    for term_id in term_ids:
        modalities = [offerings.get(key, {}).get(term_id) for key in keys]
        if any(modality is None for modality in modalities):
            continue
        if async_only and any(modality != "online_async" for modality in modalities):
            continue
        chosen: str | None = None
        for modality in modalities:
            if modality is not None:
                chosen = _preferred_modality(chosen, modality)
        return chosen
    return None


def _parse_id_list(value: str | None) -> list[int]:
    # Keep behavior aligned with parseCompanionIds in functions/api/reverse.ts.
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [
        item
        for item in parsed
        if isinstance(item, int) and not isinstance(item, bool)
    ]


def _find_university(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    university = conn.execute(
        "SELECT id, code, name FROM institutions WHERE code = ? COLLATE NOCASE",
        (code.strip(),),
    ).fetchone()
    if not university:
        raise HTTPException(404, f"Unknown university code {code!r}")
    return university


def _find_receiving_course(
    conn: sqlite3.Connection,
    university: sqlite3.Row,
    prefix: str,
    number: str,
) -> sqlite3.Row | JSONResponse:
    course = conn.execute(
        """SELECT id, prefix, number, title, min_units, max_units
           FROM courses
           WHERE institution_id = ? AND prefix = ? COLLATE NOCASE
             AND number = ? COLLATE NOCASE""",
        (university["id"], prefix.strip(), number.strip()),
    ).fetchone()
    if course:
        return course
    candidates = conn.execute(
        """SELECT prefix, number, title FROM courses
           WHERE institution_id = ? AND prefix = ? COLLATE NOCASE
           ORDER BY number LIMIT 20""",
        (university["id"], prefix.strip()),
    ).fetchall()
    return JSONResponse(
        status_code=404,
        content={
            "error": f"No {prefix} {number} at {university['code'].strip()}",
            "did_you_mean": [dict(candidate) for candidate in candidates],
        },
    )


def _latest_academic_year(conn: sqlite3.Connection, course_id: int) -> int | None:
    row = conn.execute(
        """SELECT MAX(academic_year_id) AS year_id FROM articulations
           WHERE receiving_course_id = ?""",
        (course_id,),
    ).fetchone()
    return row["year_id"] if row else None


def _query_reverse_rows(
    conn: sqlite3.Connection,
    course_id: int,
    year_id: int | None,
    standalone_only: bool,
) -> list[sqlite3.Row]:
    sql = """
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
    """
    params: list[Any] = [course_id]
    if year_id is not None:
        sql += " AND ri.academic_year_id = ?"
        params.append(year_id)
    if standalone_only:
        sql += " AND ri.is_standalone_equivalent = 1"
    sql += (
        " GROUP BY cc.id, c_cc.id, ri.is_standalone_equivalent,"
        "          ri.companion_course_ids, ri.receiving_companion_course_ids"
        " ORDER BY cc.name, c_cc.prefix, c_cc.number"
    )
    return conn.execute(sql, params).fetchall()


def _resolve_offering_term_ids(
    conn: sqlite3.Connection,
    selected_term_ids: list[int],
    async_only: bool,
) -> list[int]:
    if selected_term_ids:
        return selected_term_ids
    if not async_only:
        return []
    upcoming_codes = [term.code for term in upcoming_terms(date.today(), count=4)]
    marks = ",".join(["?"] * len(upcoming_codes))
    return [
        row[0]
        for row in conn.execute(
            f"SELECT id FROM terms WHERE code IN ({marks})", upcoming_codes
        )
    ]


ParsedReverseRow = tuple[sqlite3.Row, list[int], list[int]]


def _parse_reverse_rows(rows: list[sqlite3.Row]) -> tuple[list[ParsedReverseRow], set[int]]:
    parsed_rows = []
    course_ids: set[int] = set()
    for row in rows:
        companions = _parse_id_list(row["companion_course_ids"])
        receiving_companions = _parse_id_list(row["receiving_companion_course_ids"])
        course_ids.update(companions)
        course_ids.update(receiving_companions)
        parsed_rows.append((row, companions, receiving_companions))
    return parsed_rows, course_ids


def _load_course_map(
    conn: sqlite3.Connection,
    course_ids: set[int],
) -> dict[int, dict[str, Any]]:
    if not course_ids:
        return {}
    marks = ",".join(["?"] * len(course_ids))
    rows = conn.execute(
        f"""SELECT id, prefix, number, title, min_units, max_units
            FROM courses WHERE id IN ({marks})""",
        list(course_ids),
    )
    return {row["id"]: _course_row_to_obj(row) for row in rows}


def _course_offering_key(
    institution_id: int,
    course: dict[str, Any],
) -> tuple[int, str, str]:
    return institution_id, course["prefix"].upper(), course["number"].upper()


def _collect_offering_keys(
    rows: list[sqlite3.Row],
    parsed_rows: list[ParsedReverseRow],
    course_map: dict[int, dict[str, Any]],
) -> list[tuple[int, str, str]]:
    keys = [_offering_key(row) for row in rows]
    for row, companions, _ in parsed_rows:
        keys.extend(
            _course_offering_key(row["cc_institution_id"], course_map[course_id])
            for course_id in companions
            if course_id in course_map
        )
    return keys


def _build_reverse_results(
    parsed_rows: list[ParsedReverseRow],
    course_map: dict[int, dict[str, Any]],
    offerings: dict[tuple[int, str, str], dict[int, str]],
    term_ids: list[int],
    selected_terms: list[ResolvedTerm],
    async_only: bool,
) -> list[dict[str, Any]]:
    results = []
    for row, companions, receiving_companions in parsed_rows:
        bundle_keys = [_offering_key(row)] + [
            _course_offering_key(row["cc_institution_id"], course_map[course_id])
            for course_id in companions
            if course_id in course_map
        ]
        complete_bundle = len(bundle_keys) == len(companions) + 1
        offering_terms = []
        for code, selected_term_id, label in selected_terms:
            modality = (
                _matching_bundle_modality(
                    offerings, bundle_keys, [selected_term_id], async_only
                )
                if complete_bundle
                else None
            )
            if modality is not None:
                offering_terms.append(
                    {"code": code, "label": label, "status": _offering_status(modality)}
                )

        modality: str | None = None
        if selected_terms:
            for offering_term in offering_terms:
                candidate = (
                    "online_async"
                    if offering_term["status"] == "async_online"
                    else "online_sync"
                )
                modality = _preferred_modality(modality, candidate)
        elif term_ids and complete_bundle:
            modality = _matching_bundle_modality(offerings, bundle_keys, term_ids, async_only)

        if selected_terms and not offering_terms:
            continue
        if async_only and modality != "online_async":
            continue
        results.append(
            {
                "cc_code": row["cc_code"].strip(),
                "cc_name": row["cc_name"],
                "cc_course": {
                    "prefix": row["cc_prefix"],
                    "number": row["cc_number"],
                    "title": row["cc_title"],
                    "min_units": row["min_units"],
                    "max_units": row["max_units"],
                },
                "is_standalone": bool(row["is_standalone_equivalent"]),
                "companion_courses": [
                    course_map.get(course_id, {"id": course_id}) for course_id in companions
                ],
                "receiving_companion_courses": [
                    course_map.get(course_id, {"id": course_id})
                    for course_id in receiving_companions
                ],
                "offering_status": _offering_status(modality) if selected_terms else "unknown",
                "offering_terms": offering_terms,
                "sources": [source for source in (row["sources_csv"] or "").split(",") if source],
                "academic_year_id": row["academic_year_id"],
                "academic_year": academic_year_label(row["academic_year_id"]),
            }
        )
    return results


def _query_no_articulation(
    conn: sqlite3.Connection,
    course_id: int,
    year_id: int | None,
) -> list[sqlite3.Row]:
    sql = """
      SELECT DISTINCT cc.code AS cc_code, cc.name AS cc_name,
             a.no_articulation_reason
      FROM articulations a
      JOIN institutions cc ON cc.id = a.sending_cc_id
      WHERE a.receiving_course_id = ?
        AND a.no_articulation_reason IS NOT NULL
    """
    params: list[Any] = [course_id]
    if year_id is not None:
        sql += " AND a.academic_year_id = ?"
        params.append(year_id)
    sql += """
        AND NOT EXISTS (
          SELECT 1 FROM reverse_index ri
          WHERE ri.receiving_course_id = a.receiving_course_id
            AND ri.sending_cc_id = a.sending_cc_id
            AND ri.academic_year_id = a.academic_year_id
        )
      ORDER BY cc.name
    """
    return conn.execute(sql, params).fetchall()


@app.get("/api/reverse")
def reverse_lookup(
    university: str = Query(..., description="Institution code"),
    prefix: str = Query(..., description="Course prefix (e.g. MATH)"),
    number: str = Query(..., description="Course number (e.g. 170B)"),
    standalone_only: bool = Query(False, description="Only return standalone equivalents"),
    term: Annotated[
        list[str] | None,
        Query(description="Repeat for each canonical term code"),
    ] = None,
    async_only: bool = Query(False, description="Return only rows confirmed as async online"),
) -> dict[str, Any]:
    conn = _conn()
    try:
        uni = _find_university(conn, university)
        course = _find_receiving_course(conn, uni, prefix, number)
        if isinstance(course, JSONResponse):
            return course
        year_id = _latest_academic_year(conn, course["id"])
        selected_terms = _resolve_requested_terms(conn, term)
        selected_term_ids = [term_id for _, term_id, _ in selected_terms]
        rows = _query_reverse_rows(conn, course["id"], year_id, standalone_only)
        query_term_ids = _resolve_offering_term_ids(conn, selected_term_ids, async_only)
        parsed_rows, companion_ids = _parse_reverse_rows(rows)
        course_map = _load_course_map(conn, companion_ids)
        offering_keys = _collect_offering_keys(rows, parsed_rows, course_map)
        offerings = _query_offerings(conn, offering_keys, query_term_ids)
        results = _build_reverse_results(
            parsed_rows, course_map, offerings, query_term_ids, selected_terms, async_only
        )
        no_art_rows = _query_no_articulation(conn, course["id"], year_id)

        return {
            "query": {
                "university": {"code": uni["code"].strip(), "name": uni["name"]},
                "course": _course_row_to_obj(course),
                "academic_year_id": year_id,
                "term": (
                    {"code": selected_terms[0][0], "label": selected_terms[0][2]}
                    if len(selected_terms) == 1
                    else None
                ),
                "terms": [
                    {"code": code, "label": label} for code, _, label in selected_terms
                ],
                "async_only": async_only,
            },
            "results": results,
            "no_articulation": [
                {
                    "cc_code": row["cc_code"].strip(),
                    "cc_name": row["cc_name"],
                    "reason": row["no_articulation_reason"],
                }
                for row in no_art_rows
            ],
        }
    finally:
        conn.close()


# Mount static frontend last so API routes take precedence
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
