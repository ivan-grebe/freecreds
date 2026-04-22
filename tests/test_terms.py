"""Tests for term math."""
from __future__ import annotations

from datetime import date

import pytest

from src.terms import current_term, next_term, parse_code, upcoming_terms


def test_current_term_spring():
    t = current_term(date(2026, 3, 15))
    assert t.code == "SP26"
    assert t.label == "Spring 2026"
    assert t.season == "Spring"
    assert t.year == 2026


def test_current_term_summer():
    t = current_term(date(2026, 7, 1))
    assert t.code == "SU26"


def test_current_term_fall():
    t = current_term(date(2026, 9, 15))
    assert t.code == "FA26"
    t2 = current_term(date(2026, 12, 31))
    assert t2.code == "FA26"


def test_next_term_rollover():
    sp = current_term(date(2026, 3, 15))
    assert next_term(sp).code == "SU26"
    assert next_term(next_term(sp)).code == "FA26"
    # Fall rolls to Winter of next year, then Spring
    assert next_term(next_term(next_term(sp))).code == "WI27"
    assert next_term(next_term(next_term(next_term(sp)))).code == "SP27"


def test_upcoming_terms_four_forward():
    ts = upcoming_terms(date(2026, 4, 21), count=4)
    assert [t.code for t in ts] == ["SP26", "SU26", "FA26", "WI27"]


def test_upcoming_terms_from_august_spans_years():
    ts = upcoming_terms(date(2026, 8, 20), count=4)
    assert [t.code for t in ts] == ["FA26", "WI27", "SP27", "SU27"]


def test_parse_code_roundtrip():
    for code in ("SP26", "SU26", "FA26", "WI27"):
        t = parse_code(code)
        assert t.code == code


def test_parse_code_invalid():
    with pytest.raises(ValueError):
        parse_code("XX26")
    with pytest.raises(ValueError):
        parse_code("SP2")
