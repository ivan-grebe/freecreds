"""Static map: ASSIST code (trimmed) → CCC's public class-schedule URL.

Applied once to `institutions.schedule_url` at server startup (if the column
is NULL for that row). See `apply_schedule_urls()` below.

**Population status: partial.** The URLs below were verified during the
initial feasibility research. Remaining CCCs have `None` — the frontend
simply won't render a "check schedule" link for those. Filling out the
rest is an expected follow-up task (~2 hours of manual research, one URL
per college). To add a URL, look up the CCC's "Class Schedule" or
"Search Classes" public page (no login) and paste it below.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, Optional

# Keys are ASSIST codes with whitespace trimmed. Values are either a direct
# URL to the college's class-schedule search page, or None if not yet
# researched. Do not use the bare homepage as a fallback — prefer leaving
# the entry None so the UI omits the link cleanly.
SCHEDULE_URLS: Dict[str, Optional[str]] = {
    # --- Verified in research ---
    "SMCC": "https://www.smc.edu/academics/classes/",
    "DAC": "https://www.deanza.edu/schedule/",
    "PASADENA": "https://findclasses.pasadena.edu/",
    "MTSAC": "https://prod8s.mtsac.edu/prod/pw_sigsched.p_Search",
    # --- Remaining 112 CCCs: populate below as needed ---
    # (Intentionally omitted — see module docstring.)
}


def apply_schedule_urls(conn: sqlite3.Connection) -> int:
    """Populate `institutions.schedule_url` for every entry in SCHEDULE_URLS
    whose current value is NULL. Returns the number of rows updated.

    Intentionally non-destructive: never overwrites a user-set URL, and
    never clears a URL for a CCC that's been removed from this map.
    """
    n = 0
    for code, url in SCHEDULE_URLS.items():
        if url is None:
            continue
        cur = conn.execute(
            """UPDATE institutions
               SET schedule_url = ?
               WHERE code = ? AND schedule_url IS NULL""",
            (url, code),
        )
        n += cur.rowcount
    conn.commit()
    return n
