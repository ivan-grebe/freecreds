"""SQLite migrations and ingestion queries."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

DEFAULT_DB_PATH = Path("data/assist.db")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
LOCAL_MIGRATIONS_TABLE = "_freecreds_migrations"

# ASSIST integer code → readable label.
CATEGORY_MAP = {0: "CSU", 1: "UC", 2: "CCC", 5: "AICCU"}
TERM_TYPE_MAP = {0: "Semester", 1: "Quarter", 2: "Trimester"}

def connect(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _apply_migration(conn: sqlite3.Connection, migration: Path) -> None:
    migration_name = migration.name.replace("'", "''")
    script = migration.read_text(encoding="utf-8")
    try:
        conn.executescript(
            f"BEGIN;\n{script}\n"
            f"INSERT INTO {LOCAL_MIGRATIONS_TABLE} (name) "
            f"VALUES ('{migration_name}');\nCOMMIT;"
        )
    except sqlite3.Error:
        conn.rollback()
        raise


def init_db(conn: sqlite3.Connection) -> None:
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        raise RuntimeError(f"No database migrations found in {MIGRATIONS_DIR}")
    initialized = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'institutions'"
    ).fetchone()
    migration_tracking = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (LOCAL_MIGRATIONS_TABLE,),
    ).fetchone()
    if initialized and not migration_tracking:
        raise RuntimeError(
            "Local database predates migration tracking. Delete it and regenerate "
            "it with the ingestion command."
        )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {LOCAL_MIGRATIONS_TABLE} "
        "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {
        row[0] for row in conn.execute(f"SELECT name FROM {LOCAL_MIGRATIONS_TABLE}")
    }

    for migration in migration_files:
        if migration.name in applied:
            continue
        _apply_migration(conn, migration)
    conn.commit()


def upsert_institution(
    conn: sqlite3.Connection,
    assist_id: int,
    code: str,
    name: str,
    category_int: int,
    term_type_int: int,
) -> int:
    category = CATEGORY_MAP.get(category_int, "AICCU")
    term_type = TERM_TYPE_MAP.get(term_type_int, "Semester")
    cur = conn.execute(
        """INSERT INTO institutions (assist_id, code, name, category, term_type)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(assist_id) DO UPDATE SET
             code=excluded.code,
             name=excluded.name,
             category=excluded.category,
             term_type=excluded.term_type
           RETURNING id""",
        (assist_id, code.strip(), name, category, term_type),
    )
    row = cur.fetchone()
    return row[0]


def upsert_course(
    conn: sqlite3.Connection,
    institution_id: int,
    course_identifier_parent_id: int,
    prefix: str,
    number: str,
    title: str,
    min_units: float | None,
    max_units: float | None,
    is_terminated: bool = False,
) -> int:
    cur = conn.execute(
        """INSERT INTO courses
             (institution_id, course_identifier_parent_id, prefix, number,
              title, min_units, max_units, is_terminated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(institution_id, course_identifier_parent_id) DO UPDATE SET
             prefix=excluded.prefix,
             number=excluded.number,
             title=excluded.title,
             min_units=excluded.min_units,
             max_units=excluded.max_units,
             is_terminated=excluded.is_terminated
           RETURNING id""",
        (
            institution_id,
            course_identifier_parent_id,
            prefix.strip(),
            number.strip(),
            title,
            min_units,
            max_units,
            1 if is_terminated else 0,
        ),
    )
    return cur.fetchone()[0]


def insert_articulation(
    conn: sqlite3.Connection,
    receiving_course_id: int,
    sending_cc_id: int,
    university_id: int,
    academic_year_id: int,
    no_articulation_reason: str | None,
    source_context: str = "AllDepartments",
) -> int | None:
    """Insert an articulation row. Returns the row id, or None if a
    duplicate already exists (same receiving/sending/year/source_context).
    Duplicates within a single source happen when a course appears in
    multiple departments.
    """
    cur = conn.execute(
        """INSERT OR IGNORE INTO articulations
             (receiving_course_id, sending_cc_id, university_id,
              academic_year_id, source_context, no_articulation_reason)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            receiving_course_id,
            sending_cc_id,
            university_id,
            academic_year_id,
            source_context,
            no_articulation_reason,
        ),
    )
    return cur.lastrowid if cur.rowcount else None


