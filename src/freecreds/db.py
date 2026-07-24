"""SQLite schema and helpers.

One connection per caller. No ORM for MVP — plain sqlite3.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

DEFAULT_DB_PATH = Path("data/assist.db")

# ASSIST integer code → readable label.
CATEGORY_MAP = {0: "CSU", 1: "UC", 2: "CCC", 5: "AICCU"}
TERM_TYPE_MAP = {0: "Semester", 1: "Quarter", 2: "Trimester"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS institutions (
  id INTEGER PRIMARY KEY,
  assist_id INTEGER UNIQUE NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT NOT NULL CHECK(category IN ('CCC','CSU','UC','AICCU')),
  term_type TEXT NOT NULL,
  schedule_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_inst_code ON institutions(code);
CREATE INDEX IF NOT EXISTS idx_inst_code_nocase
  ON institutions(code COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_inst_category ON institutions(category);

CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY,
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  course_identifier_parent_id INTEGER NOT NULL,
  prefix TEXT NOT NULL,
  number TEXT NOT NULL,
  title TEXT NOT NULL,
  min_units REAL,
  max_units REAL,
  is_terminated BOOLEAN NOT NULL DEFAULT 0,
  UNIQUE(institution_id, course_identifier_parent_id)
);
CREATE INDEX IF NOT EXISTS idx_courses_lookup ON courses(institution_id, prefix, number);
CREATE INDEX IF NOT EXISTS idx_courses_lookup_nocase
  ON courses(institution_id, prefix COLLATE NOCASE, number COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS articulations (
  id INTEGER PRIMARY KEY,
  receiving_course_id INTEGER NOT NULL REFERENCES courses(id),
  sending_cc_id INTEGER NOT NULL REFERENCES institutions(id),
  university_id INTEGER NOT NULL REFERENCES institutions(id),
  academic_year_id INTEGER NOT NULL,
  source_context TEXT NOT NULL DEFAULT 'AllDepartments',
  no_articulation_reason TEXT,
  UNIQUE(receiving_course_id, sending_cc_id, academic_year_id, source_context)
);
CREATE INDEX IF NOT EXISTS idx_art_lookup ON articulations(
  university_id, academic_year_id, receiving_course_id
);
CREATE INDEX IF NOT EXISTS idx_art_receiving_year_cc ON articulations(
  receiving_course_id, academic_year_id, sending_cc_id
);

CREATE TABLE IF NOT EXISTS articulation_course_groups (
  id INTEGER PRIMARY KEY,
  articulation_id INTEGER NOT NULL REFERENCES articulations(id),
  conjunction TEXT NOT NULL CHECK(conjunction IN ('And','Or','Single')),
  position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS articulation_group_members (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES articulation_course_groups(id),
  sending_course_id INTEGER NOT NULL REFERENCES courses(id),
  position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reverse_index (
  id INTEGER PRIMARY KEY,
  receiving_course_id INTEGER NOT NULL REFERENCES courses(id),
  sending_cc_id INTEGER NOT NULL REFERENCES institutions(id),
  sending_course_id INTEGER NOT NULL REFERENCES courses(id),
  is_standalone_equivalent BOOLEAN NOT NULL,
  companion_course_ids TEXT,
  academic_year_id INTEGER NOT NULL,
  source_context TEXT NOT NULL DEFAULT 'AllDepartments',
  receiving_companion_course_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_reverse ON reverse_index(
  receiving_course_id, academic_year_id
);
CREATE INDEX IF NOT EXISTS idx_reverse_receiving_year_cc ON reverse_index(
  receiving_course_id, academic_year_id, sending_cc_id
);
CREATE INDEX IF NOT EXISTS idx_reverse_sending ON reverse_index(
  sending_cc_id, academic_year_id
);

CREATE TABLE IF NOT EXISTS cross_listings (
  id INTEGER PRIMARY KEY,
  primary_course_id INTEGER NOT NULL REFERENCES courses(id),
  alias_course_id INTEGER NOT NULL REFERENCES courses(id),
  UNIQUE(primary_course_id, alias_course_id)
);

CREATE TABLE IF NOT EXISTS terms (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  season TEXT NOT NULL,
  year INTEGER NOT NULL,
  start_date TEXT,
  end_date TEXT
);

CREATE TABLE IF NOT EXISTS class_offerings (
  id INTEGER PRIMARY KEY,
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  course_id INTEGER REFERENCES courses(id),
  prefix TEXT NOT NULL,
  number TEXT NOT NULL,
  term_id INTEGER NOT NULL REFERENCES terms(id),
  modality TEXT NOT NULL CHECK(modality IN ('online_async','online_sync','online_mixed')),
  source TEXT NOT NULL,
  source_ref TEXT,
  fetched_at TEXT NOT NULL,
  UNIQUE(institution_id, prefix, number, term_id, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_offerings_lookup
  ON class_offerings(institution_id, prefix, number, term_id);
CREATE INDEX IF NOT EXISTS idx_offerings_course
  ON class_offerings(course_id, term_id);
CREATE INDEX IF NOT EXISTS idx_offerings_source_term
  ON class_offerings(source, term_id);

CREATE TABLE IF NOT EXISTS ingest_jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('cvc','assist')),
  status TEXT NOT NULL CHECK(status IN ('queued','dispatched','completed','failed')),
  requested_by TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error TEXT,
  metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_kind_started
  ON ingest_jobs(kind, started_at);
"""


