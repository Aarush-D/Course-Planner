"""Helpers for /api/parse-transcript in app.py: extracting the course-table
text out of a raw PDF-transcript text dump, and mapping each matched line's
trailing grade/status token to a completed/failed/withdrawn/in-progress
verdict.

Split out of app.py verbatim (pure text-processing functions with no Flask
dependency, only `planner_engine`) as part of a size-reduction refactor --
see the code-review note that flagged app.py/planner_engine.py for
splitting. Zero behavior change.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

import planner_engine as engine

_TRANSCRIPT_COURSE_HEADER_RE = re.compile(r"^[ \t]*course\b", re.IGNORECASE | re.MULTILINE)


def _extract_transcript_course_text(text: str) -> str:
    """Anchor extraction on the literal "Course" column header instead of
    scanning the whole PDF indiscriminately.

    A real transcript export lists courses in a table under a "Course"
    heading -- often repeated once per term. Segmenting at each occurrence
    and only matching course codes within those segments (rather than the
    full document) keeps stray numbers elsewhere on the page -- student
    ID, page numbers, phone numbers -- from ever reaching the course-code
    matcher in the first place, instead of relying on the matcher to
    reject them after the fact.

    Anchored on "Course" at the START of a line specifically, not just
    the word appearing anywhere -- a course's own title can legitimately
    contain the word "course" (e.g. "Intro to Course Design"), and that
    must not be mistaken for a new header and silently cut off whatever
    real course code preceded it on an earlier line.

    Falls back to the full text when "Course" never appears anywhere, so
    an unusually-formatted document still gets best-effort matching
    rather than silently returning nothing.
    """
    matches = list(_TRANSCRIPT_COURSE_HEADER_RE.finditer(text))
    if not matches:
        return text
    segments = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segments.append(text[start:end])
    return "\n".join(segments)


# Grade/status tokens PSU's real transcript export prints in the trailing
# "Grade" column, bucketed into the four outcomes callers actually need to
# treat differently. A letter grade in the A/B/C/D range (with +/-), plus
# CR ("credit"), TR (transfer credit), S ("satisfactory" in a
# satisfactory/unsatisfactory course), P (pass in a pass/fail course), and
# AU (audit) all mean the course is DONE and should count -- only F, a
# withdrawal, or a still-in-progress course should not.
#
# Penn State's own D grades are still a passing, completed grade (the
# course earns credit and counts toward the degree) even though some
# individual courses require a HIGHER minimum grade to satisfy a specific
# prerequisite or major requirement -- that's a separate, per-course
# concept (see the NOTE on minimum-grade requirements below) from whether
# the course itself is complete.
_TRANSCRIPT_FAILING_GRADES = {"F", "NP", "U"}
_TRANSCRIPT_WITHDRAWN_GRADES = {"W", "WD"}
_TRANSCRIPT_IN_PROGRESS_GRADES = {"IP", "PR", "NG"}

# Matches one recognized grade/status token as a whole word (never as part
# of a longer alphanumeric run -- "202W" or "100B" are course-code suffixes,
# not a "W" or "B" grade sitting on their own).
_TRANSCRIPT_GRADE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|F|WD|W|IP|PR|NG|AU|CR|NC|TR|S|U|P|NP)(?![A-Za-z0-9])"
)

# NOTE on minimum-grade requirements: as of this fix, nothing in this
# codebase's data model (Course in Courseplanner.py, the degree-plan JSON
# schema, or the prereq/exclusion checks in planner_engine.py) represents a
# per-course "grade of C or better required" style rule -- prereqs are
# tracked purely as course codes, never with an attached minimum grade.
# That concept simply doesn't exist yet to thread through here. The raw
# `grade` token is still returned per matched course below (not just the
# derived status) precisely so that whenever such a requirement is added
# to the data model, this endpoint won't need to be revisited to compare
# against it.


def _transcript_course_status(grade_token: Optional[str]) -> str:
    """Map one parsed grade/status token to completed/failed/withdrawn/in-progress.

    Defaults to "completed" for a missing or unrecognized token -- matching
    the endpoint's pre-existing behavior of treating a plain regex/text
    match as done, so a row this can't find a real grade token on (an
    unusual transcript layout) degrades gracefully instead of the whole
    course silently vanishing.
    """
    if not grade_token:
        return "completed"
    token = grade_token.upper()
    if token in _TRANSCRIPT_FAILING_GRADES:
        return "failed"
    if token in _TRANSCRIPT_WITHDRAWN_GRADES:
        return "withdrawn"
    if token in _TRANSCRIPT_IN_PROGRESS_GRADES:
        return "in-progress"
    return "completed"


def _parse_transcript_course_statuses(course_text: str, catalog: Dict[str, "engine.Course"]) -> Dict[str, Dict[str, Any]]:
    """Figure out each matched course's grade/status by re-running the same
    match_courses_in_text() matcher one transcript LINE at a time, then
    pairing whatever that line matched with the grade/status token found on
    that same line.

    Done line-by-line (rather than once over the whole course_text, the way
    the caller's own top-level match does) specifically so a grade token can
    be tied to the one course it actually belongs to -- a real transcript
    row is "Course   Title   Credits   Grade" and the grade column is the
    LAST thing on the line, so scoping the token search to a single course's
    own row is what keeps one course's grade from ever being attributed to
    another course listed elsewhere in the document.

    A code appearing on more than one line (a retaken course) keeps
    whichever line comes LAST in the document -- transcripts list terms in
    chronological order, and it's the later grade that actually reflects
    where the student ended up.

    Known limitation: if a course's title itself contains a standalone
    token that looks like a grade (rare, but e.g. a lone "U" or "S") AND
    that same row has no real trailing grade after it (a blank/unusual
    grade column), the title token can be mistaken for the real grade.
    Every real title seen in this codebase's own tests doesn't trigger
    this, but it's a real edge a genuinely unusual PDF export could hit.
    """
    statuses: Dict[str, Dict[str, Any]] = {}
    for line in course_text.splitlines():
        if not line.strip():
            continue
        line_matched, _ = engine.match_courses_in_text(line, catalog)
        if not line_matched:
            continue
        grade_matches = list(_TRANSCRIPT_GRADE_TOKEN_RE.finditer(line.upper()))
        grade_token = grade_matches[-1].group(1) if grade_matches else None
        status = _transcript_course_status(grade_token)
        for m in line_matched:
            statuses[m["code"]] = {"grade": grade_token, "status": status}
    return statuses
