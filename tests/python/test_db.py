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


def test_removing_unused_persistence_preserves_query_data():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    for name in [
        "0001_schema.sql", "0002_reverse_index_receiving_companions.sql",
        "0003_query_read_indexes.sql", "0004_remove_schedule_url.sql",
    ]:
        conn.executescript((db.MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
    conn.executescript("""
        INSERT INTO institutions (id, assist_id, code, name, category, term_type)
        VALUES (1, 1, 'UNI', 'University', 'UC', 'Semester'),
               (2, 2, 'CC', 'College', 'CCC', 'Semester');
        INSERT INTO courses
            (id, institution_id, course_identifier_parent_id, prefix, number, title)
        VALUES (1, 1, 1, 'MATH', '1', 'Calculus'),
               (2, 2, 2, 'MATH', '10', 'Calculus');
        INSERT INTO articulations
            (id, receiving_course_id, sending_cc_id, university_id, academic_year_id)
        VALUES (1, 1, 2, 1, 76);
        INSERT INTO articulation_course_groups VALUES (1, 1, 'Single', 0);
        INSERT INTO articulation_group_members VALUES (1, 1, 2, 0);
        INSERT INTO cross_listings VALUES (1, 1, 2);
        INSERT INTO reverse_index
            (receiving_course_id, sending_cc_id, sending_course_id,
             is_standalone_equivalent, academic_year_id)
        VALUES (1, 2, 2, 1, 76);
    """)
    conn.executescript(
        (db.MIGRATIONS_DIR / "0005_remove_unused_persistence.sql").read_text(encoding="utf-8")
    )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("""
        SELECT receiving.number, sending.number
        FROM reverse_index ri
        JOIN courses receiving ON receiving.id = ri.receiving_course_id
        JOIN courses sending ON sending.id = ri.sending_course_id
    """).fetchall() == [("1", "10")]
    db.clear_articulation_data(conn, 1, 76)
    assert conn.execute("SELECT COUNT(*) FROM reverse_index").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM articulations").fetchone()[0] == 0
    conn.close()


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
