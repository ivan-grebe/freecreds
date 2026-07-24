"""FastAPI app serving reverse-lookup queries + the static frontend."""
from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .schedule_urls import apply_schedule_urls
from .terms import academic_year_label, parse_code, upcoming_terms

DB_PATH = Path(__file__).parent.parent / "data" / "assist.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

MODALITY_RANK = {"online_mixed": 1, "online_sync": 2, "online_async": 3}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _run_startup_tasks() -> None:
    """Run lightweight migrations, populate schedule_url for known CCCs,
    and ensure the upcoming terms exist in the DB.

    The terms upsert matters for the reverse-lookup endpoint: it resolves
    a term code to an ID to join against class_offerings. If a term is
    missing from the table, the endpoint silently drops the term filter
    — which makes the UI's "Offered" column disappear.

    All three steps are idempotent — safe to run on every startup.
    """
    if not DB_PATH.parent.exists():
        return
    conn = _conn()
    try:
        db.init_db(conn)
        apply_schedule_urls(conn)
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


@app.get("/api/reverse")
def reverse_lookup(
    university: str = Query(..., description="Institution code"),
    prefix: str = Query(..., description="Course prefix (e.g. MATH)"),
    number: str = Query(..., description="Course number (e.g. 170B)"),
    standalone_only: bool = Query(False, description="Only return standalone equivalents"),
    term: str | None = Query(None, description="Canonical term code, e.g. FA26"),
    async_only: bool = Query(False, description="Return only rows confirmed as async online"),
) -> dict[str, Any]:
    conn = _conn()
    try:
        uni = conn.execute(
            "SELECT id, code, name FROM institutions WHERE code = ? COLLATE NOCASE",
            (university.strip(),),
        ).fetchone()
        if not uni:
            raise HTTPException(404, f"Unknown university code {university!r}")

        course = conn.execute(
            """SELECT id, prefix, number, title, min_units, max_units
               FROM courses
               WHERE institution_id = ? AND prefix = ? COLLATE NOCASE
                 AND number = ? COLLATE NOCASE""",
            (uni["id"], prefix.strip(), number.strip()),
        ).fetchone()
        if not course:
            # Return candidates on prefix match for a "did you mean?" list
            candidates = conn.execute(
                """SELECT prefix, number, title FROM courses
                   WHERE institution_id = ? AND prefix = ? COLLATE NOCASE
                   ORDER BY number LIMIT 20""",
                (uni["id"], prefix.strip()),
            ).fetchall()
            return JSONResponse(
                status_code=404,
                content={
                    "error": f"No {prefix} {number} at {uni['code'].strip()}",
                    "did_you_mean": [dict(c) for c in candidates],
                },
            )

        year_row = conn.execute(
            """SELECT MAX(academic_year_id) AS year_id FROM articulations
               WHERE receiving_course_id = ?""",
            (course["id"],),
        ).fetchone()
        year_id = year_row["year_id"] if year_row else None

        term_code, term_id, term_label = _resolve_requested_term(conn, term)

        # Articulating CCs. GROUP BY the articulation path so the same path
        # that appears under multiple sources (AllDepartments + AllMajors)
        # collapses to one row with `sources` carrying both labels.
        sql = """
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
        """
        params: list[Any] = [course["id"]]
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

        rows = conn.execute(sql, params).fetchall()

        # Fetch offerings for every (cc, course) pair in one query — avoids N+1.
        # Keyed by (institution_id, prefix, number).
        #   - Specific term selected: look at that term only.
        #   - No term + async_only:   look across all ingested terms so the
        #                             filter means "offered async sometime".
        #   - No term, no filter:     skip the query entirely.
        query_term_ids: list[int] = []
        if term_id is not None:
            query_term_ids = [term_id]
        elif async_only:
            upcoming_codes = [t.code for t in upcoming_terms(date.today(), count=4)]
            code_marks = ",".join(["?"] * len(upcoming_codes))
            query_term_ids = [
                r[0]
                for r in conn.execute(
                    f"SELECT id FROM terms WHERE code IN ({code_marks})",
                    upcoming_codes,
                ).fetchall()
            ]

        results: list[dict[str, Any]] = []
        all_course_ids: set[int] = set()
        parsed_rows: list[tuple[sqlite3.Row, list[int], list[int]]] = []
        for r in rows:
            comps = _parse_id_list(r["companion_course_ids"])
            recv_comps = _parse_id_list(r["receiving_companion_course_ids"])
            all_course_ids.update(comps)
            all_course_ids.update(recv_comps)
            parsed_rows.append((r, comps, recv_comps))

        course_map: dict[int, dict[str, Any]] = {}
        if all_course_ids:
            q_marks = ",".join(["?"] * len(all_course_ids))
            comp_rows = conn.execute(
                f"""SELECT id, prefix, number, title, min_units, max_units
                    FROM courses WHERE id IN ({q_marks})""",
                list(all_course_ids),
            ).fetchall()
            for cr in comp_rows:
                course_map[cr["id"]] = _course_row_to_obj(cr)

        offering_keys = [_offering_key(r) for r in rows]
        for r, comps, _ in parsed_rows:
            offering_keys.extend(
                (r["cc_institution_id"], course_map[cid]["prefix"].upper(),
                 course_map[cid]["number"].upper())
                for cid in comps
                if cid in course_map
            )
        offerings_map = _query_offerings(conn, offering_keys, query_term_ids)

        for r, comps, recv_comps in parsed_rows:
            bundle_keys = [_offering_key(r)] + [
                (r["cc_institution_id"], course_map[cid]["prefix"].upper(),
                 course_map[cid]["number"].upper())
                for cid in comps
                if cid in course_map
            ]
            modality = (
                _matching_bundle_modality(
                    offerings_map, bundle_keys, query_term_ids, async_only
                )
                if query_term_ids and len(bundle_keys) == len(comps) + 1
                else None
            )
            # Per-row status only has meaning when the user picked a specific term.
            # In "Any term" mode we still use modality for filtering, but the UI
            # hides the offered column (nothing to display per-row).
            status = _offering_status(modality) if term_id is not None else "unknown"

            # When a term is selected, drop rows with no offering record —
            # the result set becomes "articulates AND is offered this term"
            # instead of "articulates (with a possibly-unknown status)".
            if term_id is not None and modality is None:
                continue

            if async_only and modality != "online_async":
                continue

            sources = [s for s in (r["sources_csv"] or "").split(",") if s]
            row_year_id = r["academic_year_id"]
            results.append({
                "cc_code": r["cc_code"].strip(),
                "cc_name": r["cc_name"],
                "cc_course": {
                    "prefix": r["cc_prefix"],
                    "number": r["cc_number"],
                    "title": r["cc_title"],
                    "min_units": r["min_units"],
                    "max_units": r["max_units"],
                },
                "is_standalone": bool(r["is_standalone_equivalent"]),
                "companion_courses": [course_map.get(cid, {"id": cid}) for cid in comps],
                "receiving_companion_courses": [
                    course_map.get(cid, {"id": cid}) for cid in recv_comps
                ],
                "offering_status": status,
                "schedule_url": r["cc_schedule_url"],
                "sources": sources,
                "academic_year_id": row_year_id,
                "academic_year": academic_year_label(row_year_id) if row_year_id else None,
            })

        # No-articulation list. A CCC qualifies only if EVERY source we
        # know about for that pair reports "no articulation" — otherwise
        # a concurrent Major-view articulation would be contradicted by
        # listing the CCC here.
        no_art_sql = """
          SELECT DISTINCT cc.code AS cc_code, cc.name AS cc_name,
                 a.no_articulation_reason
          FROM articulations a
          JOIN institutions cc ON cc.id = a.sending_cc_id
          WHERE a.receiving_course_id = ?
            AND a.no_articulation_reason IS NOT NULL
        """
        no_art_params: list[Any] = [course["id"]]
        if year_id is not None:
            no_art_sql += " AND a.academic_year_id = ?"
            no_art_params.append(year_id)
        no_art_sql += """
            AND NOT EXISTS (
              SELECT 1 FROM reverse_index ri
              WHERE ri.receiving_course_id = a.receiving_course_id
                AND ri.sending_cc_id = a.sending_cc_id
                AND ri.academic_year_id = a.academic_year_id
            )
          ORDER BY cc.name
        """
        no_art_rows = conn.execute(no_art_sql, no_art_params).fetchall()

        return {
            "query": {
                "university": {"code": uni["code"].strip(), "name": uni["name"]},
                "course": _course_row_to_obj(course),
                "academic_year_id": year_id,
                "term": {"code": term_code, "label": term_label} if term_id else None,
                "async_only": async_only,
            },
            "results": results,
            "no_articulation": [
                {
                    "cc_code": r["cc_code"].strip(),
                    "cc_name": r["cc_name"],
                    "reason": r["no_articulation_reason"],
                }
                for r in no_art_rows
            ],
        }
    finally:
        conn.close()


# Mount static frontend last so API routes take precedence
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
