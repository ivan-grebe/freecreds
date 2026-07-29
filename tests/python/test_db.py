from __future__ import annotations

import sqlite3

import pytest

from freecreds import db


def test_fresh_database_records_and_reuses_sql_migrations():
    conn = sqlite3.connect(":memory:")

    db.init_db(conn)
    db.init_db(conn)

    applied = [
        row[0]
        for row in conn.execute(
            f"SELECT name FROM {db.LOCAL_MIGRATIONS_TABLE} ORDER BY name"
        )
    ]
    assert applied == [migration.name for migration in sorted(db.MIGRATIONS_DIR.glob("*.sql"))]
    columns = [row[1] for row in conn.execute("PRAGMA table_info(institutions)")]
    assert "schedule_url" not in columns


def test_untracked_database_must_be_regenerated():
    conn = sqlite3.connect(":memory:")
    conn.executescript((db.MIGRATIONS_DIR / "0001_schema.sql").read_text(encoding="utf-8"))
    conn.execute(
        """INSERT INTO institutions
             (assist_id, code, name, category, term_type, schedule_url)
           VALUES (1, 'TEST', 'Test University', 'CSU', 'Semester', 'https://old.test')"""
    )
    conn.commit()

    with pytest.raises(RuntimeError, match="Delete it and regenerate"):
        db.init_db(conn)

    assert conn.execute("SELECT name FROM institutions WHERE code = 'TEST'").fetchone()[0] == (
        "Test University"
    )
