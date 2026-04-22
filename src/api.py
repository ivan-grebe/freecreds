"""FastAPI app serving reverse-lookup queries + the static frontend."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .schedule_urls import apply_schedule_urls
from .terms import academic_year_label, parse_code, upcoming_terms

DB_PATH = Path(__file__).parent.parent / "data" / "assist.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Reverse ASSIST Search")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


@app.on_event("startup")
def _on_startup() -> None:
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


@app.get("/api/universities")
def list_universities() -> Dict[str, Any]:
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT code, name, category FROM institutions
               WHERE category IN ('CSU','UC','AICCU')
                 AND id IN (SELECT DISTINCT university_id FROM articulations)
               ORDER BY category, name"""
        ).fetchall()
        return {"universities": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/terms")
def list_terms() -> Dict[str, Any]:
    """Four upcoming terms, starting from today. Clients populate the
    term-filter dropdown from this. Four covers the full CCC calendar
    (Spring / Summer / Fall / Winter intersession) from any start date.
    """
    terms = [t.to_dict() for t in upcoming_terms(date.today(), count=4)]
    return {"terms": terms}


@app.get("/api/courses")
def list_courses(
    university: str = Query(..., description="Institution code, e.g. CSUFULL"),
) -> Dict[str, Any]:
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
                 AND c.id IN (SELECT DISTINCT receiving_course_id FROM articulations
                              WHERE university_id = ?)
               ORDER BY c.prefix, c.number""",
            (uni["id"], uni["id"]),
        ).fetchall()
        return {
            "university": {"code": uni["code"].strip(), "name": uni["name"]},
            "courses": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _course_row_to_obj(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "prefix": row["prefix"],
        "number": row["number"],
        "title": row["title"],
        "min_units": row["min_units"],
        "max_units": row["max_units"],
    }


def _offering_status(modality: Optional[str]) -> str:
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


@app.get("/api/reverse")
def reverse_lookup(
    university: str = Query(..., description="Institution code"),
    prefix: str = Query(..., description="Course prefix (e.g. MATH)"),
    number: str = Query(..., description="Course number (e.g. 170B)"),
    standalone_only: bool = Query(False, description="Only return standalone equivalents"),
    term: Optional[str] = Query(None, description="Canonical term code, e.g. FA26"),
    async_only: bool = Query(False, description="Return only rows confirmed as async online"),
) -> Dict[str, Any]:
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

        # Resolve the requested term (if any) to an ID for the offerings join.
        # If the terms table doesn't yet have this code — typical when the
        # server was started before the upcoming-terms seed ran, or when a
        # client asks for a term outside the rolling 4-term window — parse
        # the code ourselves and upsert it so the rest of the path works.
        term_id: Optional[int] = None
        term_label: Optional[str] = None
        if term:
            code_norm = term.strip().upper()
            term_row = conn.execute(
                "SELECT id, label FROM terms WHERE code = ?", (code_norm,)
            ).fetchone()
            if term_row:
                term_id = term_row["id"]
                term_label = term_row["label"]
            else:
                try:
                    parsed = parse_code(code_norm)
                except ValueError:
                    parsed = None
                if parsed is not None:
                    term_id = db.upsert_term(
                        conn,
                        code=parsed.code,
                        label=parsed.label,
                        season=parsed.season,
                        year=parsed.year,
                    )
                    term_label = parsed.label
                    conn.commit()

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
                 ri.sending_cc_id AS cc_institution_id,
                 GROUP_CONCAT(DISTINCT ri.source_context) AS sources_csv,
                 MAX(ri.academic_year_id) AS academic_year_id
          FROM reverse_index ri
          JOIN institutions cc ON cc.id = ri.sending_cc_id
          JOIN courses c_cc ON c_cc.id = ri.sending_course_id
          WHERE ri.receiving_course_id = ?
        """
        params: List[Any] = [course["id"]]
        if standalone_only:
            sql += " AND ri.is_standalone_equivalent = 1"
        sql += (
            " GROUP BY cc.id, c_cc.id, ri.is_standalone_equivalent, ri.companion_course_ids"
            " ORDER BY cc.name, c_cc.prefix, c_cc.number"
        )

        rows = conn.execute(sql, params).fetchall()

        # Fetch offerings for every (cc, course) pair in one query — avoids N+1.
        # Keyed by (institution_id, prefix, number).
        #   - Specific term selected: look at that term only.
        #   - No term + async_only:   look across all ingested terms so the
        #                             filter means "offered async sometime".
        #   - No term, no filter:     skip the query entirely.
        query_term_ids: List[int] = []
        if term_id is not None:
            query_term_ids = [term_id]
        elif async_only:
            query_term_ids = [
                r[0] for r in conn.execute("SELECT id FROM terms").fetchall()
            ]

        offerings_map: Dict[tuple, str] = {}
        if query_term_ids and rows:
            inst_ids = {r["cc_institution_id"] for r in rows}
            t_marks = ",".join(["?"] * len(query_term_ids))
            i_marks = ",".join(["?"] * len(inst_ids))
            off_rows = conn.execute(
                f"""SELECT institution_id, UPPER(prefix) AS prefix,
                           UPPER(number) AS number, modality
                    FROM class_offerings
                    WHERE term_id IN ({t_marks}) AND institution_id IN ({i_marks})""",
                [*query_term_ids, *inst_ids],
            ).fetchall()
            for o in off_rows:
                key = (o["institution_id"], o["prefix"], o["number"])
                # Preference: async > sync > mixed. Upgrade if a better modality is found.
                existing = offerings_map.get(key)
                if existing is None or (
                    o["modality"] == "online_async" and existing != "online_async"
                ):
                    offerings_map[key] = o["modality"]

        results: List[Dict[str, Any]] = []
        companion_ids: set = set()
        parsed_rows: List[Any] = []
        for r in rows:
            comps = json.loads(r["companion_course_ids"]) if r["companion_course_ids"] else []
            companion_ids.update(comps)
            parsed_rows.append((r, comps))

        companion_map: Dict[int, Dict[str, Any]] = {}
        if companion_ids:
            q_marks = ",".join(["?"] * len(companion_ids))
            comp_rows = conn.execute(
                f"""SELECT id, prefix, number, title, min_units, max_units
                    FROM courses WHERE id IN ({q_marks})""",
                list(companion_ids),
            ).fetchall()
            for cr in comp_rows:
                companion_map[cr["id"]] = _course_row_to_obj(cr)

        for r, comps in parsed_rows:
            key = (r["cc_institution_id"], r["cc_prefix"].upper(), r["cc_number"].upper())
            modality = offerings_map.get(key) if query_term_ids else None
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

            sources = (
                [s for s in (r["sources_csv"] or "").split(",") if s]
                if "sources_csv" in r.keys() else []
            )
            row_year_id = r["academic_year_id"] if "academic_year_id" in r.keys() else None
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
                "companion_courses": [companion_map.get(cid, {"id": cid}) for cid in comps],
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
        no_art_rows = conn.execute(
            """SELECT DISTINCT cc.code AS cc_code, cc.name AS cc_name,
                      a.no_articulation_reason
               FROM articulations a
               JOIN institutions cc ON cc.id = a.sending_cc_id
               WHERE a.receiving_course_id = ?
                 AND a.no_articulation_reason IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM reverse_index ri
                   WHERE ri.receiving_course_id = a.receiving_course_id
                     AND ri.sending_cc_id = a.sending_cc_id
                 )
               ORDER BY cc.name""",
            (course["id"],),
        ).fetchall()

        return {
            "query": {
                "university": {"code": uni["code"].strip(), "name": uni["name"]},
                "course": _course_row_to_obj(course),
                "academic_year_id": year_id,
                "term": {"code": term.strip().upper(), "label": term_label} if term_id else None,
                "async_only": async_only,
            },
            "results": results,
            "no_articulation": [
                {"cc_code": r["cc_code"].strip(), "cc_name": r["cc_name"], "reason": r["no_articulation_reason"]}
                for r in no_art_rows
            ],
        }
    finally:
        conn.close()


# Mount static frontend last so API routes take precedence
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
