"""Orchestrate fetch → parse → store.

Run: python -m src.ingester --university CSUF
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db
from .assist_api import (
    AssistClient,
    find_institution_by_code,
    institution_display_name,
    latest_academic_year_id,
)
from .parser import ParsedArticulation, build_reverse_rows, iter_parsed_articulations

log = logging.getLogger(__name__)


def _institution_name(inst: Dict[str, Any], year_hint: Optional[int] = None) -> str:
    return institution_display_name(inst, year=year_hint)


def _upsert_all_institutions(
    conn: sqlite3.Connection, institutions: List[Dict[str, Any]]
) -> Dict[int, int]:
    """Upsert every institution. Returns a map from ASSIST id → DB id."""
    mapping: Dict[int, int] = {}
    for inst in institutions:
        cat = inst.get("category")
        if not isinstance(cat, int):
            continue
        term = inst.get("termType") if isinstance(inst.get("termType"), int) else 0
        db_id = db.upsert_institution(
            conn,
            assist_id=inst["id"],
            code=(inst.get("code") or "").strip(),
            name=_institution_name(inst),
            category_int=cat,
            term_type_int=term,
        )
        mapping[inst["id"]] = db_id
    conn.commit()
    return mapping


def _ingest_one_agreement(
    conn: sqlite3.Connection,
    agreement_payload: Dict[str, Any],
    university_db_id: int,
    cc_db_id: int,
    academic_year_id: int,
) -> Dict[str, int]:
    """Persist a single AllDepartments agreement payload. Returns counts."""
    counts = {"articulations": 0, "with_sending": 0, "no_art": 0, "reverse_rows": 0}

    # Build course ID cache so we insert each course once per run.
    course_cache: Dict[tuple, int] = {}

    def ensure_course(parsed_course, institution_db_id: int) -> int:
        key = (institution_db_id, parsed_course.course_identifier_parent_id)
        if key in course_cache:
            return course_cache[key]
        cid = db.upsert_course(
            conn,
            institution_id=institution_db_id,
            course_identifier_parent_id=parsed_course.course_identifier_parent_id,
            prefix=parsed_course.prefix,
            number=parsed_course.number,
            title=parsed_course.title,
            min_units=parsed_course.min_units,
            max_units=parsed_course.max_units,
            is_terminated=parsed_course.is_terminated,
        )
        course_cache[key] = cid
        return cid

    for parsed in iter_parsed_articulations(agreement_payload):
        recv_db_id = ensure_course(parsed.receiving_course, university_db_id)

        # Cross-listed receiving aliases — index them at the university too
        for xl in parsed.cross_listed_receiving:
            alias_db_id = ensure_course(xl, university_db_id)
            db.insert_cross_listing(conn, recv_db_id, alias_db_id)

        # Ensure all sending courses exist first
        sending_db_ids: Dict[int, int] = {}  # parent_id → db id
        for grp in parsed.sending_groups:
            for c in grp.courses:
                sending_db_ids[c.course_identifier_parent_id] = ensure_course(c, cc_db_id)

        art_id = db.insert_articulation(
            conn,
            receiving_course_id=recv_db_id,
            sending_cc_id=cc_db_id,
            university_id=university_db_id,
            academic_year_id=academic_year_id,
            no_articulation_reason=parsed.no_articulation_reason,
            raw_json=json.dumps(parsed.raw),
        )
        counts["articulations"] += 1
        if parsed.no_articulation_reason:
            counts["no_art"] += 1
            continue
        if not parsed.sending_groups:
            continue
        counts["with_sending"] += 1

        if art_id is None:
            # Duplicate articulation (course in multiple departments) — skip
            # the group+reverse-index inserts; they already exist.
            continue

        # Persist AND/OR tree
        for gi, grp in enumerate(parsed.sending_groups):
            group_row_id = db.insert_course_group(conn, art_id, grp.conjunction, gi)
            for ci, c in enumerate(grp.courses):
                db.insert_group_member(
                    conn, group_row_id, sending_db_ids[c.course_identifier_parent_id], ci
                )

        # Reverse-index rows — for each sending course, including cross-listed receiving
        receiving_targets = [recv_db_id] + [
            course_cache[(university_db_id, xl.course_identifier_parent_id)]
            for xl in parsed.cross_listed_receiving
        ]
        reverse_rows = build_reverse_rows(parsed)
        row_tuples = []
        for target_recv_id in receiving_targets:
            for r in reverse_rows:
                sending_db_id = sending_db_ids[r.sending_course_parent_id]
                companions_db_ids = [sending_db_ids[p] for p in r.companion_parent_ids]
                row_tuples.append(
                    (
                        target_recv_id,
                        cc_db_id,
                        sending_db_id,
                        r.is_standalone,
                        companions_db_ids,
                        academic_year_id,
                    )
                )
        if row_tuples:
            db.insert_reverse_index_rows(conn, row_tuples)
            counts["reverse_rows"] += len(row_tuples)

    return counts


def ingest_university(
    university_code: str,
    db_path: Path = db.DEFAULT_DB_PATH,
    limit_ccs: Optional[int] = None,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conn = db.connect(db_path)
    db.init_db(conn)

    with AssistClient() as client:
        institutions = client.get_institutions()
        log.info("Fetched %d institutions", len(institutions))
        id_map = _upsert_all_institutions(conn, institutions)

        university = find_institution_by_code(institutions, university_code)
        uni_assist_id = university["id"]
        uni_db_id = id_map[uni_assist_id]
        log.info("Target: %s (assist_id=%d)", _institution_name(university), uni_assist_id)

        year_id = latest_academic_year_id(client, uni_assist_id)
        log.info("Academic year ID: %d", year_id)

        agreements = client.get_agreements_from(uni_assist_id)
        active_ccs = [
            e for e in agreements
            if year_id in (e.get("sendingYearIds") or [])
            and (e.get("receivingInstitution") or {}).get("isCommunityCollege")
        ]
        if limit_ccs is not None:
            active_ccs = active_ccs[:limit_ccs]
        log.info("CCCs to process: %d", len(active_ccs))

        # Idempotency: clear prior data for this (university, year)
        db.clear_articulation_data(conn, uni_db_id, year_id)
        conn.commit()

        totals = {"articulations": 0, "with_sending": 0, "no_art": 0, "reverse_rows": 0}
        for i, entry in enumerate(active_ccs, start=1):
            cc = entry["receivingInstitution"]
            cc_assist_id = cc["id"]
            cc_db_id = id_map[cc_assist_id]
            cc_code = (cc.get("code") or "?").strip()

            keys_resp = client.list_agreement_keys(uni_assist_id, cc_assist_id, year_id)
            reports = keys_resp.get("allReports") or []
            all_dep = next((r for r in reports if r.get("type") == "AllDepartments"), None)
            if not all_dep:
                log.warning("[%d/%d] %s: no AllDepartments key", i, len(active_ccs), cc_code)
                continue

            try:
                payload = client.get_agreement(all_dep["key"])
            except Exception as e:
                log.warning("[%d/%d] %s: fetch failed: %s", i, len(active_ccs), cc_code, e)
                continue

            counts = _ingest_one_agreement(
                conn, payload, uni_db_id, cc_db_id, year_id
            )
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
            conn.commit()
            log.info(
                "[%d/%d] %s: %d articulations (%d with sending, %d no-art), %d reverse rows",
                i, len(active_ccs), cc_code,
                counts["articulations"], counts["with_sending"], counts["no_art"],
                counts["reverse_rows"],
            )

        log.info("Done. Totals: %s", totals)

    conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--university", default="CSUF", help="Target university code (alias or ASSIST code)")
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="SQLite database path")
    parser.add_argument("--limit", type=int, default=None, help="Max number of CCs to process (for testing)")
    args = parser.parse_args(argv)
    ingest_university(args.university, Path(args.db), limit_ccs=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
