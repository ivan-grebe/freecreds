"""Orchestrate fetch → parse → store.

Single target:
    python -m src.ingester --university CSUFULL

Every CSU + UC + AICCU target in one go (slow — run in a fresh shell):
    python -m src.ingester --all
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from . import db
from .assist_api import (
    AssistClient,
    find_institution_by_code,
    institution_display_name,
    latest_academic_year_id,
)
from .parser import build_reverse_rows, iter_parsed_articulations

log = logging.getLogger(__name__)

COUNT_KEYS = (
    "courses",
    "cross_listings",
    "articulations",
    "with_sending",
    "no_art",
    "course_groups",
    "group_members",
    "reverse_rows",
)


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in COUNT_KEYS}


def _add_counts(total: dict[str, int], counts: dict[str, int]) -> None:
    for key in COUNT_KEYS:
        total[key] += counts.get(key, 0)


def _elapsed(started_at: float) -> str:
    seconds = int(time.monotonic() - started_at)
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes:d}m {seconds:02d}s"


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={counts[key]}" for key in COUNT_KEYS if counts.get(key))


def _institution_name(inst: dict[str, Any], year_hint: int | None = None) -> str:
    return institution_display_name(inst, year=year_hint)


def _find_all_summary_agreements(
    client: AssistClient, receiving_id: int, sending_id: int, year_id: int
) -> list[tuple[str, str]]:
    """Locate every summary agreement key that covers articulations for a
    (target, CCC, year) triple. Returns a list of (key, schema_name) pairs
    where schema_name is "AllDepartments" or "AllMajors". An empty list
    means no summaries exist for this pair.

    CSU/UC targets usually publish both: "AllDepartments" covers the
    cross-department view, while "AllMajors" covers per-major pathways
    that can differ (a major may accept a single course where the
    department summary requires a bundle, or vice versa). AICCU targets
    typically publish only "AllMajors".

    We read both and the downstream ingest stores them side-by-side with
    their source tag, so the reverse-lookup layer can show each distinct
    articulation path and which view(s) produced it.
    """
    summaries: list[tuple[str, str]] = []

    resp = client.list_agreement_keys(receiving_id, sending_id, year_id, types="Department")
    reports = resp.get("allReports") or resp.get("reports") or []
    dep = next((r for r in reports if r.get("type") == "AllDepartments"), None)
    if dep:
        summaries.append((dep["key"], "AllDepartments"))

    resp = client.list_agreement_keys(receiving_id, sending_id, year_id, types="Major")
    reports = resp.get("allReports") or resp.get("reports") or []
    maj = next((r for r in reports if r.get("type") == "AllMajors"), None)
    if maj:
        summaries.append((maj["key"], "AllMajors"))

    return summaries


def _build_cell_to_major_map(template_assets: Any) -> dict[str, str]:
    """Walk the AllMajors `templateAssets` tree to build a mapping from
    articulation `templateCellId` → specific major name.

    templateAssets is a list of `{name, templateAssets: [...]}` — one entry
    per major. Each major's nested assets eventually contain course cells
    with an `id` (the templateCellId referenced by per-articulation entries).
    We collect every such id and tag it with its containing major.
    """
    result: dict[str, str] = {}
    if isinstance(template_assets, str):
        try:
            template_assets = json.loads(template_assets)
        except ValueError:
            return result
    if not isinstance(template_assets, list):
        return result

    def gather_course_cell_ids(node: Any, into: set[str]) -> None:
        if isinstance(node, dict):
            # A course cell has both an `id` (string) and a `course` sibling.
            if isinstance(node.get("id"), str) and "course" in node:
                into.add(node["id"])
            for v in node.values():
                gather_course_cell_ids(v, into)
        elif isinstance(node, list):
            for v in node:
                gather_course_cell_ids(v, into)

    for major in template_assets:
        if not isinstance(major, dict):
            continue
        name = (major.get("name") or "").strip()
        if not name:
            continue
        ids: set[str] = set()
        gather_course_cell_ids(major.get("templateAssets") or [], ids)
        for cid in ids:
            # If a cell appears in multiple majors, the first wins. Rare in
            # practice; the pay-per-major entries are usually distinct.
            result.setdefault(cid, name)
    return result


def _normalize_majors_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Reshape an AllMajors payload into the AllDepartments shape the parser
    expects, *and* tag each articulation with its specific major.

    AllDepartments body (stringified): `[{name, articulations: [entry, ...]}]`
    AllMajors body     (stringified):  `[{templateCellId, articulation: entry, ...}]`

    We unwrap each `articulation` field, inject `_source_major` on the inner
    dict using the `templateCellId` → major-name map derived from
    `templateAssets`, and wrap the whole flat list in one synthetic container.
    Articulations whose cell isn't found in any major's asset tree are left
    with `_source_major = None` (they fall back to the generic "AllMajors"
    tag at ingest time).
    """
    raw = payload.get("articulations")
    if not isinstance(raw, str):
        return payload
    try:
        items = json.loads(raw)
    except ValueError:
        return payload

    cell_to_major = _build_cell_to_major_map(payload.get("templateAssets"))

    flat: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        inner = it.get("articulation")
        if not isinstance(inner, dict):
            continue
        cid = it.get("templateCellId")
        major = cell_to_major.get(cid) if isinstance(cid, str) else None
        if major:
            inner["_source_major"] = major
        flat.append(inner)

    synthetic = [{"name": "All Majors", "articulations": flat}]
    new = dict(payload)
    new["articulations"] = json.dumps(synthetic)
    return new


