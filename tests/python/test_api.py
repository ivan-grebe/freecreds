"""Tests for API query behavior that is easy to regress."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from freecreds import api, db

BUNDLE_CONTRACTS = json.loads(
    (Path(__file__).parents[1] / "contracts" / "bundle_offerings.json").read_text()
)


@pytest.mark.parametrize("contract", BUNDLE_CONTRACTS, ids=lambda case: case["name"])
def test_bundle_offering_contract(contract):
    offerings = {
        key: {int(term_id): modality for term_id, modality in terms.items()}
        for key, terms in contract["offerings"].items()
    }
    assert api._matching_bundle_modality(
        offerings,
        contract["keys"],
        contract["term_ids"],
        contract["async_only"],
    ) == contract["expected"]


def _seed_reverse_lookup_db(db_path: Path) -> None:
    conn = db.connect(db_path)
    db.init_db(conn)

    uni_id = db.upsert_institution(conn, 100, "CSUFULL", "Cal State Fullerton", 0, 0)
    old_cc_id = db.upsert_institution(conn, 200, "OLDCC", "Old College", 2, 0)
    new_cc_id = db.upsert_institution(conn, 300, "NEWCC", "New College", 2, 0)
    old_no_art_id = db.upsert_institution(conn, 400, "OLDNO", "Old No-Art College", 2, 0)
    new_no_art_id = db.upsert_institution(conn, 500, "NEWNO", "New No-Art College", 2, 0)

    receiving_course_id = db.upsert_course(
        conn, uni_id, 1000, "MATH", "170A", "Calculus", 4.0, 4.0
    )
    old_sending_course_id = db.upsert_course(
        conn, old_cc_id, 2000, "MATH", "1A", "Old Calculus", 4.0, 4.0
    )
    new_sending_course_id = db.upsert_course(
        conn, new_cc_id, 3000, "MATH", "1A", "New Calculus", 4.0, 4.0
    )

    db.insert_articulation(conn, receiving_course_id, old_cc_id, uni_id, 75, None, "{}")
    db.insert_articulation(conn, receiving_course_id, new_cc_id, uni_id, 76, None, "{}")
    db.insert_articulation(
        conn,
        receiving_course_id,
        old_no_art_id,
        uni_id,
        75,
        "No Course Articulated",
        "{}",
    )
    db.insert_articulation(
        conn,
        receiving_course_id,
        new_no_art_id,
        uni_id,
        76,
        "No Course Articulated",
        "{}",
    )
    db.insert_reverse_index_rows(
        conn,
        [
            (
                receiving_course_id,
                old_cc_id,
                old_sending_course_id,
                True,
                [],
                75,
                "AllDepartments",
                [],
            ),
            (
                receiving_course_id,
                new_cc_id,
                new_sending_course_id,
                True,
                [],
                76,
                "AllDepartments",
                [],
            ),
        ],
    )
    term_id = db.upsert_term(conn, "FA26", "Fall 2026", "Fall", 2026)
    db.upsert_class_offering(
        conn,
        institution_id=new_cc_id,
        course_id=None,
        prefix="MATH",
        number="1A",
        term_id=term_id,
        modality="online_async",
        source="cvc",
        source_ref="https://search.cvc.edu/courses/1",
        fetched_at="2026-04-23T00:00:00+00:00",
    )
    db.upsert_class_offering(
        conn,
        institution_id=new_cc_id,
        course_id=None,
        prefix="ENGL",
        number="1A",
        term_id=term_id,
        modality="online_async",
        source="cvc",
        source_ref="https://search.cvc.edu/courses/2",
        fetched_at="2026-04-23T00:00:00+00:00",
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "assist.db"
    _seed_reverse_lookup_db(db_path)
    monkeypatch.setattr(api, "DB_PATH", db_path)

    with TestClient(api.app) as test_client:
        yield test_client


def test_reverse_lookup_uses_latest_academic_year(client: TestClient):
    response = client.get("/api/reverse?university=CSUFULL&prefix=MATH&number=170A")

    assert response.status_code == 200
    data = response.json()
    assert data["query"]["academic_year_id"] == 76
    assert [row["cc_code"] for row in data["results"]] == ["NEWCC"]
    assert data["results"][0]["academic_year_id"] == 76
    assert [row["cc_code"] for row in data["no_articulation"]] == ["NEWNO"]


def test_reverse_lookup_rejects_invalid_term(client: TestClient):
    response = client.get("/api/reverse?university=CSUFULL&prefix=MATH&number=170A&term=BAD")

    assert response.status_code == 400
    assert "Invalid term code" in response.json()["detail"]


def test_reverse_lookup_term_filter_uses_matching_course(client: TestClient):
    response = client.get("/api/reverse?university=CSUFULL&prefix=MATH&number=170A&term=FA26")

    assert response.status_code == 200
    data = response.json()
    assert [row["cc_code"] for row in data["results"]] == ["NEWCC"]
    assert data["results"][0]["offering_status"] == "async_online"


def test_reverse_lookup_requires_every_course_in_bundle_for_term(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db_path = tmp_path / "assist.db"
    _seed_reverse_lookup_db(db_path)
    conn = db.connect(db_path)
    receiving_id = conn.execute(
        "SELECT id FROM courses WHERE institution_id = 1 AND prefix = 'MATH'"
    ).fetchone()[0]
    cc_id = conn.execute("SELECT id FROM institutions WHERE code = 'NEWCC'").fetchone()[0]
    first_id = conn.execute(
        "SELECT id FROM courses WHERE institution_id = ? AND prefix = 'MATH'",
        (cc_id,),
    ).fetchone()[0]
    companion_id = db.upsert_course(
        conn, cc_id, 3001, "MATH", "1B", "New Calculus II", 4.0, 4.0
    )
    conn.execute("DELETE FROM reverse_index WHERE academic_year_id = 76")
    db.insert_reverse_index_rows(
        conn,
        [
            (receiving_id, cc_id, first_id, False, [companion_id], 76, "AllDepartments", []),
            (receiving_id, cc_id, companion_id, False, [first_id], 76, "AllDepartments", []),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(api, "DB_PATH", db_path)

    with TestClient(api.app) as test_client:
        response = test_client.get(
            "/api/reverse?university=CSUFULL&prefix=MATH&number=170A&term=FA26"
        )

    assert response.status_code == 200
    assert response.json()["results"] == []


def test_async_any_term_ignores_historical_offerings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db_path = tmp_path / "assist.db"
    _seed_reverse_lookup_db(db_path)
    conn = db.connect(db_path)
    uni_id = conn.execute("SELECT id FROM institutions WHERE code = 'CSUFULL'").fetchone()[0]
    receiving_id = conn.execute(
        "SELECT id FROM courses WHERE institution_id = ? AND prefix = 'MATH'",
        (uni_id,),
    ).fetchone()[0]
    cc_id = db.upsert_institution(conn, 600, "PASTCC", "Past College", 2, 0)
    sending_id = db.upsert_course(
        conn, cc_id, 6000, "MATH", "1A", "Past Calculus", 4.0, 4.0
    )
    db.insert_articulation(conn, receiving_id, cc_id, uni_id, 76, None, "AllDepartments")
    db.insert_reverse_index_rows(
        conn,
        [(receiving_id, cc_id, sending_id, True, [], 76, "AllDepartments", [])],
    )
    old_term_id = db.upsert_term(conn, "SP25", "Spring 2025", "Spring", 2025)
    db.upsert_class_offering(
        conn, cc_id, sending_id, "MATH", "1A", old_term_id, "online_async", "cvc",
        "https://search.cvc.edu/courses/past", "2025-01-01T00:00:00+00:00",
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(api, "DB_PATH", db_path)

    with TestClient(api.app) as test_client:
        response = test_client.get(
            "/api/reverse?university=CSUFULL&prefix=MATH&number=170A&async_only=true"
        )

    assert response.status_code == 200
    assert [row["cc_code"] for row in response.json()["results"]] == ["NEWCC"]


def test_reverse_lookup_ignores_malformed_companion_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db_path = tmp_path / "assist.db"
    _seed_reverse_lookup_db(db_path)
    conn = db.connect(db_path)
    conn.execute(
        """UPDATE reverse_index
           SET companion_course_ids = ?, receiving_companion_course_ids = ?
           WHERE academic_year_id = 76""",
        ("not json", "[true, 123]"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(api, "DB_PATH", db_path)

    with TestClient(api.app) as test_client:
        response = test_client.get("/api/reverse?university=CSUFULL&prefix=MATH&number=170A")

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["companion_courses"] == []
    assert row["receiving_companion_courses"] == [{"id": 123}]
