"""Tests for CVC HTML parsing + DB writer.

Network code (CVCClient.search_html) is exercised only via fixture HTML;
the live endpoint is covered by manual smoke test.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from freecreds import cvc_fetcher, db
from freecreds.cvc_fetcher import (
    CVC_HOME_UNIVERSITY_ID,
    CVCClient,
    OfferingRecord,
    count_cards,
    ensure_term,
    has_next_page,
    parse_home_college_options,
    parse_search_html,
    parse_session_names,
    write_offerings,
)
from freecreds.terms import parse_code

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "cvc_response_sample.html"


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db.init_db(conn)
    for code, name in [
        ("SMCC", "Santa Monica College"),
        ("WHC", "Coalinga College"),
        ("SJCC", "San Jose City College"),
        ("FRESNO", "Fresno City College"),
        ("MERRITT", "Merritt College"),
    ]:
        conn.execute(
            """INSERT INTO institutions (assist_id, code, name, category, term_type)
               VALUES (?, ?, ?, 'CCC', 'Semester')""",
            (hash(code) & 0x7fffffff, code, name),
        )
    conn.commit()
    return conn


CARD_HTML = """
<div id="search-results">
  <div class="course border-gray-400 border rounded-t-md bg-white">
    <div class="course-head">
      <div class="font-semibold text-sm">Coalinga College</div>
      <h3>
        <a class="course-details-link" href="/courses/1842959?filter%5Buniversity_id%5D=101">
          MATH45 - Contemporary Math
        </a>
      </h3>
    </div>
  </div>
  <div class="course border-gray-400 border rounded-t-md bg-white">
    <div class="course-head">
      <div class="font-semibold text-sm">San Jose City College</div>
      <h3>
        <a class="course-details-link" href="/courses/999">ENGL1A - Reading &amp; Writing</a>
      </h3>
    </div>
  </div>
