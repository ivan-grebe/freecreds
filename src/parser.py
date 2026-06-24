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

Series-typed receiving articulations represent an AND/OR bundle on the
*receiving* side (e.g. UCR's BIOL 5A + BIOL 5LA treated as a single
transfer unit). We fan them out: one ParsedArticulation per course in
the series, sharing the same sending tree. That way a search for any
member course surfaces the sending CC. The nuance that taking the
sending course also yields credit for the other series members is
not surfaced in the UI today.
"""
from __future__ import annotations

import json
from typing import Tuple
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
    # For AllMajors-sourced payloads, the specific major (e.g. "Biology, BS")
    # that this articulation belongs to, derived from the templateAssets
    # mapping in the normalizer. None for AllDepartments entries or when
    # the cell lookup couldn't resolve a major.
    source_major: Optional[str] = None
    # Other receiving courses bundled with this one on the university side
    # (populated when the articulation came from a Series fan-out). Taking
    # any sending course in this articulation also yields credit for these
    # siblings. Empty for ordinary Course-type articulations.
    receiving_siblings: List[CourseRef] = field(default_factory=list)


def _course_ref(obj: Dict[str, Any]) -> Optional[CourseRef]:
    """Build a CourseRef from a course dict. Returns None on missing key
    fields rather than raising — malformed entries are skipped.
    """
    cpid = obj.get("courseIdentifierParentId")
    if cpid is None:
        return None
    prefix = (obj.get("prefix") or "").strip()
    number = (obj.get("courseNumber") or "").strip()
    if not prefix or not number:
        return None
    end = obj.get("end") or ""
    is_terminated = bool(end and end.strip())
    return CourseRef(
        course_identifier_parent_id=int(cpid),
        prefix=prefix,
        number=number,
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
        malformed_course = False
        for c in grp.get("items") or []:
            if c.get("type") != "Course":
                continue
            ref = _course_ref(c)
            if ref is None:
                malformed_course = True
                continue
            courses.append(ref)
        if malformed_course or not courses:
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
            atype = a.get("type")
            if atype == "Course":
                yield from _yield_course_articulation(a)
            elif atype == "Series":
                yield from _yield_series_articulation(a)
            # Other receiving types (if any) are still skipped.


def _yield_course_articulation(a: Dict[str, Any]) -> Iterator[ParsedArticulation]:
    course_obj = a.get("course") or {}
    recv = _course_ref(course_obj)
    if recv is None:
        return

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
        source_major=a.get("_source_major"),
    )


def _yield_series_articulation(a: Dict[str, Any]) -> Iterator[ParsedArticulation]:
    """Fan out a Series (receiving-side AND/OR bundle) into one
    ParsedArticulation per member course. Cross-listed aliases are
    attributed to the specific series course they alias, via the
    `seriesCourseId` GUID on each visibleCrossListedCourses entry.
    """
    series = a.get("series") or {}
    series_courses = series.get("courses") or []
    if not series_courses:
        return

    xl_by_series_id: Dict[str, List[CourseRef]] = {}
    for xl in a.get("visibleCrossListedCourses") or []:
        sid = xl.get("seriesCourseId")
        ref = _course_ref(xl)
        if sid is None or ref is None:
            continue
        xl_by_series_id.setdefault(sid, []).append(ref)

    sa = a.get("sendingArticulation") or {}
    groups = _parse_sending_groups(sa)
    no_art = _no_art_label(sa) if not groups else None
    source_major = a.get("_source_major")

    # Pre-resolve all series members; we need to know siblings per member.
    resolved: List[Tuple[Dict[str, Any], CourseRef]] = []
    for c in series_courses:
        recv = _course_ref(c)
        if recv is not None:
            resolved.append((c, recv))

    for i, (c, recv) in enumerate(resolved):
        cross_listed = xl_by_series_id.get(c.get("id") or "", [])
        siblings = [other for j, (_, other) in enumerate(resolved) if j != i]
        yield ParsedArticulation(
            receiving_course=recv,
            cross_listed_receiving=cross_listed,
            sending_groups=groups,
            no_articulation_reason=no_art,
            raw=a,
            source_major=source_major,
            receiving_siblings=siblings,
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
