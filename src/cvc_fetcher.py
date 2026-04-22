"""Fetch online-course offerings from CVC Exchange (search.cvc.edu).

CVC Exchange is the California Virtual Campus course-finder, powered by
Quottly. It aggregates fully-online courses across ~112 CCCs — a direct
match for the async-online filter on our reverse-search results.

## Endpoint — HTML, not JSON

Live discovery (April 2026) confirmed there is **no public JSON API** for
course results. The autocomplete endpoint `/api/search.json` only returns
typeahead suggestions. Real course search is rendered as HTML on
`/search?filter[...]=...&page=N`.

Experimentally (verified April 2026), CVC rejects "show everything"
queries — `filter[subject]` is a **required** field. Without it the
response is a landing page with zero course cards. So we drive the
search one *(term, modality, subject)* triple at a time. Every card
returned is unambiguously that term + that modality; the card's own
prefix+number (parsed from its title) determines where it lands in our
DB, not the subject keyword we searched with.

The subject iteration set is the distinct CCC course prefixes already
in `data/assist.db` — the only ones we need offering data for, since
anything that doesn't articulate somewhere won't surface in reverse
lookups anyway.

Required filters on the search URL:
- `filter[search_type]=open_search`
- `filter[search_all_universities]=false`
- `filter[display_home_school]=false`
- `filter[university_id]=<any home college id>`  (affects pricing display
  only; results are the same aggregate set)
- `filter[session_names][]=<term label>`         (e.g. "Fall 2026")
- `filter[delivery_method_subtypes][]=<subtype>` (online_async / online_sync)
- `filter[subject]=<prefix>`                     (**required** — no subject = no results)
- `page=N`                                        (1-indexed)

## Graceful degradation

If CVC is unreachable or its HTML shape changes, this module logs and
exits without crashing. The reverse-search feature still works; offering
status becomes "unknown" and the link-out is the user's fallback.

## Usage

    python -m src.cvc_fetcher --terms FA26,SP27
    python -m src.cvc_fetcher --terms FA26 --subjects MATH,ENGL    # quick test
    python -m src.cvc_fetcher --fixture tests/fixtures/cvc_response_sample.html --terms FA26

`--fixture` parses a single saved HTML page (offline mode for testing).
It treats every card as async; use the live flow in prod.
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from . import db
from .cvc_college_map import lookup as cvc_code_for
from .terms import Term, parse_code

log = logging.getLogger(__name__)

CVC_BASE_URL = "https://search.cvc.edu"
USER_AGENT = "reverse-assist-search/0.1 (+contact: ivanderson412@gmail.com)"
THROTTLE_S = 0.6
TIMEOUT_S = 30.0
DEFAULT_HOME_UNIVERSITY_ID = 101  # arbitrary; affects tuition display only
MAX_PAGES_PER_QUERY = 200  # safety cap; CVC typically returns ≤ ~40 pages
MODALITY_SUBTYPES = (("online_async", "online_async"), ("online_sync", "online_sync"))
WRITE_COUNT_KEYS = ("written", "skipped_unknown_college", "skipped_missing_institution")


def _empty_write_counts() -> Dict[str, int]:
    return {key: 0 for key in WRITE_COUNT_KEYS}


def _add_write_counts(total: Dict[str, int], counts: Dict[str, int]) -> None:
    for key in WRITE_COUNT_KEYS:
        total[key] += counts.get(key, 0)


@dataclass(frozen=True)
class OfferingRecord:
    """Normalized CVC record. The DB writer consumes these directly."""
    college_name: str
    prefix: str
    number: str
    term_code: str
    modality: str        # "online_async" | "online_sync"
    source_ref: str


# --- HTML parsing ------------------------------------------------------------

# Splits page HTML into per-card chunks. Card containers start with
# `<div class="course border-gray-400 ..."`. Using a sentinel split is more
# robust than trying to balance nested tags with regex.
_CARD_SPLIT = re.compile(r'<div class="course border-gray-400[^"]*"', re.IGNORECASE)

# Within a card, the college name is the first `<div class="font-semibold text-sm">...</div>`.
_COLLEGE_RE = re.compile(
    r'<div class="font-semibold text-sm">\s*([^<]+?)\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
# The course link has class `course-details-link` and text like "MATH45 - Title".
_LINK_RE = re.compile(
    r'<a[^>]*class="course-details-link[^"]*"[^>]*href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE | re.DOTALL,
)
# Title token pattern: prefix is letters, number is digits with optional letter suffix
# (e.g. "MATH10", "ENGL1A", "MATH150A", "BT115"). Greedy on letters so multi-letter
# prefixes like "ENGL" or "PSYC" work.
_TITLE_TOKEN_RE = re.compile(r'^([A-Z][A-Z&/]{0,9})\s*([0-9]+[A-Z]{0,3})\b')


def _unescape(s: str) -> str:
    """Decode the handful of HTML entities we see in search results."""
    return (
        s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )


def parse_search_html(
    html: str,
    *,
    term_code: str,
    modality: str,
) -> List[OfferingRecord]:
    """Extract OfferingRecords from one rendered CVC search page."""
    chunks = _CARD_SPLIT.split(html)
    # First chunk is everything before the first card; skip it.
    out: List[OfferingRecord] = []
    for chunk in chunks[1:]:
        college_m = _COLLEGE_RE.search(chunk)
        link_m = _LINK_RE.search(chunk)
        if not (college_m and link_m):
            continue
        college = _unescape(college_m.group(1))
        href = _unescape(link_m.group(1))
        title_text = _unescape(link_m.group(2))
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


class _HomeCollegeOptionParser(HTMLParser):
    """Pulls the home-college `<select>` options out of a CVC search page.
    Not used by the main ingest path, but exposed so we can regenerate
    cvc_college_map when the CCC roster changes.
    """
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_home_select = False
        self._pending_value: Optional[str] = None
        self._text: List[str] = []
        self.options: List[Tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        d = dict(attrs)
        if tag == "select" and d.get("id") == "filter_university_id":
            self._in_home_select = True
        elif tag == "option" and self._in_home_select:
            self._pending_value = d.get("value")
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._in_home_select = False
        elif tag == "option" and self._pending_value is not None:
            text = "".join(self._text).strip()
            try:
                vid = int(self._pending_value)
            except (TypeError, ValueError):
                vid = None
            if vid is not None and text:
                self.options.append((vid, text))
            self._pending_value = None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._in_home_select and self._pending_value is not None:
            self._text.append(data)


def parse_home_college_options(html: str) -> List[Tuple[int, str]]:
    p = _HomeCollegeOptionParser()
    p.feed(html)
    return p.options


# --- HTTP layer --------------------------------------------------------------

class CVCClient:
    def __init__(
        self,
        base_url: str = CVC_BASE_URL,
        home_university_id: int = DEFAULT_HOME_UNIVERSITY_ID,
    ):
        self.base_url = base_url.rstrip("/")
        self.home_university_id = home_university_id
        self._client = httpx.Client(
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            follow_redirects=True,
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CVCClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request_at
        if gap < THROTTLE_S:
            time.sleep(THROTTLE_S - gap)
        self._last_request_at = time.monotonic()

    def search_html(
        self,
        term: Term,
        modality_subtype: str,
        subject: str,
        page: int = 1,
    ) -> Optional[str]:
        """GET one page of CVC search results. Returns HTML text or None on error."""
        self._throttle()
        params = [
            ("filter[search_type]", "open_search"),
            ("filter[search_all_universities]", "false"),
            ("filter[display_home_school]", "false"),
            ("filter[university_id]", str(self.home_university_id)),
            ("filter[session_names][]", term.label),
            ("filter[delivery_method_subtypes][]", modality_subtype),
            ("filter[subject]", subject),
            ("page", str(page)),
        ]
        url = f"{self.base_url}/search"
        try:
            resp = self._client.get(url, params=params)
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


# --- DB writer ---------------------------------------------------------------

def write_offerings(
    conn: sqlite3.Connection,
    records: Iterable[OfferingRecord],
    term: Term,
    source: str = "cvc",
) -> Dict[str, int]:
    """Upsert records. `term` must already be in the terms table."""
    counts = _empty_write_counts()
    term_id = db.get_term_id_by_code(conn, term.code)
    if term_id is None:
        raise ValueError(f"Term {term.code} not in DB — call ensure_term first")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for rec in records:
        code = cvc_code_for(rec.college_name)
        if not code:
            counts["skipped_unknown_college"] += 1
            log.debug("No ASSIST code for CVC college %r", rec.college_name)
            continue
        inst_row = conn.execute(
            "SELECT id FROM institutions WHERE TRIM(code) = ?", (code,)
        ).fetchone()
        if not inst_row:
            counts["skipped_missing_institution"] += 1
            log.debug("No ingested institution row for ASSIST code %s", code)
            continue
        institution_id = inst_row[0]

        # Best-effort course linking: match on (institution_id, prefix, number).
        # If the CCC has no articulating course ingested yet, course_id stays
        # NULL and the API falls back to (prefix, number) matching.
        course_row = conn.execute(
            """SELECT id FROM courses
               WHERE institution_id = ?
                 AND UPPER(prefix) = ? AND UPPER(number) = ?""",
            (institution_id, rec.prefix, rec.number),
        ).fetchone()
        course_id = course_row[0] if course_row else None

        db.upsert_class_offering(
            conn,
            institution_id=institution_id,
            course_id=course_id,
            prefix=rec.prefix,
            number=rec.number,
            term_id=term_id,
            modality=rec.modality,
            source=source,
            source_ref=rec.source_ref,
            fetched_at=fetched_at,
        )
        counts["written"] += 1
    return counts


def ensure_term(conn: sqlite3.Connection, term: Term) -> int:
    return db.upsert_term(
        conn, code=term.code, label=term.label, season=term.season, year=term.year
    )


def ccc_subject_prefixes(conn: sqlite3.Connection) -> List[str]:
    """Distinct subject prefixes for CCC courses already in the DB.

    This is the iteration set for CVC ingestion. A prefix not present here
    is a course that doesn't articulate anywhere in our data — so we'd
    never surface its offering status, even if CVC has it.
    """
    rows = conn.execute(
        """SELECT DISTINCT UPPER(c.prefix) AS p
           FROM courses c
           JOIN institutions i ON i.id = c.institution_id
           WHERE i.category = 'CCC'
           ORDER BY p"""
    ).fetchall()
    return [r[0] for r in rows if r[0]]


# --- Entry point -------------------------------------------------------------

def ingest_terms(
    term_codes: List[str],
    db_path: Path = db.DEFAULT_DB_PATH,
    fixture_path: Optional[Path] = None,
    subjects: Optional[List[str]] = None,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    terms = [parse_code(c) for c in term_codes]

    conn = db.connect(db_path)
    db.init_db(conn)
    term_ids = [ensure_term(conn, t) for t in terms]
    conn.commit()

    db.clear_offerings(conn, source="cvc", term_ids=term_ids)
    conn.commit()

    if fixture_path:
        log.info("Loading from fixture %s (treated as online_async)", fixture_path)
        html = fixture_path.read_text(encoding="utf-8", errors="replace")
        totals = _empty_write_counts()
        for term in terms:
            records = parse_search_html(html, term_code=term.code, modality="online_async")
            counts = write_offerings(conn, records, term)
            _add_write_counts(totals, counts)
            conn.commit()
            log.info("Term %s (fixture): parsed %d cards, wrote %d",
                     term.code, len(records), counts["written"])
        log.info("Totals: %s", totals)
        conn.close()
        return

    if subjects is None:
        subjects = ccc_subject_prefixes(conn)
        log.info("Harvested %d distinct CCC subject prefixes from DB", len(subjects))
    else:
        log.info("Using supplied subject list (%d prefixes)", len(subjects))
    if not subjects:
        log.error("No subjects to search — did you run the ASSIST ingester first?")
        conn.close()
        return

    totals = _empty_write_counts()
    # Estimate: 0.6s throttle × 2 modalities × N subjects × pages
    log.info("Estimated minimum runtime: %.1f min (pagination extends this)",
             0.6 * 2 * len(subjects) * len(terms) / 60.0)
    with CVCClient() as client:
        for term in terms:
            term_total = 0
            for subtype, modality in MODALITY_SUBTYPES:
                for si, subject in enumerate(subjects, start=1):
                    pages_fetched = 0
                    cards_for_subject = 0
                    for page in range(1, MAX_PAGES_PER_QUERY + 1):
                        html = client.search_html(term, subtype, subject, page=page)
                        if html is None:
                            log.warning(
                                "Abort at %s/%s subject=%s page=%d — partial data written.",
                                term.code, subtype, subject, page,
                            )
                            break
                        if count_cards(html) == 0:
                            break
                        records = parse_search_html(html, term_code=term.code, modality=modality)
                        counts = write_offerings(conn, records, term)
                        _add_write_counts(totals, counts)
                        conn.commit()
                        term_total += counts["written"]
                        cards_for_subject += len(records)
                        pages_fetched += 1
                    if cards_for_subject:
                        log.info(
                            "  %s / %s subj=%-8s [%d/%d] %d cards (%d pages)",
                            term.code, subtype, subject, si, len(subjects),
                            cards_for_subject, pages_fetched,
                        )
            log.info("Term %s: %d offerings written", term.code, term_total)
    log.info("Totals: %s", totals)
    conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--terms", default="", help="Comma-separated term codes, e.g. FA26,SP27")
    p.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    p.add_argument("--fixture", default=None, help="Path to saved CVC search HTML (offline mode)")
    p.add_argument(
        "--subjects",
        default="",
        help="Comma-separated subject prefixes (default: all in DB)",
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
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
