"""Term math and canonical codes.

Canonical code format: SEASON + YY (e.g. "FA26" = Fall 2026).

CCC-term conventions:
- Semester colleges: Fall, Spring, (sometimes Winter intersession), Summer
- Quarter colleges: Fall, Winter, Spring, Summer

We emit a single canonical sequence (the union). The 4-term dropdown shown
in the UI is based on the current calendar date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

SEASONS = ("SP", "SU", "FA", "WI")
SEASON_LABELS = {"SP": "Spring", "SU": "Summer", "FA": "Fall", "WI": "Winter"}


@dataclass(frozen=True)
class Term:
    code: str       # "FA26"
    label: str      # "Fall 2026"
    season: str     # "Fall"
    year: int       # 2026

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "season": self.season, "year": self.year}


def _make(season_abbr: str, year: int) -> Term:
    season = SEASON_LABELS[season_abbr]
    return Term(
        code=f"{season_abbr}{year % 100:02d}",
        label=f"{season} {year}",
        season=season,
        year=year,
    )


def current_term(today: date) -> Term:
    """Return the term currently considered 'active' for planning purposes.

    Rough US academic calendar heuristic:
      Jan-May  → Spring
      Jun-Jul  → Summer
      Aug-Dec  → Fall
    """
    m = today.month
    if m <= 5:
        return _make("SP", today.year)
    if m <= 7:
        return _make("SU", today.year)
    return _make("FA", today.year)


def next_term(t: Term) -> Term:
    """Return the term immediately following `t` in a generic academic sequence:
    Spring → Summer → Fall → Winter → (next year) Spring.
    Winter is included so quarter colleges and CCCs that run a real Winter
    intersession are represented in the upcoming-terms dropdown.
    """
    if t.season == "Spring":
        return _make("SU", t.year)
    if t.season == "Summer":
        return _make("FA", t.year)
    if t.season == "Fall":
        return _make("WI", t.year + 1)
    # Winter → Spring of the same year
    return _make("SP", t.year)


def upcoming_terms(today: date, count: int = 3) -> list[Term]:
    out: list[Term] = []
    t = current_term(today)
    for _ in range(count):
        out.append(t)
        t = next_term(t)
    return out


def academic_year_label(year_id: int) -> str:
    """Map an ASSIST academic year ID to its human label.

    Calibrated against observed data: id 74 = 2023-2024, 75 = 2024-2025,
    76 = 2025-2026. So the start year is `1949 + year_id`.

    This is a hardcoded offset — if ASSIST ever resets its year ID
    numbering, this function needs recalibration. Cheaper than storing
    the label per-row, since the payload already carries a label we
    could scrape, but the offset has held since at least 2019.
    """
    start = 1949 + int(year_id)
    return f"{start}-{start + 1}"


def parse_code(code: str) -> Term:
    """Parse a canonical code like 'FA26' back into a Term. Assumes 20xx."""
    code = code.strip().upper()
    if len(code) != 4 or code[:2] not in SEASON_LABELS:
        raise ValueError(f"Invalid term code: {code!r}")
    season_abbr = code[:2]
    try:
        yy = int(code[2:])
    except ValueError as e:
        raise ValueError(f"Invalid term code: {code!r}") from e
    return _make(season_abbr, 2000 + yy)