def connect(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive migrations to already-created DBs.

    CREATE TABLE IF NOT EXISTS won't add new columns or change UNIQUE
    constraints on an existing table, so we probe and rewrite when needed.
    All migrations are idempotent.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(institutions)")}
    if "schedule_url" not in cols:
        conn.execute("ALTER TABLE institutions ADD COLUMN schedule_url TEXT")

    # articulations: add `source_context` + widen UNIQUE to include it.
    # SQLite can't alter a UNIQUE constraint in place; we rebuild the table.
    art_cols = {row[1] for row in conn.execute("PRAGMA table_info(articulations)")}
    if "source_context" not in art_cols:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            """
            CREATE TABLE articulations_new (
              id INTEGER PRIMARY KEY,
              receiving_course_id INTEGER NOT NULL REFERENCES courses(id),
              sending_cc_id INTEGER NOT NULL REFERENCES institutions(id),
              university_id INTEGER NOT NULL REFERENCES institutions(id),
              academic_year_id INTEGER NOT NULL,
              source_context TEXT NOT NULL DEFAULT 'AllDepartments',
              no_articulation_reason TEXT,
              UNIQUE(receiving_course_id, sending_cc_id, academic_year_id, source_context)
            );
            INSERT INTO articulations_new
              (id, receiving_course_id, sending_cc_id, university_id,
               academic_year_id, no_articulation_reason)
              SELECT id, receiving_course_id, sending_cc_id, university_id,
                     academic_year_id, no_articulation_reason
              FROM articulations;
            DROP TABLE articulations;
            ALTER TABLE articulations_new RENAME TO articulations;
            CREATE INDEX IF NOT EXISTS idx_art_lookup ON articulations(
              university_id, academic_year_id, receiving_course_id
            );
            """
        )
        conn.execute("PRAGMA foreign_keys = ON")

    rev_cols = {row[1] for row in conn.execute("PRAGMA table_info(reverse_index)")}
    if "source_context" not in rev_cols:
        conn.execute(
            "ALTER TABLE reverse_index "
            "ADD COLUMN source_context TEXT NOT NULL DEFAULT 'AllDepartments'"
        )
    if "receiving_companion_course_ids" not in rev_cols:
        conn.execute(
            "ALTER TABLE reverse_index "
            "ADD COLUMN receiving_companion_course_ids TEXT"
        )

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_inst_code_nocase
          ON institutions(code COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_courses_lookup_nocase
          ON courses(institution_id, prefix COLLATE NOCASE, number COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_art_receiving_year_cc
          ON articulations(receiving_course_id, academic_year_id, sending_cc_id);
        CREATE INDEX IF NOT EXISTS idx_reverse_receiving_year_cc
          ON reverse_index(receiving_course_id, academic_year_id, sending_cc_id);
        CREATE INDEX IF NOT EXISTS idx_offerings_source_term
          ON class_offerings(source, term_id);
        """
    )


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


def insert_course_group(
    conn: sqlite3.Connection,
    articulation_id: int,
    conjunction: str,
    position: int,
) -> int:
    cur = conn.execute(
        """INSERT INTO articulation_course_groups
             (articulation_id, conjunction, position)
           VALUES (?, ?, ?)""",
        (articulation_id, conjunction, position),
    )
    return cur.lastrowid


def insert_group_member(
    conn: sqlite3.Connection,
    group_id: int,
    sending_course_id: int,
    position: int,
) -> int:
    cur = conn.execute(
        """INSERT INTO articulation_group_members
             (group_id, sending_course_id, position)
           VALUES (?, ?, ?)""",
        (group_id, sending_course_id, position),
    )
    return cur.lastrowid


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


def insert_cross_listing(
    conn: sqlite3.Connection, primary_course_id: int, alias_course_id: int
) -> bool:
    cur = conn.execute(
        """INSERT OR IGNORE INTO cross_listings (primary_course_id, alias_course_id)
           VALUES (?, ?)""",
        (primary_course_id, alias_course_id),
    )
    return cur.rowcount > 0


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


def set_schedule_url(conn: sqlite3.Connection, code: str, url: str) -> int:
    """Set schedule_url on institutions matching `code` (trimmed). Returns rows affected."""
    cur = conn.execute(
        "UPDATE institutions SET schedule_url = ? WHERE code = ?",
        (url, code.strip()),
    )
    return cur.rowcount


def clear_articulation_data(
    conn: sqlite3.Connection, university_id: int, academic_year_id: int
) -> None:
    """Remove all articulation data for a university+year. Used to make
    ingestion idempotent.
    """
    conn.execute(
        """DELETE FROM articulation_group_members
           WHERE group_id IN (
             SELECT g.id FROM articulation_course_groups g
             JOIN articulations a ON a.id = g.articulation_id
             WHERE a.university_id = ? AND a.academic_year_id = ?
           )""",
        (university_id, academic_year_id),
    )
    conn.execute(
        """DELETE FROM articulation_course_groups
           WHERE articulation_id IN (
             SELECT id FROM articulations
             WHERE university_id = ? AND academic_year_id = ?
           )""",
        (university_id, academic_year_id),
    )
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
