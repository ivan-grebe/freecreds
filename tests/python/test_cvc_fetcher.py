"""Tests for CVC parsing, crawling, and publication."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from freecreds import cvc_fetcher, db
from freecreds.cvc_fetcher import (
    CVC_HOME_UNIVERSITY_ID,
    CVCClient,
    OfferingRecord,
    advertised_last_page,
    count_cards,
    ensure_term,
    has_next_page,
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
    assert "?" not in records[0].source_ref

    assert records[1].prefix == "ENGL"
    assert records[1].number == "1A"


def test_count_cards_matches_parse():
    assert count_cards(CARD_HTML) == 2
    assert count_cards("<html><body>no results</body></html>") == 0


def test_parse_search_html_decodes_numeric_entities():
    html = CARD_HTML.replace("Coalinga College", "Coalinga&#32;College")
    html = html.replace("MATH45", "MATH&#52;5")
    records = parse_search_html(html, term_code="FA26", modality="online_async")
    assert records[0].college_name == "Coalinga College"
    assert records[0].number == "45"


def test_has_next_page_detects_enabled_pagination_link():
    assert has_next_page('<a href="/search?page=2" rel="next">Next</a>')
    assert not has_next_page('<span class="page next disabled">Next</span>')


def test_advertised_last_page_reads_the_final_pager_link():
    html = '<a href="/search?page=2" rel="next">2</a> <a href="/search?page=493">493</a>'
    assert advertised_last_page(html) == 493
    assert advertised_last_page("<html></html>") is None


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


def test_search_html_retries_transient_failures(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cvc_fetcher.time, "sleep", lambda _seconds: None)

    class FakeResponse:
        status_code = 200
        text = "<html></html>"

    class FakeHttpClient:
        def __init__(self):
            self.calls = 0

        def get(self, _url, *, params):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ReadTimeout("timed out")
            return FakeResponse()

    client = CVCClient.__new__(CVCClient)
    client.base_url = "https://search.cvc.edu"
    client._client = FakeHttpClient()
    client._last_request_at = 0.0
    monkeypatch.setattr(client, "_throttle", lambda: None)

    assert client.search_html(parse_code("FA26"), "online_async", "MATH") == "<html></html>"
    assert client._client.calls == 2


def test_parse_search_html_skips_unparseable_titles():
    html = """
    <div class="course border-gray-400">
      <div class="font-semibold text-sm">Nowhere College</div>
      <a class="course-details-link" href="/x">Something without a course code</a>
    </div>
    """
    assert parse_search_html(html, term_code="FA26", modality="online_async") == []


def test_parse_synthetic_fixture_has_cards():
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")
    records = parse_search_html(html, term_code="FA26", modality="online_async")
    assert len(records) == 3
    for r in records:
        assert r.college_name
        assert r.prefix.isalpha()
        assert r.number[0].isdigit()


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


@pytest.mark.parametrize("failed_page", [1, 2])
def test_failed_crawl_preserves_existing_offerings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_page: int,
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

        def search_html(self, *_args, page=1):
            if page == failed_page:
                return None
            return CARD_HTML + '<a href="/search?page=2" rel="next">Next</a>'

    monkeypatch.setattr(cvc_fetcher, "CVCClient", FailingClient)

    with pytest.raises(RuntimeError, match="existing offerings were preserved"):
        cvc_fetcher.ingest_terms(["FA26"], db_path=db_path, subjects=["MATH"])

    conn = db.connect(db_path)
    refs = [row[0] for row in conn.execute("SELECT source_ref FROM class_offerings")]
    conn.close()
    assert refs == ["https://search.cvc.edu/courses/original"]


@pytest.mark.parametrize("html", [
    "<html><body><p>No recognizable cards</p></body></html>",
    '<div class="course border-gray-400"><p>New markup</p></div>',
    CARD_HTML.replace("MATH45", "WE186WELD").replace("ENGL1A", "E.S.L.1"),
])
def test_empty_crawl_preserves_existing_offerings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    html: str,
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

    class EmptyClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def available_session_names(self):
            return {"Fall 2026"}

        def search_html(self, *_args, **_kwargs):
            return html

    monkeypatch.setattr(cvc_fetcher, "CVCClient", EmptyClient)

    with pytest.raises(RuntimeError, match="produced no offerings"):
        cvc_fetcher.ingest_terms(["FA26"], db_path=db_path, subjects=["MATH"])

    conn = db.connect(db_path)
    refs = [row[0] for row in conn.execute("SELECT source_ref FROM class_offerings")]
    conn.close()
    assert refs == ["https://search.cvc.edu/courses/original"]


@pytest.mark.parametrize("bad_page", [1, 2, 3])
@pytest.mark.parametrize("bad_html", [
    '<div class="course border-gray-400"><p>New markup</p></div>',
    CARD_HTML.replace("MATH45", "WE186WELD").replace("ENGL1A", "E.S.L.1"),
    CARD_HTML.replace("MATH45", "WE186WELD"),
])
def test_unparseable_cards_do_not_block_pagination_or_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    bad_page: int,
    bad_html: str,
):
    db_path = tmp_path / "assist.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    for index, (code, name) in enumerate([
        ("WHC", "Coalinga College"), ("SJCC", "San Jose City College"),
    ]):
        db.upsert_institution(conn, index + 200, code, name, 2, 0)
    cc_id = conn.execute("SELECT id FROM institutions WHERE code = 'WHC'").fetchone()[0]
    for code in ["FA26", "SP27"]:
        term_id = ensure_term(conn, parse_code(code))
        db.upsert_class_offering(
            conn, cc_id, None, "MATH", "45", term_id, "online_async", "cvc",
            f"https://search.cvc.edu/courses/old-{code}", "2026-01-01T00:00:00+00:00",
        )
    conn.commit()
    conn.close()
    requests = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def available_session_names(self):
            return {"Fall 2026"}

        def search_html(self, term, modality, subject, page=1):
            requests.append((modality, subject, page))
            if modality == "online_sync":
                return "<html></html>"
            html = bad_html if subject == "MATH" and page == bad_page else CARD_HTML
            html = html.replace("/courses/", f"/courses/{subject}-{page}-")
            if subject == "MATH" and page < 3:
                html += f'<a href="/search?page={page + 1}" rel="next">Next</a>'
            return html

    monkeypatch.setattr(cvc_fetcher, "CVCClient", Client)
    cvc_fetcher.ingest_terms(["FA26"], db_path=db_path, subjects=["MATH", "ZOO"])

    assert requests == [
        ("online_async", "MATH", 1),
        ("online_async", "MATH", 2),
        ("online_async", "MATH", 3),
        ("online_async", "ZOO", 1),
        ("online_sync", "MATH", 1),
        ("online_sync", "ZOO", 1),
    ]
    conn = db.connect(db_path)
    refs = {row[0] for row in conn.execute("SELECT source_ref FROM class_offerings")}
    conn.close()
    expected = {
        f"https://search.cvc.edu/courses/MATH-{page}-{course}"
        for page in [1, 2, 3] if page != bad_page
        for course in ["1842959", "999"]
    }
    expected.update({
        "https://search.cvc.edu/courses/ZOO-1-1842959",
        "https://search.cvc.edu/courses/ZOO-1-999",
        "https://search.cvc.edu/courses/old-SP27",
    })
    if "ENGL1A" in bad_html:
        expected.add(f"https://search.cvc.edu/courses/MATH-{bad_page}-999")
    assert refs == expected
    assert "unparseable CVC cards" in caplog.text
    assert f"FA26/online_async subject=MATH page={bad_page}" in caplog.text


def test_implausibly_small_refresh_requires_explicit_override():
    conn = _seed_conn()
    term_id = ensure_term(conn, parse_code("FA26"))
    institution_id = conn.execute(
        "SELECT id FROM institutions WHERE code = 'WHC'"
    ).fetchone()[0]
    for index in range(cvc_fetcher.MIN_REFRESH_BASELINE):
        db.upsert_class_offering(
            conn,
            institution_id,
            None,
            "MATH",
            str(index),
            term_id,
            "online_async",
            "cvc",
            f"https://search.cvc.edu/courses/old-{index}",
            "2026-01-01T00:00:00+00:00",
        )
    cvc_fetcher._prepare_offering_staging(conn)
    conn.execute(
        """INSERT INTO cvc_offerings_staging
             (institution_id, course_id, prefix, number, term_id, modality,
              source, source_ref, fetched_at)
           VALUES (?, NULL, 'MATH', '1', ?, 'online_async', 'cvc',
                   'https://search.cvc.edu/courses/new', '2026-02-01T00:00:00+00:00')""",
        (institution_id, term_id),
    )

    with pytest.raises(RuntimeError, match="implausibly small"):
        cvc_fetcher._validate_staged_offerings(
            conn,
            source="cvc",
            term_ids=[term_id],
            allow_small_refresh=False,
        )

    cvc_fetcher._validate_staged_offerings(
        conn,
        source="cvc",
        term_ids=[term_id],
        allow_small_refresh=True,
    )