</div>
"""


def test_parse_search_html_minimal():
    records = parse_search_html(CARD_HTML, term_code="FA26", modality="online_async")
    assert len(records) == 2
    assert records[0].college_name == "Coalinga College"
    assert records[0].prefix == "MATH"
    assert records[0].number == "45"
    assert records[0].term_code == "FA26"
    assert records[0].modality == "online_async"
    assert "/courses/1842959" in records[0].source_ref
    assert "?" not in records[0].source_ref  # query string stripped

    assert records[1].prefix == "ENGL"
    assert records[1].number == "1A"


def test_count_cards_matches_parse():
    assert count_cards(CARD_HTML) == 2
    assert count_cards("<html><body>no results</body></html>") == 0


def test_has_next_page_detects_enabled_pagination_link():
    assert has_next_page('<a href="/search?page=2" rel="next">Next</a>')
    assert not has_next_page('<span class="page next disabled">Next</span>')


def test_search_html_sends_home_context_and_normalized_subject():
    class FakeResponse:
        status_code = 200
        url = "https://search.cvc.edu/search"
        text = "<html></html>"

    class FakeHttpClient:
        def __init__(self):
            self.params = None

        def get(self, _url, *, params):
            self.params = params
            return FakeResponse()

    fake = FakeHttpClient()
    client = CVCClient.__new__(CVCClient)
    client.base_url = "https://search.cvc.edu"
    client._client = fake
    client._last_request_at = 0.0

    term = parse_code("FA26")
    assert client.search_html(term, "online_async", "MATH", page=3) == "<html></html>"
    assert ("filter[search_all_universities]", "false") in fake.params
    assert ("filter[display_home_school]", "false") in fake.params
    assert ("filter[university_id]", CVC_HOME_UNIVERSITY_ID) in fake.params
    assert ("filter[subject]", "math") in fake.params
    assert ("page", "3") in fake.params


def test_parse_search_html_skips_unparseable_titles():
    html = """
    <div class="course border-gray-400">
      <div class="font-semibold text-sm">Nowhere College</div>
      <a class="course-details-link" href="/x">Something without a course code</a>
    </div>
    """
    assert parse_search_html(html, term_code="FA26", modality="online_async") == []


def test_parse_synthetic_fixture_has_cards():
    if not FIXTURE.exists():
        return  # fixture optional
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")
    records = parse_search_html(html, term_code="FA26", modality="online_async")
    # The fixture is deliberately small and entirely synthetic.
    assert len(records) == 3
    # Every record should have a non-empty college + prefix + digit-starting number
    for r in records:
        assert r.college_name
        assert r.prefix.isalpha()
        assert r.number[0].isdigit()


def test_parse_home_college_options():
    if not FIXTURE.exists():
        return
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")
    opts = parse_home_college_options(html)
    # Synthetic options exercise the same selector used by the live page.
    assert len(opts) == 3
    names = {name for _, name in opts}
    assert "Santa Monica College" in names or "Coalinga College" in names


def test_parse_session_names():
    html = """
    <input name="filter[session_names][]" value="Spring 2026" type="checkbox">
    <input type="checkbox" value="Summer 2026" name="filter[session_names][]">
    <input name="something_else" value="Fall 2099">
    """
    assert parse_session_names(html) == {"Spring 2026", "Summer 2026"}


def test_available_session_names():
    class FakeResponse:
        status_code = 200
        text = """
        <input name="filter[session_names][]" value="Summer 2026">
        <input name="filter[session_names][]" value="Fall 2026">
        """

    class FakeHttpClient:
        def get(self, url, *, params):
            assert url == "https://search.cvc.edu/search"
            assert ("filter[university_id]", CVC_HOME_UNIVERSITY_ID) in params
            assert ("filter[subject]", "math") in params
            return FakeResponse()

    client = CVCClient.__new__(CVCClient)
    client.base_url = "https://search.cvc.edu"
    client._client = FakeHttpClient()
    client._last_request_at = 0.0

    assert client.available_session_names() == {"Summer 2026", "Fall 2026"}


def test_write_offerings_smoke():
    conn = _seed_conn()
    term = parse_code("FA26")
    ensure_term(conn, term)
    conn.commit()

    records = [
        OfferingRecord(
            college_name="Coalinga College",
            prefix="MATH", number="45",
            term_code="FA26", modality="online_async",
            source_ref="https://search.cvc.edu/courses/1842959",
        ),
        OfferingRecord(
            college_name="Unknown Unmapped College",
            prefix="MATH", number="10",
            term_code="FA26", modality="online_async",
            source_ref="x",
        ),
    ]
    counts = write_offerings(conn, records, term)
    assert counts["written"] == 1
    assert counts["skipped_unknown_college"] == 1
    assert counts["skipped_missing_institution"] == 0

    row = conn.execute(
        "SELECT modality, source FROM class_offerings WHERE prefix='MATH' AND number='45'"
    ).fetchone()
    assert row["modality"] == "online_async"
    assert row["source"] == "cvc"


def test_write_offerings_counts_missing_institution_separately():
    conn = _seed_conn()
    term = parse_code("FA26")
    ensure_term(conn, term)
    conn.commit()
    conn.execute("DELETE FROM institutions WHERE code = 'SMCC'")
    conn.commit()

    records = [
        OfferingRecord(
            college_name="Santa Monica College",
            prefix="MATH", number="45",
            term_code="FA26", modality="online_async",
            source_ref="https://search.cvc.edu/courses/1",
        )
    ]
    counts = write_offerings(conn, records, term)
    assert counts["written"] == 0
    assert counts["skipped_unknown_college"] == 0
    assert counts["skipped_missing_institution"] == 1


def test_write_offerings_idempotent_upsert():
    conn = _seed_conn()
    term = parse_code("FA26")
    ensure_term(conn, term)
    conn.commit()
    records = [OfferingRecord(
        college_name="Coalinga College",
        prefix="MATH", number="45",
        term_code="FA26", modality="online_async",
        source_ref="https://search.cvc.edu/courses/1842959",
    )]
    write_offerings(conn, records, term)
    write_offerings(conn, records, term)
    count = conn.execute("SELECT COUNT(*) FROM class_offerings").fetchone()[0]
    assert count == 1


def test_write_offerings_uses_constant_lookup_queries():
    conn = _seed_conn()
    term = parse_code("FA26")
    ensure_term(conn, term)
    conn.commit()
    statements = []
    conn.set_trace_callback(statements.append)
    records = [
        OfferingRecord(
            college_name="Coalinga College",
            prefix="MATH",
            number="45",
            term_code="FA26",
            modality="online_async",
            source_ref=f"https://search.cvc.edu/courses/{index}",
        )
        for index in range(25)
    ]

    write_offerings(conn, records, term)

    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 3
    assert conn.execute("SELECT COUNT(*) FROM class_offerings").fetchone()[0] == 25


def test_failed_crawl_preserves_existing_offerings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db_path = tmp_path / "assist.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    cc_id = db.upsert_institution(conn, 200, "WHC", "Coalinga College", 2, 0)
    db.upsert_course(conn, cc_id, 2000, "MATH", "45", "Math", 3.0, 3.0)
    term_id = ensure_term(conn, parse_code("FA26"))
    db.upsert_class_offering(
        conn, cc_id, None, "MATH", "45", term_id, "online_async", "cvc",
        "https://search.cvc.edu/courses/original", "2026-01-01T00:00:00+00:00",
    )
    conn.commit()
    conn.close()

    class FailingClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def available_session_names(self):
            return {"Fall 2026"}

        def search_html(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(cvc_fetcher, "CVCClient", FailingClient)

    with pytest.raises(RuntimeError, match="existing offerings were preserved"):
        cvc_fetcher.ingest_terms(["FA26"], db_path=db_path, subjects=["MATH"])

    conn = db.connect(db_path)
    refs = [row[0] for row in conn.execute("SELECT source_ref FROM class_offerings")]
    conn.close()
    assert refs == ["https://search.cvc.edu/courses/original"]
