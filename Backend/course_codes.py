"""Course-code primitives shared by planner_engine, course_matching and
math_placement: the course-code regex, canonical normalization, and the
spoken-name alias table.

A deliberately tiny leaf module with no project imports, so the two
helper modules that used to import these names straight from
planner_engine (which in turn imports them mid-file) can be imported
standalone -- `python -c "import course_matching"` used to raise a
circular ImportError because course_matching pulled norm_code from a
half-initialized planner_engine. planner_engine re-exports everything
here, so `engine.norm_code`, `engine.COURSE_ALIASES`, etc. keep working.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Course number is capped at a 2-3 digit minimum deliberately, even though
# ~144 real PSU courses (PSU 1, PHIL 1-9, SOC 1, AERSP 1, mostly First-Year
# Seminars) have a single-digit number this can never match. Tried widening
# to \d{1,3} and reverted it: on a real transcript, a course's DESCRIPTION
# text and its own credit-hours count sit right next to each other on the
# same flattened line ("...Ren to Modern Art 3.000..."), and a 1-digit
# minimum lets an ordinary description word immediately followed by that
# credit count masquerade as a course code -- confirmed live: "...Modern
# Art 3.000" matched the real, unrelated catalog course "ART 3" and would
# have silently credited a course the student never took. A missed match
# (shown as an unmatched hint the student can add by hand) is recoverable;
# a phantom credited course is silent data corruption, so the safer
# 2-3-digit floor stays even at the cost of these single-digit courses.
COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6})\s*-?\s*(\d{2,3}[A-Z]{0,2})\b")

COURSE_ALIASES_PATH = os.path.join(BASE_DIR, "data", "course_aliases.json")


def _load_course_aliases() -> Dict[str, str]:
    """Common spoken names for courses students type into chat, e.g.
    'CALC 1' -> 'MATH 140'. Lives in data/course_aliases.json (same
    pattern as degree plans/catalogs) so adding an alias is a data edit,
    not a code change + redeploy. The JSON is the single source of truth
    -- there is deliberately no hardcoded copy in code to drift from it
    (one used to shadow this load entirely, and the two had already
    diverged: the cross-listed "CMPEN 315" -> "CMPSC 315" entry only
    existed in code)."""
    with open(COURSE_ALIASES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{COURSE_ALIASES_PATH} must contain a JSON object")
    return {str(k).strip().upper(): str(v).strip().upper() for k, v in data.items()}


# A plain dict object on purpose -- tests (and any runtime tweak) mutate
# it in place via engine.COURSE_ALIASES, and course_matching reads the
# same object, so the mutation is visible everywhere.
COURSE_ALIASES: Dict[str, str] = _load_course_aliases()


def norm_code(code: str) -> str:
    """Canonical course code: uppercase, single space, no leading zeros (ENGL 015 -> ENGL 15)."""
    s = re.sub(r"\s+", " ", (code or "").strip().upper().replace("\xa0", " "))
    m = re.match(r"^([A-Z]+)\s*0*(\d+[A-Z]*)$", s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return s
