"""Parser tests.

Most tests run against hand-crafted payloads so each articulation shape is
tested in isolation. One test runs the parser over the real fixture to
guard against regressions on shape changes in the live API.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.parser import (
    build_reverse_rows,
    iter_parsed_articulations,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _agreement(articulations_list):
    return {"articulations": json.dumps([{"name": "Test Dept", "articulations": articulations_list}])}


def _course(cpid, prefix, number, title="Course"):
    return {
        "courseIdentifierParentId": cpid,
        "prefix": prefix,
        "courseNumber": number,
        "courseTitle": title,
        "minUnits": 3.0,
        "maxUnits": 3.0,
        "end": "",
        "type": "Course",
    }


def _recv(cpid, prefix, number, sending, cross_listed=None):
    return {
        "type": "Course",
        "course": _course(cpid, prefix, number),
        "visibleCrossListedCourses": cross_listed or [],
        "sendingArticulation": sending,
    }


def _sa_single_course(cpid, prefix, number):
    return {
        "noArticulationReason": None,
        "items": [
            {
                "courseConjunction": "And",
                "items": [_course(cpid, prefix, number)],
                "type": "CourseGroup",
            }
        ],
    }


def _sa_and_bundle(courses):
    return {
        "noArticulationReason": None,
        "items": [
            {
                "courseConjunction": "And",
                "items": [_course(*c) for c in courses],
                "type": "CourseGroup",
            }
        ],
    }


def _sa_or_alternatives(courses):
    """Top-level OR: multiple groups, each with a single course."""
    return {
        "noArticulationReason": None,
        "items": [
            {
                "courseConjunction": "And",  # single course per group → treated Single
                "items": [_course(*c)],
                "type": "CourseGroup",
            }
            for c in courses
        ],
    }


def _sa_no_art():
    return {
        "noArticulationReason": {"label": "No Course Articulated"},
        "items": [],
    }


def test_simple_one_to_one():
    payload = _agreement([_recv(1, "MATH", "100", _sa_single_course(11, "MATH", "10"))])
    parsed = list(iter_parsed_articulations(payload))
    assert len(parsed) == 1
    p = parsed[0]
    assert p.receiving_course.prefix == "MATH"
    assert len(p.sending_groups) == 1
    assert p.sending_groups[0].conjunction == "Single"
    assert len(p.sending_groups[0].courses) == 1
    assert p.sending_groups[0].courses[0].course_identifier_parent_id == 11

    rows = build_reverse_rows(p)
    assert len(rows) == 1
    assert rows[0].is_standalone
    assert rows[0].companion_parent_ids == []


def test_or_alternatives_flatten_to_standalone():
    payload = _agreement([_recv(2, "BIO", "200",
        _sa_or_alternatives([(21, "BIO", "20"), (22, "BIO", "21")])
    )])
    parsed = list(iter_parsed_articulations(payload))
    rows = build_reverse_rows(parsed[0])
    assert len(rows) == 2
    assert all(r.is_standalone for r in rows)


def test_and_bundle_produces_companions():
    payload = _agreement([_recv(3, "ANTH", "1",
        _sa_and_bundle([(31, "ANTH", "102"), (32, "SOC", "150")])
    )])
    parsed = list(iter_parsed_articulations(payload))
    p = parsed[0]
    assert len(p.sending_groups) == 1
    assert p.sending_groups[0].conjunction == "And"
    assert len(p.sending_groups[0].courses) == 2

    rows = build_reverse_rows(p)
    assert len(rows) == 2
    by_id = {r.sending_course_parent_id: r for r in rows}
    assert not by_id[31].is_standalone
    assert by_id[31].companion_parent_ids == [32]
    assert not by_id[32].is_standalone
    assert by_id[32].companion_parent_ids == [31]


def test_no_articulation_reason_captured():
    payload = _agreement([_recv(4, "PHIL", "100", _sa_no_art())])
    parsed = list(iter_parsed_articulations(payload))
    assert len(parsed) == 1
    p = parsed[0]
    assert p.no_articulation_reason == "No Course Articulated"
    assert p.sending_groups == []
    assert build_reverse_rows(p) == []


def test_cross_listed_receiving_captured():
    xl = _course(5, "CHIC", "101", "Intro Ethnic Studies")
    payload = _agreement([_recv(6, "AFAM", "101",
        _sa_single_course(60, "AFAM", "9"),
        cross_listed=[xl]
    )])
    parsed = list(iter_parsed_articulations(payload))
    p = parsed[0]
    assert len(p.cross_listed_receiving) == 1
    assert p.cross_listed_receiving[0].prefix == "CHIC"


def test_series_type_skipped_in_mvp():
    series_art = {
        "type": "Series",
        "series": {"conjunction": "And", "courses": [_course(71, "HIST", "170A")]},
        "visibleCrossListedCourses": [],
        "sendingArticulation": _sa_single_course(72, "HIST", "17A"),
    }
    payload = _agreement([series_art])
    parsed = list(iter_parsed_articulations(payload))
    assert parsed == []


def test_real_fixture_parses_and_has_mix():
    path = FIXTURES / "allDepartments_sample.json"
    if not path.exists():
        pytest.skip("fixture missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    parsed = list(iter_parsed_articulations(payload))
    # There should be many Course-type articulations
    assert len(parsed) > 100
    # At least one no-articulation, at least one sending, and at least one AND bundle
    with_sending = [p for p in parsed if p.sending_groups and not p.no_articulation_reason]
    no_art = [p for p in parsed if p.no_articulation_reason]
    and_bundles = [
        p for p in with_sending
        if any(g.conjunction == "And" and len(g.courses) > 1 for g in p.sending_groups)
    ]
    assert with_sending, "expected some parsed articulations to have sending groups"
    assert no_art, "expected some no-articulation rows"
    assert and_bundles, "expected at least one AND bundle in fixture"
