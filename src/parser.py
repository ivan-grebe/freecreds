"""Parse an ASSIST AllDepartments agreement payload into normalized records.

The ASSIST API returns `articulations` as a stringified JSON blob containing
a list of {name, articulations[]} department sections. Each inner articulation
has a receiving-side entity (Course or Series) and a sendingArticulation tree
describing what satisfies it.

The sending tree semantics:
  sendingArticulation.items[]         → CourseGroup list (implicit OR between
                                         adjacent groups unless overridden by
                                         courseGroupConjunctions)
  courseGroup.items[]                 → Course list within the group
  courseGroup.courseConjunction       → "And" | "Or" (how to combine the
                                         courses inside this group)

A group of 1 course → standalone equivalent (single course satisfies).
A group of N courses with "And" → bundle (all N required together).
A group of N courses with "Or" → flatten; each course is a standalone option.

Series-typed receiving articulations are skipped in MVP (see PLAN.md).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


@dataclass
class CourseRef:
    course_identifier_parent_id: int
    prefix: str
    number: str
    title: str
    min_units: Optional[float]
    max_units: Optional[float]
    is_terminated: bool = False


@dataclass
class SendingGroup:
    conjunction: str  # 'And' | 'Or' | 'Single'
    courses: List[CourseRef]


@dataclass
class ParsedArticulation:
    receiving_course: CourseRef
    cross_listed_receiving: List[CourseRef] = field(default_factory=list)
    sending_groups: List[SendingGroup] = field(default_factory=list)
    no_articulation_reason: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def _course_ref(obj: Dict[str, Any]) -> Optional[CourseRef]:
    """Build a CourseRef from a course dict. Returns None on missing key
    fields rather than raising — malformed entries are skipped.
    """
    cpid = obj.get("courseIdentifierParentId")
    if cpid is None:
        return None
    end = obj.get("end") or ""
    is_terminated = bool(end and end.strip())
    return CourseRef(
        course_identifier_parent_id=int(cpid),
        prefix=(obj.get("prefix") or "").strip(),
        number=(obj.get("courseNumber") or "").strip(),
        title=obj.get("courseTitle") or "",
        min_units=obj.get("minUnits"),
        max_units=obj.get("maxUnits"),
        is_terminated=is_terminated,
    )


def _parse_sending_groups(sa: Dict[str, Any]) -> List[SendingGroup]:
    groups: List[SendingGroup] = []
    for grp in sa.get("items") or []:
        conjunction = grp.get("courseConjunction") or "Single"
        courses: List[CourseRef] = []
        for c in grp.get("items") or []:
            if c.get("type") != "Course":
                continue
            ref = _course_ref(c)
            if ref is not None:
                courses.append(ref)
        if not courses:
            continue
        if len(courses) == 1:
            conjunction = "Single"
        groups.append(SendingGroup(conjunction=conjunction, courses=courses))
    return groups


def _no_art_label(sa: Dict[str, Any]) -> Optional[str]:
    nar = sa.get("noArticulationReason")
    if not nar:
        return None
    if isinstance(nar, dict):
        return nar.get("label") or nar.get("description") or "No articulation"
    return str(nar)


def iter_parsed_articulations(agreement_payload: Dict[str, Any]) -> Iterator[ParsedArticulation]:
    """Yield one ParsedArticulation per Course-type receiving articulation.

    Accepts the raw dict returned by AssistClient.get_agreement() (the
    `result` field already unwrapped).
    """
    raw_arts = agreement_payload.get("articulations")
    if isinstance(raw_arts, str):
        arts = json.loads(raw_arts)
    elif isinstance(raw_arts, list):
        arts = raw_arts
    else:
        return

    for dept in arts:
        for a in dept.get("articulations") or []:
            if a.get("type") != "Course":
                # Series / other types deferred past MVP
                continue
            course_obj = a.get("course") or {}
            recv = _course_ref(course_obj)
            if recv is None:
                continue

            cross_listed: List[CourseRef] = []
            for xl in a.get("visibleCrossListedCourses") or []:
                r = _course_ref(xl)
                if r is not None:
                    cross_listed.append(r)

            sa = a.get("sendingArticulation") or {}
            groups = _parse_sending_groups(sa)
            no_art = _no_art_label(sa) if not groups else None

            yield ParsedArticulation(
                receiving_course=recv,
                cross_listed_receiving=cross_listed,
                sending_groups=groups,
                no_articulation_reason=no_art,
                raw=a,
            )


# --- Reverse-index derivation ---

@dataclass
class ReverseRow:
    receiving_course_parent_id: int
    sending_course_parent_id: int
    is_standalone: bool
    companion_parent_ids: List[int]


def build_reverse_rows(parsed: ParsedArticulation) -> List[ReverseRow]:
    """Given a parsed articulation, emit reverse-index rows for every
    sending course. Each row references courses by stable
    courseIdentifierParentId; callers resolve to DB ids.

    Rules:
      - group conjunction "Single" or "Or" → each course is standalone
      - group conjunction "And" → each course is bundled; companions list
        the other courses in the same group
    """
    rows: List[ReverseRow] = []
    recv_id = parsed.receiving_course.course_identifier_parent_id
    for grp in parsed.sending_groups:
        ids = [c.course_identifier_parent_id for c in grp.courses]
        if grp.conjunction == "And" and len(ids) > 1:
            for i, cid in enumerate(ids):
                companions = [x for j, x in enumerate(ids) if j != i]
                rows.append(
                    ReverseRow(
                        receiving_course_parent_id=recv_id,
                        sending_course_parent_id=cid,
                        is_standalone=False,
                        companion_parent_ids=companions,
                    )
                )
        else:
            for cid in ids:
                rows.append(
                    ReverseRow(
                        receiving_course_parent_id=recv_id,
                        sending_course_parent_id=cid,
                        is_standalone=True,
                        companion_parent_ids=[],
                    )
                )
    return rows
