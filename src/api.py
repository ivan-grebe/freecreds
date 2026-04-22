"""FastAPI app serving reverse-lookup queries + the static frontend."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db

DB_PATH = Path(__file__).parent.parent / "data" / "assist.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Reverse ASSIST Search")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


@app.get("/api/universities")
def list_universities() -> Dict[str, Any]:
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT code, name, category FROM institutions
               WHERE category IN ('CSU','UC')
                 AND id IN (SELECT DISTINCT university_id FROM articulations)
               ORDER BY name"""
        ).fetchall()
        return {"universities": [dict(r) for r in rows]}
    finally:
        conn.close()


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


@app.get("/api/reverse")
def reverse_lookup(
    university: str = Query(..., description="Institution code"),
    prefix: str = Query(..., description="Course prefix (e.g. MATH)"),
    number: str = Query(..., description="Course number (e.g. 170B)"),
    standalone_only: bool = Query(False, description="Only return standalone equivalents"),
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

        # Articulating CCs
        sql = """
          SELECT cc.code AS cc_code, cc.name AS cc_name,
                 c_cc.id AS cc_course_id,
                 c_cc.prefix AS cc_prefix, c_cc.number AS cc_number,
                 c_cc.title AS cc_title,
                 c_cc.min_units, c_cc.max_units,
                 ri.is_standalone_equivalent, ri.companion_course_ids
          FROM reverse_index ri
          JOIN institutions cc ON cc.id = ri.sending_cc_id
          JOIN courses c_cc ON c_cc.id = ri.sending_course_id
          WHERE ri.receiving_course_id = ?
        """
        params: List[Any] = [course["id"]]
        if standalone_only:
            sql += " AND ri.is_standalone_equivalent = 1"
        sql += " ORDER BY cc.name, c_cc.prefix, c_cc.number"

        rows = conn.execute(sql, params).fetchall()
        results = []
        # Collect companion ids so we can hydrate them in one query
        companion_ids: set = set()
        parsed_rows = []
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
            })

        # No-articulation list
        no_art_rows = conn.execute(
            """SELECT cc.code AS cc_code, cc.name AS cc_name, a.no_articulation_reason
               FROM articulations a
               JOIN institutions cc ON cc.id = a.sending_cc_id
               WHERE a.receiving_course_id = ? AND a.no_articulation_reason IS NOT NULL
               ORDER BY cc.name""",
            (course["id"],),
        ).fetchall()

        return {
            "query": {
                "university": {"code": uni["code"].strip(), "name": uni["name"]},
                "course": _course_row_to_obj(course),
                "academic_year_id": year_id,
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
