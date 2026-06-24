"""Parser tests.

Most tests run against hand-crafted payloads so each articulation shape is
tested in isolation. One test runs the parser over the real fixture to
guard against regressions on shape changes in the live API.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.parser import (
    build_reverse_rows,
    iter_parsed_articulations,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _agreement(articulations_list):
    return {
        "articulations": json.dumps(
            [{"name": "Test Dept", "articulations": articulations_list}]
        )
    }


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


def test_malformed_course_refs_are_skipped_safely():
    payload = _agreement([
        _recv(40, "", "100", _sa_single_course(41, "MATH", "1")),
        _recv(42, "MATH", "100", _sa_single_course(43, "", "1")),
        _recv(44, "MATH", "101", _sa_and_bundle([
            (45, "MATH", "2"),
            (46, "MATH", ""),
        ])),
    ])
    parsed = list(iter_parsed_articulations(payload))

    assert len(parsed) == 2
    assert [p.receiving_course.course_identifier_parent_id for p in parsed] == [42, 44]
    assert parsed[0].sending_groups == []
    assert parsed[1].sending_groups == []


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


def test_series_fans_out_to_each_member_course():
    """A Series receiving side (UCR BIOL 5A + BIOL 5LA ← BIOL 150)
    fans out into one ParsedArticulation per member course, all sharing
    the same sending tree."""
    series_courses = [
        dict(_course(71, "BIOL", "5A"), id="guid-a"),
        dict(_course(72, "BIOL", "5LA"), id="guid-la"),
    ]
    series_art = {
        "type": "Series",
        "series": {"conjunction": "And", "courses": series_courses},
        "visibleCrossListedCourses": [],
        "sendingArticulation": _sa_single_course(99, "BIOL", "150"),
    }
    payload = _agreement([series_art])
    parsed = list(iter_parsed_articulations(payload))
    assert len(parsed) == 2
    recv_parents = {p.receiving_course.course_identifier_parent_id for p in parsed}
    assert recv_parents == {71, 72}
    # Both share the same sending side (one CC course satisfies the bundle).
    for p in parsed:
        assert len(p.sending_groups) == 1
        assert [c.course_identifier_parent_id for c in p.sending_groups[0].courses] == [99]
        rows = build_reverse_rows(p)
        assert len(rows) == 1 and rows[0].is_standalone


def test_series_cross_listed_attributed_by_series_course_id():
    """visibleCrossListedCourses on a Series carry seriesCourseId pointing
    to which series member they alias. Each alias must land on its own
    ParsedArticulation, not all of them."""
    series_courses = [
        dict(_course(81, "HIST", "170A"), id="guid-a"),
        dict(_course(82, "AFAM", "190"), id="guid-b"),
    ]
    xl_a = dict(_course(83, "HIST", "170A-alt"), seriesCourseId="guid-a")
    xl_b = dict(_course(84, "CHIC", "190"), seriesCourseId="guid-b")
    series_art = {
        "type": "Series",
        "series": {"conjunction": "And", "courses": series_courses},
        "visibleCrossListedCourses": [xl_a, xl_b],
        "sendingArticulation": _sa_single_course(99, "HIST", "17"),
    }
    payload = _agreement([series_art])
    parsed = list(iter_parsed_articulations(payload))
    by_recv = {p.receiving_course.course_identifier_parent_id: p for p in parsed}
    assert len(by_recv[81].cross_listed_receiving) == 1
    assert by_recv[81].cross_listed_receiving[0].course_identifier_parent_id == 83
    assert len(by_recv[82].cross_listed_receiving) == 1
    assert by_recv[82].cross_listed_receiving[0].course_identifier_parent_id == 84


def test_synthetic_fixture_parses_and_has_mix():
    path = FIXTURES / "allDepartments_sample.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    parsed = list(iter_parsed_articulations(payload))
    # The fixture covers a single, a no-articulation result, and an AND bundle.
    assert len(parsed) == 3
    with_sending = [p for p in parsed if p.sending_groups and not p.no_articulation_reason]
    no_art = [p for p in parsed if p.no_articulation_reason]
    and_bundles = [
        p for p in with_sending
        if any(g.conjunction == "And" and len(g.courses) > 1 for g in p.sending_groups)
    ]
    assert with_sending, "expected some parsed articulations to have sending groups"
    assert no_art, "expected some no-articulation rows"
    assert and_bundles, "expected at least one AND bundle in fixture"