def insert_reverse_index_rows(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[int, int, int, bool, list[int], int, str, list[int]]],
) -> None:
    """rows: iterable of (receiving_course_id, sending_cc_id, sending_course_id,
    is_standalone, companion_ids, academic_year_id, source_context,
    receiving_companion_ids).
    """
    payload = [
        (
            rcid, ccid, scid, 1 if standalone else 0,
            json.dumps(companions), yid, src,
            json.dumps(recv_comps) if recv_comps else None,
        )
        for (rcid, ccid, scid, standalone, companions, yid, src, recv_comps) in rows
    ]
    conn.executemany(
        """INSERT INTO reverse_index
             (receiving_course_id, sending_cc_id, sending_course_id,
              is_standalone_equivalent, companion_course_ids, academic_year_id,
              source_context, receiving_companion_course_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )


def upsert_term(
    conn: sqlite3.Connection,
    code: str,
    label: str,
    season: str,
    year: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO terms (code, label, season, year, start_date, end_date)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET
             label=excluded.label,
             season=excluded.season,
             year=excluded.year,
             start_date=COALESCE(excluded.start_date, terms.start_date),
             end_date=COALESCE(excluded.end_date, terms.end_date)
           RETURNING id""",
        (code, label, season, year, start_date, end_date),
    )
    return cur.fetchone()[0]


def get_term_id_by_code(conn: sqlite3.Connection, code: str) -> int | None:
    row = conn.execute("SELECT id FROM terms WHERE code = ?", (code,)).fetchone()
    return row[0] if row else None


def upsert_class_offering(
    conn: sqlite3.Connection,
    institution_id: int,
    course_id: int | None,
    prefix: str,
    number: str,
    term_id: int,
    modality: str,
    source: str,
    source_ref: str | None,
    fetched_at: str,
) -> None:
    upsert_class_offerings(
        conn,
        [
            (
                institution_id,
                course_id,
                prefix,
                number,
                term_id,
                modality,
                source,
                source_ref,
                fetched_at,
            )
        ],
    )


def upsert_class_offerings(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[int, int | None, str, str, int, str, str, str | None, str]],
) -> None:
    payload = [
        (
            institution_id,
            course_id,
            prefix.strip().upper(),
            number.strip().upper(),
            term_id,
            modality,
            source,
            source_ref,
            fetched_at,
        )
        for (
            institution_id,
            course_id,
            prefix,
            number,
            term_id,
            modality,
            source,
            source_ref,
            fetched_at,
        ) in rows
    ]
    conn.executemany(
        """INSERT INTO class_offerings
             (institution_id, course_id, prefix, number, term_id,
              modality, source, source_ref, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(institution_id, prefix, number, term_id, source_ref) DO UPDATE SET
             course_id=excluded.course_id,
             modality=excluded.modality,
             fetched_at=excluded.fetched_at""",
        payload,
    )


def clear_offerings(
    conn: sqlite3.Connection, source: str, term_ids: Iterable[int]
) -> None:
    """Delete offerings from a given source for the given terms. Used to
    make ingestion idempotent.
    """
    term_id_list = list(term_ids)
    if not term_id_list:
        return
    q_marks = ",".join(["?"] * len(term_id_list))
    conn.execute(
        f"DELETE FROM class_offerings WHERE source = ? AND term_id IN ({q_marks})",
        [source, *term_id_list],
    )


def clear_articulation_data(
    conn: sqlite3.Connection, university_id: int, academic_year_id: int
) -> None:
    """Remove all articulation data for a university+year. Used to make
    ingestion idempotent.
    """
    conn.execute(
        """DELETE FROM reverse_index
           WHERE academic_year_id = ? AND receiving_course_id IN (
             SELECT id FROM courses WHERE institution_id = ?
           )""",
        (academic_year_id, university_id),
    )
    conn.execute(
        "DELETE FROM articulations WHERE university_id = ? AND academic_year_id = ?",
        (university_id, academic_year_id),
    )
