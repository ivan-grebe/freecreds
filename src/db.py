"""SQLite schema and helpers.

One connection per caller. No ORM for MVP — plain sqlite3.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

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
  term_type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inst_code ON institutions(code);
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

CREATE TABLE IF NOT EXISTS articulations (
  id INTEGER PRIMARY KEY,
  receiving_course_id INTEGER NOT NULL REFERENCES courses(id),
  sending_cc_id INTEGER NOT NULL REFERENCES institutions(id),
  university_id INTEGER NOT NULL REFERENCES institutions(id),
  academic_year_id INTEGER NOT NULL,
  no_articulation_reason TEXT,
  raw_json TEXT NOT NULL,
  UNIQUE(receiving_course_id, sending_cc_id, academic_year_id)
);
CREATE INDEX IF NOT EXISTS idx_art_lookup ON articulations(
  university_id, academic_year_id, receiving_course_id
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
  academic_year_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reverse ON reverse_index(
  receiving_course_id, academic_year_id
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
    min_units: Optional[float],
    max_units: Optional[float],
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
    no_articulation_reason: Optional[str],
    raw_json: str,
) -> Optional[int]:
    """Insert an articulation row. Returns the row id, or None if a
    duplicate already exists (which can happen when a course appears in
    multiple departments).
    """
    cur = conn.execute(
        """INSERT OR IGNORE INTO articulations
             (receiving_course_id, sending_cc_id, university_id,
              academic_year_id, no_articulation_reason, raw_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            receiving_course_id,
            sending_cc_id,
            university_id,
            academic_year_id,
            no_articulation_reason,
            raw_json,
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
    rows: Iterable[Tuple[int, int, int, bool, List[int], int]],
) -> None:
    """rows: iterable of (receiving_course_id, sending_cc_id, sending_course_id,
    is_standalone, companion_ids, academic_year_id).
    """
    payload = [
        (rcid, ccid, scid, 1 if standalone else 0, json.dumps(companions), yid)
        for (rcid, ccid, scid, standalone, companions, yid) in rows
    ]
    conn.executemany(
        """INSERT INTO reverse_index
             (receiving_course_id, sending_cc_id, sending_course_id,
              is_standalone_equivalent, companion_course_ids, academic_year_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        payload,
    )


def insert_cross_listing(
    conn: sqlite3.Connection, primary_course_id: int, alias_course_id: int
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO cross_listings (primary_course_id, alias_course_id)
           VALUES (?, ?)""",
        (primary_course_id, alias_course_id),
    )


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
