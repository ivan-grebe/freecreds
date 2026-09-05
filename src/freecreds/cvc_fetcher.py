"""Fetch CVC online offerings for the CCC subject prefixes in the ASSIST database.

Searches require a subject and the browser-style home-school filters below.
Unparseable cards are skipped; HTTP failures and empty or implausibly small
crawls abort before replacing existing offerings.
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import httpx

from . import db
from .cvc_college_map import lookup as cvc_code_for
from .terms import Term, parse_code

log = logging.getLogger(__name__)

CVC_BASE_URL = "https://search.cvc.edu"
USER_AGENT = "FreeCreds/0.1 (+https://github.com/ivan-grebe/freecreds)"
THROTTLE_S = 0.6
CVC_MAX_RETRIES = 4
CVC_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
MAX_PAGES_PER_QUERY = 1_000  # emergency ceiling if CVC omits its advertised final page
CVC_HOME_UNIVERSITY_ID = "101"
MIN_REFRESH_BASELINE = 100
MIN_REFRESH_RATIO = 0.2
MODALITIES = ("online_async", "online_sync")
WRITE_COUNT_KEYS = ("written", "skipped_unknown_college", "skipped_missing_institution")


def _empty_write_counts() -> dict[str, int]:
    return {key: 0 for key in WRITE_COUNT_KEYS}


def _add_write_counts(total: dict[str, int], counts: dict[str, int]) -> None:
    for key in WRITE_COUNT_KEYS:
        total[key] += counts.get(key, 0)


@dataclass(frozen=True)
class OfferingRecord:
    """Normalized CVC record. The DB writer consumes these directly."""
    college_name: str
    prefix: str
    number: str
    term_code: str
    modality: str
    source_ref: str


@dataclass(frozen=True)
class OfferingLookups:
    institutions_by_code: dict[str, int]
    courses_by_key: dict[tuple[int, str, str], int]


_CARD_SPLIT = re.compile(r'<div class="course border-gray-400[^"]*"', re.IGNORECASE)

_COLLEGE_RE = re.compile(
    r'<div class="font-semibold text-sm">\s*([^<]+?)\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
_LINK_RE = re.compile(
    r'<a[^>]*class="course-details-link[^"]*"[^>]*href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_TOKEN_RE = re.compile(r'^([A-Z][A-Z&/]{0,9})\s*([0-9]+[A-Z]{0,3})\b')


def parse_search_html(
    html: str,
    *,
    term_code: str,
    modality: str,
) -> list[OfferingRecord]:
    """Extract OfferingRecords from one rendered CVC search page."""
    chunks = _CARD_SPLIT.split(html)
    out: list[OfferingRecord] = []
    for chunk in chunks[1:]:
        if "No upcoming sessions matching your filter" in chunk:
            continue
        college_m = _COLLEGE_RE.search(chunk)
        link_m = _LINK_RE.search(chunk)
        if not (college_m and link_m):
            continue
        college = unescape(college_m.group(1))
        href = unescape(link_m.group(1))
        title_text = unescape(link_m.group(2))
        token = _TITLE_TOKEN_RE.match(title_text.upper())
        if not token:
            continue
        prefix, number = token.group(1), token.group(2)
        source_ref = href.split("?", 1)[0] if href.startswith("http") else (
            CVC_BASE_URL + href.split("?", 1)[0]
        )
        out.append(OfferingRecord(
            college_name=college,
            prefix=prefix,
            number=number,
            term_code=term_code,
            modality=modality,
            source_ref=source_ref,
        ))
    return out


def count_cards(html: str) -> int:
    """Used to detect end-of-pagination: a zero-card page means stop."""
    return max(0, len(_CARD_SPLIT.split(html)) - 1)


def has_next_page(html: str) -> bool:
    """Return whether CVC rendered an enabled pagination next link."""
    return bool(re.search(r'<a\b[^>]*\brel=["\']next["\']', html, re.IGNORECASE))


def advertised_last_page(html: str) -> int | None:
    """Return CVC's last advertised result page, when its pager exposes one."""
    page_numbers = [int(value) for value in re.findall(r"[?&]page=(\d+)", html)]
    return max(page_numbers, default=None)


