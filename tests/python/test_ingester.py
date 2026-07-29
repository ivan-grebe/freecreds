"""Tests for ingestion command failure signaling."""
from __future__ import annotations

from pathlib import Path

import pytest

from freecreds import db, ingester
from freecreds.parser import CourseRef


def test_all_targets_returns_failure_when_any_target_failed(monkeypatch):
    monkeypatch.setattr(
        ingester,
        "ingest_all_targets",
        lambda *_args, **_kwargs: {"succeeded": ["CSUFULL"], "failed": ["UCB"]},
    )

    assert ingester.main(["--all"]) == 1


def test_all_targets_returns_success_when_every_target_succeeded(monkeypatch):
    monkeypatch.setattr(
        ingester,
        "ingest_all_targets",
        lambda *_args, **_kwargs: {"succeeded": ["CSUFULL"], "failed": []},
    )

    assert ingester.main(["--all"]) == 0


def test_course_cache_skips_identical_writes_but_keeps_metadata_current():
    conn = db.connect(Path(":memory:"))
    db.init_db(conn)
    institution_id = db.upsert_institution(
        conn, 100, "CSUFULL", "Cal State Fullerton", 0, 0
    )
    cache: ingester.CourseCache = {}
    counts = ingester._empty_counts()
    course = CourseRef(1000, "MATH", "150", "Calculus", 4.0, 4.0)

    first_id = ingester._ensure_course(conn, course, institution_id, cache, counts)
    second_id = ingester._ensure_course(conn, course, institution_id, cache, counts)

    assert second_id == first_id
    assert counts["courses"] == 1

    updated_course = CourseRef(
        1000, "MATH", "150", "Calculus I", 4.0, 4.0, is_terminated=True
    )
    updated_id = ingester._ensure_course(
        conn, updated_course, institution_id, cache, counts
    )
    row = conn.execute(
        "SELECT title, is_terminated FROM courses WHERE id = ?", (updated_id,)
    ).fetchone()
    conn.close()

    assert updated_id == first_id
    assert counts["courses"] == 2
    assert tuple(row) == ("Calculus I", 1)


def test_failed_agreement_fetch_preserves_previous_university_snapshot():
    conn = db.connect(Path(":memory:"))
    db.init_db(conn)
    university_id = db.upsert_institution(
        conn, 100, "CSUFULL", "Cal State Fullerton", 0, 0
    )
    college_id = db.upsert_institution(conn, 200, "TESTCC", "Test College", 2, 0)
    course_id = db.upsert_course(
        conn, university_id, 1000, "MATH", "150", "Calculus", 4.0, 4.0
    )
    db.insert_articulation(
        conn, course_id, college_id, university_id, 76, "previous snapshot"
    )
    conn.commit()

    institutions = [
        {
            "id": 100,
            "code": "CSUFULL",
            "category": 0,
            "termType": 0,
            "names": [{"name": "Cal State Fullerton"}],
        },
        {
            "id": 200,
            "code": "TESTCC",
            "category": 2,
            "termType": 0,
            "names": [{"name": "Test College"}],
        },
    ]

    class FailingClient:
        def get_agreements_from(self, _institution_id):
            return [
                {
                    "sendingYearIds": [76],
                    "receivingInstitution": {
                        "id": 200,
                        "code": "TESTCC",
                        "isCommunityCollege": True,
                    },
                }
            ]

        def list_agreement_keys(self, *_args, types="Department"):
            report_type = "AllDepartments" if types == "Department" else "Other"
            return {"allReports": [{"type": report_type, "key": "agreement-key"}]}

        def get_agreement(self, _key):
            raise OSError("temporary ASSIST failure")

    with pytest.raises(RuntimeError, match="previous articulation data was preserved"):
        ingester._ingest_university_from_context(
            conn,
            FailingClient(),
            institutions,
            {100: university_id, 200: college_id},
            "CSUFULL",
        )

    preserved = conn.execute(
        "SELECT no_articulation_reason FROM articulations WHERE university_id = ?",
        (university_id,),
    ).fetchall()
    conn.close()
    assert [row[0] for row in preserved] == ["previous snapshot"]