def _upsert_all_institutions(
    conn: sqlite3.Connection, institutions: list[dict[str, Any]]
) -> dict[int, int]:
    """Upsert every institution. Returns a map from ASSIST id → DB id."""
    mapping: dict[int, int] = {}
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
    agreement_payload: dict[str, Any],
    university_db_id: int,
    cc_db_id: int,
    academic_year_id: int,
    source_context: str = "AllDepartments",
) -> dict[str, int]:
    """Persist a single summary agreement payload (either AllDepartments or
    an AllMajors-normalized-to-department shape). Returns counts.
    """
    counts = _empty_counts()

    # Build course ID cache so we insert each course once per run.
    course_cache: dict[tuple[int, int], int] = {}

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
        counts["courses"] += 1
        return cid

    for parsed in iter_parsed_articulations(agreement_payload):
        recv_db_id = ensure_course(parsed.receiving_course, university_db_id)
        # Per-row source context: use the specific major name when we know
        # it, else fall back to the payload-level tag (e.g. "AllMajors" or
        # "AllDepartments").
        row_source = (
            f"Major: {parsed.source_major}" if parsed.source_major else source_context
        )

        # Cross-listed receiving aliases — index them at the university too
        for xl in parsed.cross_listed_receiving:
            alias_db_id = ensure_course(xl, university_db_id)
            if db.insert_cross_listing(conn, recv_db_id, alias_db_id):
                counts["cross_listings"] += 1

        # Ensure all sending courses exist first
        sending_db_ids: dict[int, int] = {}  # parent_id → db id
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
            source_context=row_source,
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
            counts["course_groups"] += 1
            for ci, c in enumerate(grp.courses):
                db.insert_group_member(
                    conn, group_row_id, sending_db_ids[c.course_identifier_parent_id], ci
                )
                counts["group_members"] += 1

        # Reverse-index rows — for each sending course, including cross-listed receiving
        receiving_targets = [recv_db_id] + [
            course_cache[(university_db_id, xl.course_identifier_parent_id)]
            for xl in parsed.cross_listed_receiving
        ]
        # Series fan-out leaves us with the *other* receiving members of the
        # same series. Taking any sending course also yields credit for them.
        receiving_sibling_ids = [
            ensure_course(sib, university_db_id) for sib in parsed.receiving_siblings
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
                        row_source,
                        receiving_sibling_ids,
                    )
                )
        if row_tuples:
            db.insert_reverse_index_rows(conn, row_tuples)
            counts["reverse_rows"] += len(row_tuples)

    return counts