class _SessionNameParser(HTMLParser):
    """Extract the terms CVC currently exposes in its search filters."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.session_names: set[str] = set()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag != "input":
            return
        values = dict(attrs)
        if values.get("name") != "filter[session_names][]":
            return
        value = (values.get("value") or "").strip()
        if value:
            self.session_names.add(value)


def parse_session_names(html: str) -> set[str]:
    """Return session labels such as ``{"Summer 2026", "Fall 2026"}``."""
    parser = _SessionNameParser()
    parser.feed(html)
    return parser.session_names


class CVCClient:
    def __init__(self, base_url: str = CVC_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=5.0),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            follow_redirects=True,
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CVCClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request_at
        if gap < THROTTLE_S:
            time.sleep(THROTTLE_S - gap)
        self._last_request_at = time.monotonic()

    def available_session_names(self) -> set[str] | None:
        """Fetch the term labels currently advertised by CVC.

        ``None`` means discovery failed, in which case callers should retain
        their requested terms rather than interpreting the failure as an empty
        catalog.
        """
        self._throttle()
        params = [
            ("filter[search_type]", "open_search"),
            ("filter[search_all_universities]", "false"),
            ("filter[display_home_school]", "false"),
            ("filter[university_id]", CVC_HOME_UNIVERSITY_ID),
            ("filter[subject]", "math"),
        ]
        try:
            response = self._client.get(f"{self.base_url}/search", params=params)
        except httpx.HTTPError as exc:
            log.warning("Could not discover CVC sessions: %s", exc)
            return None
        if response.status_code >= 400:
            log.warning("CVC session discovery returned HTTP %d", response.status_code)
            return None
        sessions = parse_session_names(response.text)
        if not sessions:
            log.warning("CVC search page contained no session filters")
            return None
        return sessions

    def _get_with_retries(self, url: str, *, params: list[tuple[str, str]], page: int) -> httpx.Response:
        """Retry transient CVC HTTP failures."""
        last_error: httpx.HTTPError | None = None
        for attempt in range(1, CVC_MAX_RETRIES + 1):
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                detail = str(exc)
            else:
                if response.status_code not in CVC_RETRYABLE_STATUS_CODES:
                    return response
                detail = f"HTTP {response.status_code}"
                if attempt == CVC_MAX_RETRIES:
                    return response
            if attempt < CVC_MAX_RETRIES:
                delay = 2 ** (attempt - 1)
                log.warning(
                    "CVC request failed (page %d, attempt %d/%d): %s; retrying in %ds",
                    page,
                    attempt,
                    CVC_MAX_RETRIES,
                    detail,
                    delay,
                )
                time.sleep(delay)
        assert last_error is not None
        raise last_error

    def search_html(
        self,
        term: Term,
        modality_subtype: str,
        subject: str,
        page: int = 1,
    ) -> str | None:
        """GET one page of CVC search results. Returns HTML text or None on error."""
        self._throttle()
        params = [
            ("filter[search_type]", "open_search"),
            ("filter[search_all_universities]", "false"),
            ("filter[display_home_school]", "false"),
            ("filter[university_id]", CVC_HOME_UNIVERSITY_ID),
            ("filter[session_names][]", term.label),
            ("filter[delivery_method_subtypes][]", modality_subtype),
            # CVC defaults hide started classes and restrict enrollment eligibility.
            ("filter[start_date]", ""),
            ("filter[show_only_available]", "false"),
            ("filter[oei_phase_2_filter]", "false"),
            ("filter[subject]", subject.lower()),
            ("page", str(page)),
        ]
        url = f"{self.base_url}/search"
        try:
            resp = self._get_with_retries(url, params=params, page=page)
        except httpx.HTTPError as e:
            log.warning("CVC HTTP error (page %d): %s", page, e)
            return None
        if resp.status_code == 429:
            log.warning("CVC rate limited — stopping.")
            return None
        if resp.status_code >= 400:
            log.warning("CVC returned %d for %s", resp.status_code, resp.url)
            return None
        return resp.text


def write_offerings(
    conn: sqlite3.Connection,
    records: Iterable[OfferingRecord],
    term: Term,
    source: str = "cvc",
    *,
    staging: bool = False,
    lookups: OfferingLookups | None = None,
) -> dict[str, int]:
    """Upsert records. `term` must already be in the terms table."""
    counts = _empty_write_counts()
    term_id = db.get_term_id_by_code(conn, term.code)
    if term_id is None:
        raise ValueError(f"Term {term.code} not in DB — call ensure_term first")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if lookups is None:
        lookups = _load_offering_lookups(conn)

    payload: list[tuple[int, int | None, str, str, int, str, str, str, str]] = []
    for rec in records:
        code = cvc_code_for(rec.college_name)
        if not code:
            counts["skipped_unknown_college"] += 1
            log.debug("No ASSIST code for CVC college %r", rec.college_name)
            continue
        institution_id = lookups.institutions_by_code.get(code.strip().upper())
        if institution_id is None:
            counts["skipped_missing_institution"] += 1
            log.debug("No ingested institution row for ASSIST code %s", code)
            continue
        prefix = rec.prefix.strip().upper()
        number = rec.number.strip().upper()
        course_id = lookups.courses_by_key.get((institution_id, prefix, number))
        payload.append(
            (
                institution_id,
                course_id,
                prefix,
                number,
                term_id,
                rec.modality,
                source,
                rec.source_ref,
                fetched_at,
            )
        )
        counts["written"] += 1

    if staging:
        conn.executemany(
            """INSERT INTO cvc_offerings_staging
                 (institution_id, course_id, prefix, number, term_id,
                  modality, source, source_ref, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(institution_id, prefix, number, term_id, source_ref)
               DO UPDATE SET course_id=excluded.course_id,
                             modality=excluded.modality,
                             fetched_at=excluded.fetched_at""",
            payload,
        )
    else:
        db.upsert_class_offerings(conn, payload)
    return counts


def _load_offering_lookups(conn: sqlite3.Connection) -> OfferingLookups:
    institutions = {
        row["code"].strip().upper(): row["id"]
        for row in conn.execute("SELECT id, code FROM institutions WHERE category = 'CCC'")
    }
    courses = {
        (row["institution_id"], row["prefix"].upper(), row["number"].upper()): row["id"]
        for row in conn.execute(
            """SELECT c.id, c.institution_id, c.prefix, c.number
               FROM courses c
               JOIN institutions i ON i.id = c.institution_id
               WHERE i.category = 'CCC'"""
        )
    }
    return OfferingLookups(institutions_by_code=institutions, courses_by_key=courses)


def _prepare_offering_staging(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS temp.cvc_offerings_staging")
    conn.execute(
        """CREATE TEMP TABLE cvc_offerings_staging (
             institution_id INTEGER NOT NULL,
             course_id INTEGER,
             prefix TEXT NOT NULL,
             number TEXT NOT NULL,
             term_id INTEGER NOT NULL,
             modality TEXT NOT NULL,
             source TEXT NOT NULL,
             source_ref TEXT NOT NULL,
             fetched_at TEXT NOT NULL,
             UNIQUE(institution_id, prefix, number, term_id, source_ref)
           )"""
    )


def _publish_staged_offerings(
    conn: sqlite3.Connection,
    *,
    source: str,
    term_ids: list[int],
) -> None:
    """Atomically replace selected terms with their staged offerings."""
    with conn:
        db.clear_offerings(conn, source=source, term_ids=term_ids)
        conn.execute(
            """INSERT INTO class_offerings
                 (institution_id, course_id, prefix, number, term_id,
                  modality, source, source_ref, fetched_at)
               SELECT institution_id, course_id, prefix, number, term_id,
                      modality, source, source_ref, fetched_at
               FROM cvc_offerings_staging"""
        )


def _validate_staged_offerings(
    conn: sqlite3.Connection,
    *,
    source: str,
    term_ids: list[int],
    allow_small_refresh: bool,
) -> None:
    """Reject empty or implausibly small crawls before replacing live data."""
    term_marks = ",".join("?" for _ in term_ids)
    staged_count = conn.execute(
        "SELECT COUNT(*) FROM cvc_offerings_staging WHERE source = ?",
        (source,),
    ).fetchone()[0]
    live_count = conn.execute(
        f"SELECT COUNT(*) FROM class_offerings "
        f"WHERE source = ? AND term_id IN ({term_marks})",
        (source, *term_ids),
    ).fetchone()[0]

    if staged_count == 0:
        raise RuntimeError(
            "CVC refresh produced no offerings; existing offerings were preserved"
        )
    if (
        not allow_small_refresh
        and live_count >= MIN_REFRESH_BASELINE
        and staged_count < live_count * MIN_REFRESH_RATIO
    ):
        raise RuntimeError(
            "CVC refresh produced an implausibly small result "
            f"({staged_count} rows versus {live_count} existing); "
            "existing offerings were preserved. Re-run with "
            "--allow-small-refresh only after verifying CVC manually."
        )


def ensure_term(conn: sqlite3.Connection, term: Term) -> int:
    return db.upsert_term(
        conn, code=term.code, label=term.label, season=term.season, year=term.year
    )


def ccc_subject_prefixes(conn: sqlite3.Connection) -> list[str]:
    """Distinct subject prefixes for CCC courses already in the DB."""
    rows = conn.execute(
        """SELECT DISTINCT UPPER(c.prefix) AS p
           FROM courses c
           JOIN institutions i ON i.id = c.institution_id
           WHERE i.category = 'CCC'
           ORDER BY p"""
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _filter_available_terms(terms: list[Term], fixture_path: Path | None) -> list[Term]:
    if fixture_path:
        return terms
    with CVCClient() as client:
        available_sessions = client.available_session_names()
    if available_sessions is None:
        return terms
    skipped = [term.label for term in terms if term.label not in available_sessions]
    available = [term for term in terms if term.label in available_sessions]
    log.info(
        "CVC currently advertises %d sessions: %s",
        len(available_sessions),
        ", ".join(sorted(available_sessions)),
    )
    if skipped:
        log.info("Skipping unavailable CVC sessions: %s", ", ".join(skipped))
    return available


def _ingest_fixture(
    conn: sqlite3.Connection,
    fixture_path: Path,
    terms: list[Term],
    term_ids: list[int],
    lookups: OfferingLookups,
) -> dict[str, int]:
    _prepare_offering_staging(conn)
    log.info("Loading from fixture %s (treated as online_async)", fixture_path)
    html = fixture_path.read_text(encoding="utf-8", errors="replace")
    totals = _empty_write_counts()
    for term in terms:
        records = parse_search_html(html, term_code=term.code, modality="online_async")
        counts = write_offerings(conn, records, term, staging=True, lookups=lookups)
        _add_write_counts(totals, counts)
        log.info(
            "Term %s (fixture): parsed %d cards, wrote %d",
            term.code,
            len(records),
            counts["written"],
        )
    _publish_staged_offerings(conn, source="cvc", term_ids=term_ids)
    return totals


def _crawl_subject(
    client: CVCClient,
    conn: sqlite3.Connection,
    term: Term,
    modality: str,
    subject: str,
    lookups: OfferingLookups,
) -> tuple[dict[str, int], int, int]:
    totals = _empty_write_counts()
    cards = 0
    pages = 0
    last_page: int | None = None
    for page in range(1, MAX_PAGES_PER_QUERY + 1):
        html = client.search_html(term, modality, subject, page=page)
        if html is None:
            raise RuntimeError(
                "CVC refresh incomplete at "
                f"{term.code}/{modality} subject={subject} page={page}; "
                "existing offerings were preserved"
            )
        card_count = count_cards(html)
        if card_count == 0:
            break
        last_page = max(last_page or 0, advertised_last_page(html) or 0) or None
        records = parse_search_html(html, term_code=term.code, modality=modality)
        skipped = card_count - len(records)
        if skipped:
            log.warning(
                "Skipping %d/%d CVC cards without a parseable offering at %s/%s subject=%s page=%d",
                skipped, card_count, term.code, modality, subject, page,
            )
        counts = write_offerings(conn, records, term, staging=True, lookups=lookups)
        _add_write_counts(totals, counts)
        cards += len(records)
        pages += 1
        log.info(
            "    %s/%s subj=%s p%d: %d cards parsed, %d upserts",
            term.code,
            modality,
            subject,
            page,
            len(records),
            counts["written"],
        )
        if last_page is not None and page >= last_page:
            break
        if not has_next_page(html):
            break
        if page == MAX_PAGES_PER_QUERY:
            raise RuntimeError(
                "CVC refresh hit the pagination safety cap at "
                f"{term.code}/{modality} subject={subject}; "
                "existing offerings were preserved"
            )
    return totals, cards, pages


def _crawl_live_offerings(
    conn: sqlite3.Connection,
    terms: list[Term],
    term_ids: list[int],
    subjects: list[str],
    lookups: OfferingLookups,
    *,
    allow_small_refresh: bool = False,
) -> dict[str, int]:
    _prepare_offering_staging(conn)
    totals = _empty_write_counts()
    log.info(
        "Estimated minimum runtime: %.1f min (pagination extends this)",
        THROTTLE_S * len(MODALITIES) * len(subjects) * len(terms) / 60.0,
    )
    with CVCClient() as client:
        for term in terms:
            term_total = 0
            for modality in MODALITIES:
                for index, subject in enumerate(subjects, start=1):
                    counts, cards, pages = _crawl_subject(
                        client, conn, term, modality, subject, lookups
                    )
                    _add_write_counts(totals, counts)
                    term_total += counts["written"]
                    if cards:
                        log.info(
                            "  %s / %s subj=%-8s [%d/%d] %d cards (%d pages)",
                            term.code,
                            modality,
                            subject,
                            index,
                            len(subjects),
                            cards,
                            pages,
                        )
            distinct_count = conn.execute(
                "SELECT COUNT(*) FROM cvc_offerings_staging WHERE term_id = ?",
                (db.get_term_id_by_code(conn, term.code),),
            ).fetchone()[0]
            log.info(
                "Term %s: %d distinct offerings (%d upserts across overlapping searches)",
                term.code, distinct_count, term_total,
            )
    _validate_staged_offerings(
        conn,
        source="cvc",
        term_ids=term_ids,
        allow_small_refresh=allow_small_refresh,
    )
    _publish_staged_offerings(conn, source="cvc", term_ids=term_ids)
    return totals


def ingest_terms(
    term_codes: list[str],
    db_path: Path = db.DEFAULT_DB_PATH,
    fixture_path: Path | None = None,
    subjects: list[str] | None = None,
    allow_small_refresh: bool = False,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    terms = _filter_available_terms([parse_code(code) for code in term_codes], fixture_path)
    if not terms:
        log.info("None of the requested terms are currently available on CVC")
        return
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        term_ids = [ensure_term(conn, term) for term in terms]
        conn.commit()
        lookups = _load_offering_lookups(conn)
        if fixture_path:
            totals = _ingest_fixture(conn, fixture_path, terms, term_ids, lookups)
        else:
            selected_subjects = subjects if subjects is not None else ccc_subject_prefixes(conn)
            if not selected_subjects:
                log.error("No subjects to search — did you run the ASSIST ingester first?")
                return
            log.info("Searching %d CCC subject prefixes", len(selected_subjects))
            totals = _crawl_live_offerings(
                conn,
                terms,
                term_ids,
                selected_subjects,
                lookups,
                allow_small_refresh=allow_small_refresh,
            )
        log.info("Totals: %s", totals)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--terms", default="", help="Comma-separated term codes, e.g. FA26,SP27")
    p.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    p.add_argument("--fixture", default=None, help="Path to saved CVC search HTML (offline mode)")
    p.add_argument(
        "--subjects",
        default="",
        help="Comma-separated subject prefixes (default: all in DB)",
    )
    p.add_argument(
        "--allow-small-refresh",
        action="store_true",
        help="Publish an unusually small CVC crawl after manually verifying it",
    )
    args = p.parse_args(argv)

    if args.terms:
        codes = [c.strip() for c in args.terms.split(",") if c.strip()]
    else:
        from datetime import date

        from .terms import upcoming_terms
        codes = [t.code for t in upcoming_terms(date.today())]

    subjects = None
    if args.subjects:
        subjects = [s.strip().upper() for s in args.subjects.split(",") if s.strip()]

    ingest_terms(
        codes,
        db_path=Path(args.db),
        fixture_path=Path(args.fixture) if args.fixture else None,
        subjects=subjects,
        allow_small_refresh=args.allow_small_refresh,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
