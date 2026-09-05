from __future__ import annotations

import sqlite3

import pytest

from freecreds import db


def test_database_initialization_is_idempotent():
    conn = sqlite3.connect(":memory:")

    db.init_db(conn)
    db.upsert_institution(conn, 1, "TEST", "Test University", 0, 0)
    conn.commit()
    db.init_db(conn)

    assert conn.execute("SELECT code, name FROM institutions").fetchall() == [
        ("TEST", "Test University")
    ]
    conn.close()


def test_untracked_database_must_be_regenerated():
    conn = sqlite3.connect(":memory:")
    conn.executescript((db.MIGRATIONS_DIR / "0001_schema.sql").read_text(encoding="utf-8"))
    conn.execute(
        """INSERT INTO institutions
             (assist_id, code, name, category, term_type)
           VALUES (1, 'TEST', 'Test University', 'CSU', 'Semester')"""
    )
    conn.commit()

    with pytest.raises(RuntimeError, match="Delete it and regenerate"):
        db.init_db(conn)

    assert conn.execute("SELECT name FROM institutions WHERE code = 'TEST'").fetchone()[0] == (
        "Test University"
    )