def ingest_university(
    university_code: str,
    db_path: Path = db.DEFAULT_DB_PATH,
    limit_ccs: int | None = None,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        with AssistClient() as client:
            institutions = client.get_institutions()
            log.info("Fetched %d institutions", len(institutions))
            id_map = _upsert_all_institutions(conn, institutions)
            _ingest_university_from_context(
                conn,
                client,
                institutions,
                id_map,
                university_code,
                limit_ccs=limit_ccs,
            )
    finally:
        conn.close()


def _ingest_university_from_context(
    conn: sqlite3.Connection,
    client: AssistClient,
    institutions: list[dict[str, Any]],
    id_map: dict[int, int],
    university_code: str,
    limit_ccs: int | None = None,
) -> dict[str, int]:
    target_started_at = time.monotonic()
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
    log.info("Clearing prior articulation data for %s/%d", university_code, year_id)
    db.clear_articulation_data(conn, uni_db_id, year_id)
    conn.commit()
    log.info("Clear complete for %s/%d", university_code, year_id)

    totals = _empty_counts()
    for i, entry in enumerate(active_ccs, start=1):
        cc_started_at = time.monotonic()
        cc = entry["receivingInstitution"]
        cc_assist_id = cc["id"]
        cc_db_id = id_map[cc_assist_id]
        cc_code = (cc.get("code") or "?").strip()

        summaries = _find_all_summary_agreements(
            client, uni_assist_id, cc_assist_id, year_id
        )
        if not summaries:
            log.warning(
                "[%d/%d] %s: no AllDepartments or AllMajors key",
                i,
                len(active_ccs),
                cc_code,
            )
            continue

        per_cc = _empty_counts()
        sources_used: list[str] = []
        for summary_key, schema in summaries:
            summary_started_at = time.monotonic()
            log.info(
                "[%d/%d] %s [%s]: fetching agreement key",
                i,
                len(active_ccs),
                cc_code,
                schema,
            )
            try:
                payload = client.get_agreement(summary_key)
            except Exception as e:
                log.warning(
                    "[%d/%d] %s [%s]: fetch failed: %s",
                    i,
                    len(active_ccs),
                    cc_code,
                    schema,
                    e,
                )
                continue

            if schema == "AllMajors":
                payload = _normalize_majors_payload(payload)

            counts = _ingest_one_agreement(
                conn,
                payload,
                uni_db_id,
                cc_db_id,
                year_id,
                source_context=schema,
            )
            _add_counts(per_cc, counts)
            _add_counts(totals, counts)
            sources_used.append(schema)
            log.info(
                "[%d/%d] %s [%s]: parsed/wrote in %s (%s)",
                i,
                len(active_ccs),
                cc_code,
                schema,
                _elapsed(summary_started_at),
                _format_counts(counts) or "no rows",
            )
        conn.commit()
        log.info(
            (
                "[%d/%d] %s [%s]: committed in %s; "
                "%d articulations (%d with sending, %d no-art), %d reverse rows; "
                "target elapsed %s"
            ),
            i,
            len(active_ccs),
            cc_code,
            "+".join(sources_used) or "none",
            _elapsed(cc_started_at),
            per_cc["articulations"],
            per_cc["with_sending"],
            per_cc["no_art"],
            per_cc["reverse_rows"],
            _elapsed(target_started_at),
        )

    log.info(
        "Done %s in %s. Totals: %s",
        university_code,
        _elapsed(target_started_at),
        totals,
    )
    return totals


DEFAULT_TARGET_CATEGORIES = ("CSU", "UC", "AICCU")


def ingest_all_targets(
    db_path: Path = db.DEFAULT_DB_PATH,
    categories: tuple = DEFAULT_TARGET_CATEGORIES,
) -> dict[str, list[str]]:
    """Run ingest_university for every target in the requested categories.

    Resilient to per-target errors: a target that fails (e.g. no published
    agreements, transient network error) is logged and skipped; the loop
    keeps going. Returns {"succeeded": [...], "failed": [...]}.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log.info("Fetching institution list from ASSIST...")
    run_started_at = time.monotonic()
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        with AssistClient() as client:
            institutions = client.get_institutions()
            id_map = _upsert_all_institutions(conn, institutions)

            wanted = {c.strip().upper() for c in categories}
            targets: list[tuple[str, str]] = []
            for inst in institutions:
                cat = inst.get("category")
                cat_name = db.CATEGORY_MAP.get(cat) if isinstance(cat, int) else None
                if cat_name not in wanted:
                    continue
                code = (inst.get("code") or "").strip()
                if code:
                    targets.append((cat_name, code))
            # Stable, predictable order: category then code.
            targets.sort()

            by_cat = {c: sum(1 for cc, _ in targets if cc == c) for c in sorted(wanted)}
            log.info(
                "Targets: %d total (%s)",
                len(targets),
                ", ".join(f"{c}={n}" for c, n in by_cat.items()),
            )

            succeeded: list[str] = []
            failed: list[str] = []
            for i, (cat, code) in enumerate(targets, start=1):
                target_started_at = time.monotonic()
                log.info("=" * 60)
                log.info(
                    "[%d/%d] %s (%s) starting; run elapsed %s",
                    i,
                    len(targets),
                    code,
                    cat,
                    _elapsed(run_started_at),
                )
                try:
                    _ingest_university_from_context(conn, client, institutions, id_map, code)
                except Exception as e:
                    conn.rollback()
                    log.warning("[%d/%d] FAILED %s: %s", i, len(targets), code, e)
                    failed.append(code)
                else:
                    succeeded.append(code)
                    log.info(
                        "[%d/%d] %s completed in %s; run elapsed %s",
                        i,
                        len(targets),
                        code,
                        _elapsed(target_started_at),
                        _elapsed(run_started_at),
                    )

            log.info("=" * 60)
            log.info(
                "Done in %s. %d succeeded, %d failed.",
                _elapsed(run_started_at),
                len(succeeded),
                len(failed),
            )
            if failed:
                log.info("Failed codes: %s", ", ".join(failed))
            return {"succeeded": succeeded, "failed": failed}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--university",
        default="CSUFULL",
        help="Target university ASSIST code",
    )
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="SQLite database path")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of CCs to process (for testing)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Ingest every CSU/UC/AICCU target sequentially (ignores --university and --limit)",
    )
    parser.add_argument(
        "--categories",
        default=",".join(DEFAULT_TARGET_CATEGORIES),
        help="Comma-separated category filter for --all (default: CSU,UC,AICCU)",
    )
    args = parser.parse_args(argv)
    if args.all:
        cats = tuple(c.strip().upper() for c in args.categories.split(",") if c.strip())
        ingest_all_targets(Path(args.db), categories=cats)
    else:
        ingest_university(args.university, Path(args.db), limit_ccs=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
