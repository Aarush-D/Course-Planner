"""Real PSU ALEKS math-placement ladder: which developmental/placement math
courses a student's completed courses (or a stated ALEKS score / high-school
calculus) already prove unnecessary.

Split out of planner_engine.py verbatim as part of a size-reduction
refactor -- see the code-review note that flagged app.py/planner_engine.py
for splitting. Zero behavior change.

`norm_code` is imported from planner_engine itself (rather than
duplicated) -- imported here at module level rather than inline is safe
because planner_engine.py only ever imports this module AFTER norm_code is
already defined in its own source (this module is never imported by
anything other than planner_engine.py), so there's no real import-order
hazard despite the two modules referencing each other.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from planner_engine import norm_code

# Verified against bulletins.psu.edu's "Mathematics Placement" page (the
# official ALEKS-score-to-course chart) and each course's own catalog
# description — not guessed. Two distinct facts drive this:
#
# 1. MATH 3 and MATH 4 are never degree-applicable at all: both state in
#    their own bulletin description that they "may not be used to satisfy
#    the basic minimum requirements for graduation in any baccalaureate
#    degree program." No student should ever be required to take them for a
#    B.S./B.A., regardless of anyone's placement — they're satisfied
#    unconditionally below.
# 2. The rest of the developmental/placement ladder (MATH 21 -> 22/26/41 ->
#    110 -> 140) is real coursework, but a student who has already completed
#    something HIGHER on the ladder (an AP credit, a transfer course, a
#    transcript upload) obviously doesn't also owe a lower rung just because
#    some major's plan happens to list it as a stepping stone toward a
#    different course's real prerequisite.
NON_DEGREE_APPLICABLE_MATH: Set[str] = {"MATH 3", "MATH 4"}

# Tier N proves every tier below it. MATH 41 combines the MATH 22 + MATH 26
# material into one course (the bulletin lists "MATH 26 or MATH 41" as
# interchangeable at that stage) — tier-mates with both, not a level above.
_MATH_PLACEMENT_TIERS: List[Tuple[int, Set[str]]] = [
    (1, {"MATH 21"}),
    (2, {"MATH 22", "MATH 26", "MATH 41"}),
    (3, {"MATH 110"}),
    (4, {"MATH 140", "MATH 140B", "MATH 140E", "MATH 140G", "MATH 140H",
         "MATH 141", "MATH 141B", "MATH 141E", "MATH 141G", "MATH 141H"}),
]


def _math_placement_tier(code: str) -> Optional[int]:
    for tier, codes in _MATH_PLACEMENT_TIERS:
        if code in codes:
            return tier
    return None


def math_placement_satisfied(
    code: str, completed: Set[str], placement_tier: Optional[int] = None
) -> bool:
    """True if `code` is a developmental/placement math course that
    `completed` (plus an optional ALEKS/high-school-calculus placement tier
    from detect_math_placement) already proves unnecessary — even though it
    was never literally completed for credit.

    Two different kinds of proof, deliberately handled differently:
    - An actual completed course carries real credit, so a higher one proves
      a lower one unnecessary at ANY tier (transfer/AP MATH 141 waives even
      a MATH 110 requirement).
    - An ALEKS score or "took calc in high school" is NOT completed credit —
      per PSU's real placement policy it only says where a student is
      allowed to *start*, tier 3+ (MATH 110/140+) still has to be actually
      taken and passed for real Gen Ed credit, so placement only waives the
      pure developmental/algebra-trig stepping stones below tier 3.
    """
    code = norm_code(code)
    if code in NON_DEGREE_APPLICABLE_MATH:
        return True
    tier = _math_placement_tier(code)
    if tier is None:
        return False
    completed = {norm_code(c) for c in completed}
    if code in ("MATH 22", "MATH 26") and "MATH 41" in completed:
        return True
    highest_completed = 0
    for c in completed:
        t = _math_placement_tier(c)
        if t is not None and t > highest_completed:
            highest_completed = t
    if highest_completed > tier:
        return True
    if placement_tier and tier <= 2 and placement_tier > tier:
        return True
    return False


_ALL_MATH_LADDER_CODES: Set[str] = NON_DEGREE_APPLICABLE_MATH | {
    c for _, codes in _MATH_PLACEMENT_TIERS for c in codes
}


def expand_math_placement(completed: Set[str], placement_tier: Optional[int] = None) -> Set[str]:
    """Return `completed` plus every developmental/placement math code that
    math_placement_satisfied proves unnecessary, added as if actually
    completed.

    This has to happen upstream of plan_progress/recommend_semester/
    build_full_plan, not inside them: a waived code needs to satisfy not
    just its own plan item, but any OTHER real catalog course whose actual
    prerequisite names it (e.g. CHEM 110 requiring MATH 22) — those courses
    check `completed` directly via prereqs_satisfied, with no idea plan
    items or waivers exist. Only call this for internal
    scheduling/progress/recommendation calls, never for the student-facing
    completed-course list — a waived code was never really taken, so
    showing it back to the student would misrepresent their transcript.
    """
    completed = {norm_code(c) for c in completed}
    waived = {
        code for code in _ALL_MATH_LADDER_CODES
        if math_placement_satisfied(code, completed, placement_tier)
    }
    return completed | waived


# ALEKS scores are 0-100; "math placement" is accepted as a plain-English
# synonym since that's the generic term students actually use, even though
# PSU's specific assessment is branded ALEKS.
_ALEKS_KEYWORD = r"(?:aleks|math\s+placement)"
_ALEKS_SCORE_RE = re.compile(
    rf"{_ALEKS_KEYWORD}\D{{0,20}}(\d{{1,3}})|(\d{{1,3}})\D{{0,20}}{_ALEKS_KEYWORD}",
    re.IGNORECASE,
)
_HS_CALC_RE = re.compile(
    r"\bcalc(?:ulus)?\b[^.]{0,25}\bhigh\s*school\b|\bhigh\s*school\b[^.]{0,25}\bcalc(?:ulus)?\b",
    re.IGNORECASE,
)


def _placement_tier_for_score(score: int) -> int:
    """bulletins.psu.edu Mathematics Placement chart: the ALEKS score at
    which a student places directly into each rung (skipping everything
    below it) — 30 -> MATH 21, 46 -> MATH 22/26/41, 61 -> MATH 110,
    76 -> MATH 140."""
    if score >= 76:
        return 4
    if score >= 61:
        return 3
    if score >= 46:
        return 2
    if score >= 30:
        return 1
    return 0


def detect_math_placement(prompt: str) -> Optional[Dict[str, Any]]:
    """Parse an ALEKS score or a high-school-calculus mention into an
    effective math placement tier for math_placement_satisfied.

    High school calculus auto-places into the 76-100 band per the
    bulletin's own stated policy ("students who have successfully completed
    a high school calculus course will automatically be eligible to enroll
    in MATH 110 or MATH 140") — no numeric score needed at all.
    """
    if _HS_CALC_RE.search(prompt):
        return {"tier": 4, "source": "high school calculus", "score": None}
    m = _ALEKS_SCORE_RE.search(prompt)
    if m:
        raw = m.group(1) or m.group(2)
        score = int(raw)
        if 0 <= score <= 100:
            return {"tier": _placement_tier_for_score(score), "source": "ALEKS score", "score": score}
    return None
