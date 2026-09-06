"""Deterministic degree-planning engine.

Loads semester-by-semester degree plans (degree_plans/<MAJOR>-<YEAR>.json),
merges per-department catalogs scraped from the PSU bulletin, and produces:

  - matched courses from free-form chat text ("I took cmpsc131 and calc 2")
  - a recommended next semester (prereq-safe, flowchart-ordered)
  - a full simulated plan to graduation
  - a deterministic Mermaid diagram of the student's position

No LLM involved: results are reproducible and always prerequisite-correct.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from Courseplanner import Course, load_catalog_from_json, save_catalog_to_json, scrape_psu_dept_catalog

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEGREE_PLAN_DIR = os.path.join(BASE_DIR, "degree_plans")
MINOR_PLAN_DIR = os.path.join(BASE_DIR, "minors")
CATALOG_DIR = os.path.join(BASE_DIR, "catalogs")
GEN_ED_PATH = os.path.join(BASE_DIR, "data", "gen_ed_courses.json")
GEN_ED_LEARNING_OBJECTIVES_PATH = os.path.join(BASE_DIR, "data", "gen_ed_learning_objectives.json")

# Real Penn State undergraduate campus names, as used on bulletins.psu.edu's
# own program listing. University Park is first (the default and, today,
# the only campus with any real plan data — every degree_plans/minors file
# built so far was researched against University Park bulletin pages).
# A plan file with no "campus" key is treated as University Park; a future
# branch-campus plan just needs an explicit "campus" field naming one of
# these to be picked up by the filters below automatically.
PSU_CAMPUSES: List[str] = [
    "University Park",
    "Abington",
    "Altoona",
    "Beaver",
    "Berks",
    "Brandywine",
    "DuBois",
    "Erie",
    "Fayette",
    "Greater Allegheny",
    "Harrisburg",
    "Hazleton",
    "Lehigh Valley",
    "Mont Alto",
    "New Kensington",
    "Schuylkill",
    "Scranton",
    "Shenango",
    "Wilkes-Barre",
    "World Campus",
    "York",
]
DEFAULT_CAMPUS = "University Park"

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
    not a code change + redeploy."""
    with open(COURSE_ALIASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


COURSE_ALIASES: Dict[str, str] = _load_course_aliases()
# Common spoken names for courses students type into chat.
COURSE_ALIASES: Dict[str, str] = {
    "CALC 1": "MATH 140",
    "CALCULUS 1": "MATH 140",
    "CALC I": "MATH 140",
    "CALC 2": "MATH 141",
    "CALCULUS 2": "MATH 141",
    "CALC II": "MATH 141",
    "CALC 3": "MATH 230",
    "CALCULUS 3": "MATH 230",
    "CALC III": "MATH 230",
    "LINEAR ALGEBRA": "MATH 220",
    "PHYSICS 1": "PHYS 211",
    "PHYSICS 2": "PHYS 212",
    "E&M": "PHYS 212",
    "ENGLISH COMP": "ENGL 15",
    "RHETORIC AND COMPOSITION": "ENGL 15",
    "TECHNICAL WRITING": "ENGL 202C",
    "PUBLIC SPEAKING": "CAS 100A",
    "SPEECH": "CAS 100A",
    "DISCRETE MATH": "CMPSC 360",
    "DATA STRUCTURES": "CMPSC 132",
    "INTRO TO PROGRAMMING": "CMPSC 131",
    # Cross-listed courses: the flowchart/plan shows a code under a second
    # department, but the bulletin only publishes course details under
    # one -- confirmed directly against the live bulletin (CMPEN's course
    # listing has no separate CMPEN 315; the CMPSC 315 "Computer Systems
    # I" page names no cross-listing either, so this is the flowchart's
    # own department-crossover label, not a second real course). Maps the
    # alias straight to the one real, catalogued code so a mention of
    # either resolves to the same actual course.
    "CMPEN 315": "CMPSC 315",
}


def norm_code(code: str) -> str:
    """Canonical course code: uppercase, single space, no leading zeros (ENGL 015 -> ENGL 15)."""
    s = re.sub(r"\s+", " ", (code or "").strip().upper().replace("\xa0", " "))
    m = re.match(r"^([A-Z]+)\s*0*(\d+[A-Z]*)$", s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return s


# ---------------------------------------------------------------------------
# Degree plans
# ---------------------------------------------------------------------------

def _plan_campuses(data: Dict[str, Any]) -> List[str]:
    """Normalizes a plan's "campus" field to a list — most plans (every one
    built before this) carry a single campus string, but a real PSU major
    is very often taught identically at many campuses at once (e.g.
    Management, B.S. lists 20 — see docs/BRANCH_CAMPUS_FINDINGS.md), which a
    single-string field can't represent without either duplicating the
    entire plan file per campus or silently only matching one. Accepts a
    bare string, a list, or nothing (defaults to [DEFAULT_CAMPUS])."""
    raw = data.get("campus")
    if not raw:
        return [DEFAULT_CAMPUS]
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, str) and c.strip()] or [DEFAULT_CAMPUS]
    return [DEFAULT_CAMPUS]


@lru_cache(maxsize=None)
def list_degree_plans(campus: Optional[str] = None) -> List[Dict[str, Any]]:
    """All degree plans, optionally filtered to one campus (case-insensitive
    exact match against any campus the plan is offered at). A plan with no
    "campus" field defaults to University Park — true of every plan built
    before multi-campus support existed, see PSU_CAMPUSES above."""
    plans = []
    if not os.path.isdir(DEGREE_PLAN_DIR):
        return plans
    wanted = campus.strip().lower() if campus else None
    for fname in sorted(os.listdir(DEGREE_PLAN_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(DEGREE_PLAN_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            plan_campuses = _plan_campuses(data)
            matched = next((c for c in plan_campuses if c.strip().lower() == wanted), None) if wanted else None
            if wanted is not None and matched is None:
                continue
            plans.append({
                "major": data.get("major", ""),
                "catalog_year": data.get("catalog_year"),
                "title": data.get("title", fname),
                # Single-campus callers (every existing frontend/test) still
                # get one string — the specific campus that was filtered on
                # (real casing from the data, not the caller's raw input),
                # or the first/primary one when listing everything
                # unfiltered, so this stays backward compatible.
                "campus": matched or plan_campuses[0],
                "campuses": plan_campuses,
            })
        except Exception:
            logger.exception("list_degree_plans: skipping unreadable/malformed plan file %s", fname)
            continue
    return plans


# Bounded (unlike the other lru_caches in this file, which key on a small,
# effectively-fixed set of real values like campus names): major/catalog_year
# comes straight from request bodies (/api/plan's major/additional_majors,
# /api/parse-transcript's form fields), so a burst of distinct bogus codes
# across many requests -- not just one, which the caller already caps the
# list length of -- would otherwise grow this cache (and repeat a real
# os.listdir() scan per miss) without limit. 512 comfortably covers every
# real major across every catalog year this app actually ships.
@lru_cache(maxsize=512)
def load_degree_plan(major: str, catalog_year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Load the plan for a major; latest catalog year if none requested.

    Cached: this is static reference data (a plan file only changes on
    deploy), and every caller either reads it read-only or hands it to
    merge_plans, which always copy.deepcopy()s before mutating anything
    -- so the same cached object being handed to many concurrent
    requests is safe by construction, not just by convention."""
    major = (major or "").strip().upper()
    if not os.path.isdir(DEGREE_PLAN_DIR):
        return None

    candidates = []
    for fname in os.listdir(DEGREE_PLAN_DIR):
        m = re.match(rf"^{re.escape(major)}-(\d{{4}})\.json$", fname)
        if m:
            candidates.append((int(m.group(1)), fname))
    if not candidates:
        return None

    if catalog_year is not None:
        wanted = [c for c in candidates if c[0] == int(catalog_year)]
        chosen = wanted[0] if wanted else max(candidates)
    else:
        chosen = max(candidates)

    with open(os.path.join(DEGREE_PLAN_DIR, chosen[1]), "r", encoding="utf-8") as f:
        plan = json.load(f)

    # Normalize option codes and give every item a stable id.
    next_id = 0
    for sem in plan.get("semesters", []):
        for item in sem.get("items", []):
            item["id"] = next_id
            next_id += 1
            if item.get("type") == "course":
                item["options"] = [norm_code(o) for o in item.get("options", [])]
    return plan


@lru_cache(maxsize=None)
def list_minor_plans(campus: Optional[str] = None) -> List[Dict[str, Any]]:
    """All minor plans, optionally filtered to one campus — same multi-
    campus-aware defaulting rule as list_degree_plans (no "campus" field =
    University Park; see _plan_campuses)."""
    minors = []
    if not os.path.isdir(MINOR_PLAN_DIR):
        return minors
    wanted = campus.strip().lower() if campus else None
    for fname in sorted(os.listdir(MINOR_PLAN_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(MINOR_PLAN_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            plan_campuses = _plan_campuses(data)
            matched = next((c for c in plan_campuses if c.strip().lower() == wanted), None) if wanted else None
            if wanted is not None and matched is None:
                continue
            minors.append({
                "minor": data.get("minor", ""),
                "catalog_year": data.get("catalog_year"),
                "title": data.get("title", fname),
                "campus": matched or plan_campuses[0],
                "campuses": plan_campuses,
            })
        except Exception:
            logger.exception("list_minor_plans: skipping unreadable/malformed minor file %s", fname)
            continue
    return minors


# Bounded for the same reason as load_degree_plan above.
@lru_cache(maxsize=512)
def load_minor_plan(minor: str, catalog_year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Load a minor's flat requirement list — mirrors load_degree_plan, but
    minors have no semester-by-semester flowchart, just a `requirements`
    list of the same course/slot item shapes."""
    minor = (minor or "").strip().upper()
    if not os.path.isdir(MINOR_PLAN_DIR):
        return None

    candidates = []
    for fname in os.listdir(MINOR_PLAN_DIR):
        m = re.match(rf"^{re.escape(minor)}-(\d{{4}})\.json$", fname)
        if m:
            candidates.append((int(m.group(1)), fname))
    if not candidates:
        return None

    if catalog_year is not None:
        wanted = [c for c in candidates if c[0] == int(catalog_year)]
        chosen = wanted[0] if wanted else max(candidates)
    else:
        chosen = max(candidates)

    with open(os.path.join(MINOR_PLAN_DIR, chosen[1]), "r", encoding="utf-8") as f:
        plan = json.load(f)

    for item in plan.get("requirements", []):
        if item.get("type") == "course":
            item["options"] = [norm_code(o) for o in item.get("options", [])]
    return plan


def _iter_plan_items(plan: Dict[str, Any]):
    for sem in plan.get("semesters", []):
        for item in sem.get("items", []):
            yield sem, item


# ---------------------------------------------------------------------------
# Plan merging (second major + minors)
# ---------------------------------------------------------------------------

def merge_plans(
    primary: Dict[str, Any],
    *,
    second_major: Optional[Dict[str, Any]] = None,
    additional_majors: Optional[List[Dict[str, Any]]] = None,
    minors: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Fold one or more additional majors' semesters and/or minors' flat
    requirement lists into `primary`'s own shape, so build_full_plan/
    recommend_semester/plan_progress need zero changes — they only ever
    touch plan['semesters'][*]['items'][*], and the only real invariant
    they rely on is item-id uniqueness within that list, which this
    preserves.

    `second_major` is kept as its own parameter for backward compatibility
    with every existing caller; `additional_majors` is a plain list for a
    3rd, 4th, ... major beyond that. Internally both are folded through the
    exact same per-major loop, in order (second_major first, then each of
    additional_majors) — there is no functional difference between the two
    beyond which parameter you use to pass them in.

    Returns `primary` UNCHANGED (same object, not even copied) when none of
    second_major / additional_majors / minors is given — the single-major
    fast path every existing caller hits, so this is a true no-op for all
    of them.

    A requirement (from either the second major or a minor) whose options
    overlap a course already required somewhere in the merged plan so far
    (e.g. a Statistics minor's own STAT 318 requirement, when the primary
    major — CMPSC — already requires STAT 318; or, just as importantly, two
    majors that both flatly require the SAME course like MATH 140, which
    would otherwise be scheduled once but demanded twice, since a single
    completed course can only ever satisfy one plan item — see
    plan_progress's one-completed-course-per-item rule) widens that
    EXISTING item with the new options instead of adding a second,
    redundant item that can never be satisfied: completing it once then
    genuinely counts toward both, tracked via the item's `also_satisfies`
    tag (see plan_progress). A minor requirement can also declare
    `substitutes_for_major_options` explicitly, for the case where the
    substitute is a DIFFERENT code than the existing literal requirement
    (e.g. a bulletin-documented "may take X instead of Y, and it'll count
    for both"), not just a literal same-course overlap. Requirements with
    no overlap become new items — a second major's inline in its own
    semester, a minor's in one trailing synthetic semester.

    Gen Ed slot dedup: a minor's or a second major's own generic Gen Ed
    slot (tagged with `gen_ed`, no specific course) is dropped outright if
    that domain is already covered somewhere in the plan so far. This is
    real, bulletin-verified PSU policy for concurrent majors, not a guess:
    AAPPM policy M-3 (Concurrent and Sequential Majors Programs) states
    "Students must fulfill all of the General Education requirements for
    at least one major listed on their record as well as all General
    Education courses listed as Major or Option requirements for their
    other degree(s)." — i.e. the generic 45cr Gen Ed pool is satisfied
    ONCE across a concurrent-majors plan, never doubled; but any course a
    major's own flowchart lists as a real requirement (even one that also
    happens to carry a Gen Ed tag, like MATH 140 satisfying GQ) is still
    required by that major regardless of Gen Ed overlap — which is
    exactly why this dedup only ever touches `type: "slot"` items (no
    specific course attached), never `type: "course"` items.
    """
    if not second_major and not additional_majors and not minors:
        return primary

    merged = copy.deepcopy(primary)
    next_id = max((item["id"] for _, item in _iter_plan_items(merged)), default=-1) + 1
    departments = list(merged.get("departments", []))

    def _all_course_options() -> Set[str]:
        opts: Set[str] = set()
        for _, item in _iter_plan_items(merged):
            if item.get("type") == "course":
                opts |= set(item.get("options", []))
        return opts

    def _gen_ed_domains() -> Set[str]:
        domains: Set[str] = set()
        for _, item in _iter_plan_items(merged):
            ge = item.get("gen_ed")
            if isinstance(ge, str):
                domains.add(ge)
            elif ge:
                domains.update(ge)
        return domains

    def _fold_requirement(req: Dict[str, Any], source_tag: str, also_tag: str) -> Optional[Dict[str, Any]]:
        """Widen an existing overlapping item in place (mutates `merged`,
        returns None) or hand back a fresh item ready for the caller to
        place (id assigned, not yet inserted anywhere)."""
        nonlocal next_id
        req_options = set(req.get("options", [])) if req.get("type") == "course" else set()
        hinted = set(req.get("substitutes_for_major_options", []))
        overlap_codes = (req_options | hinted) & _all_course_options()

        if overlap_codes:
            for _, existing_item in _iter_plan_items(merged):
                if existing_item.get("type") != "course":
                    continue
                if not (overlap_codes & set(existing_item.get("options", []))):
                    continue
                new_options = list(existing_item["options"])
                for code in req_options:
                    if code not in new_options:
                        new_options.append(code)
                existing_item["options"] = new_options
                also = existing_item.setdefault("also_satisfies", [])
                if also_tag not in also:
                    also.append(also_tag)
                return None

        new_item = dict(req)
        new_item["id"] = next_id
        next_id += 1
        new_item["source"] = source_tag
        # Tag newly-added items too (not just widened ones) so a minor's own
        # non-overlapping requirements still roll up into its own progress
        # bucket — without this, "how much of the minor is done" would only
        # ever reflect the courses it happened to share with the major.
        also = new_item.setdefault("also_satisfies", [])
        if also_tag not in also:
            also.append(also_tag)
        return new_item

    extra_majors = [m for m in [second_major, *(additional_majors or [])] if m]
    seen_major_codes = {primary.get("major", "").strip().upper()}
    for extra_major in extra_majors:
        extra_code = extra_major.get("major", "")
        # A major already merged in (or the primary itself) is skipped
        # outright rather than folded again — merging the same major twice
        # would just widen every one of its own items into themselves (a
        # harmless no-op), but it's meaningless to allow in the first place,
        # so this is a server-side backstop behind the frontend's own
        # duplicate-major prevention in the major-count picker.
        if extra_code.strip().upper() in seen_major_codes:
            continue
        seen_major_codes.add(extra_code.strip().upper())

        for dept in extra_major.get("departments", []):
            if dept not in departments:
                departments.append(dept)
        for sem in extra_major.get("semesters", []):
            gen_ed_domains = _gen_ed_domains()
            new_items = []
            for req in sem.get("items", []):
                if req.get("type") == "slot" and req.get("gen_ed"):
                    ge = req["gen_ed"]
                    req_domains = {ge} if isinstance(ge, str) else set(ge)
                    if req_domains & gen_ed_domains:
                        continue  # already covered — see PSU AAPPM M-3 below
                folded = _fold_requirement(req, f"major:{extra_code}", f"major:{extra_code}")
                if folded is not None:
                    new_items.append(folded)
            if not new_items:
                continue
            target = next(
                (s for s in merged["semesters"] if s.get("index") == sem.get("index")), None,
            )
            if target is not None:
                target["items"].extend(new_items)
            else:
                merged["semesters"].append({
                    "index": sem.get("index"),
                    "label": sem.get("label", f"Semester {sem.get('index')}"),
                    "items": new_items,
                })

    for minor in (minors or []):
        minor_code = minor.get("minor", "")
        for dept in minor.get("departments", []):
            if dept not in departments:
                departments.append(dept)

        gen_ed_domains = _gen_ed_domains()
        trailing_items = []
        for req in minor.get("requirements", []):
            if req.get("type") == "slot" and req.get("gen_ed"):
                ge = req["gen_ed"]
                req_domains = {ge} if isinstance(ge, str) else set(ge)
                if req_domains & gen_ed_domains:
                    continue  # major already covers this Gen Ed domain

            folded = _fold_requirement(req, f"minor:{minor_code}", f"minor:{minor_code}")
            if folded is not None:
                trailing_items.append(folded)

        if trailing_items:
            merged["semesters"].append({
                "index": len(merged["semesters"]) + 1,
                "label": f"{minor_code} Minor",
                "items": trailing_items,
            })

    merged["departments"] = departments
    return merged


def suggest_low_cost_minors(
    plan: Dict[str, Any],
    completed: Set[str],
    catalog_year: Optional[int],
    *,
    campus: Optional[str] = None,
    exclude_minors: Optional[Set[str]] = None,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    """Rank real minors by how few genuinely NEW courses they'd add on top
    of this major — not by size or popularity. merge_plans already widens
    a minor requirement into an existing major item (tagged via
    `also_satisfies`) wherever the two overlap, so "new courses needed" is
    just: of this minor's requirements, how many landed as a real new item
    (source == "minor:<code>") rather than a widened one, and of those, how
    many isn't the student already coincidentally satisfied via a course
    they've completed. This is what "a minor you can add without piling on
    a bunch of extra classes" concretely means, computed the same way the
    Progress page's per-minor bucket already is — not a separate estimate
    that could drift from what actually gets scheduled.
    """
    completed_norm = {norm_code(c) for c in completed}
    exclude = {norm_code(m) for m in (exclude_minors or set())}
    results: List[Dict[str, Any]] = []

    for entry in list_minor_plans(campus):
        code = norm_code(entry["minor"])
        if not code or code in exclude:
            continue
        minor_plan = load_minor_plan(code, catalog_year)
        if not minor_plan:
            continue

        merged = merge_plans(plan, minors=[minor_plan])
        tag = f"minor:{code}"
        total_reqs = len(minor_plan.get("requirements", []))
        if not total_reqs:
            continue

        new_items = [item for _, item in _iter_plan_items(merged) if item.get("source") == tag]
        shared_count = total_reqs - len(new_items)

        still_needed = []
        for item in new_items:
            if item.get("type") == "course" and any(
                norm_code(o) in completed_norm for o in item.get("options", [])
            ):
                continue  # already have a qualifying course, no new work
            still_needed.append(item)

        results.append({
            "minor": code,
            "title": minor_plan.get("title", code),
            "totalRequirements": total_reqs,
            "sharedWithMajor": shared_count,
            "newCoursesNeeded": len(still_needed),
            "extraCreditsNeeded": round(sum(float(i.get("credits") or 0) for i in still_needed), 1),
            "newCourseLabels": [
                i.get("label") or " or ".join(i.get("options", [])) for i in still_needed
            ],
        })

    results.sort(key=lambda r: (r["newCoursesNeeded"], r["extraCreditsNeeded"]))
    return results[:max_results]


# ---------------------------------------------------------------------------
# Merged catalog
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _load_dept_catalog_cached(dept: str) -> Optional[Dict[str, Course]]:
    dept = dept.upper()
    os.makedirs(CATALOG_DIR, exist_ok=True)
    path = os.path.join(CATALOG_DIR, f"{dept.lower()}_catalog.json")
    if os.path.exists(path):
        try:
            return load_catalog_from_json(path)
        except Exception:
            logger.exception(
                "_load_dept_catalog_cached: cached catalog at %s is unreadable/corrupt -- "
                "falling back to a live scrape of the PSU bulletin for %s", path, dept,
            )
    try:
        catalog = scrape_psu_dept_catalog(dept)
        save_catalog_to_json(path, catalog)
        return catalog
    except Exception:
        logger.exception(
            "_load_dept_catalog_cached: live scrape of %s failed -- this department will be "
            "silently missing from any merged catalog that requests it", dept,
        )
        return None


@lru_cache(maxsize=64)
def _load_merged_catalog_cached(depts_key: Tuple[str, ...]) -> Dict[str, Course]:
    merged: Dict[str, Course] = {}
    for dept in depts_key:
        cat = _load_dept_catalog_cached(dept)
        if not cat:
            continue
        for code, course in cat.items():
            merged[norm_code(code)] = course
    return merged


@lru_cache(maxsize=1)
def load_full_catalog() -> Dict[str, Course]:
    """Every department this app has a cached catalog file for, merged
    into one dict -- deliberately broader than load_merged_catalog's
    usual "just this plan's own departments" scope.

    Built for transcript parsing specifically: a real transcript export
    can legitimately contain a course from ANY department -- a Gen Ed
    elective, an exploratory course from before a major change, a minor
    that was never declared here -- and scoping that lookup to just the
    student's current major's departments silently dropped every one of
    those into "unmatched" even though the student genuinely completed
    a real PSU course (confirmed live against a real transcript: an
    Engineering student's ASTRO/NUTR/AA/ARTH gen-eds all vanished this
    way, despite each one having its own real, already-scraped catalog
    file on disk).

    Only reads *_catalog.json files already on disk -- never triggers
    _load_dept_catalog_cached's live-PSU-bulletin-scrape fallback, since
    every department name here comes from a file that already exists --
    so this stays a fast, request-time-safe lookup.
    """
    if not os.path.isdir(CATALOG_DIR):
        return {}
    depts = tuple(sorted(
        fname[: -len("_catalog.json")].upper()
        for fname in os.listdir(CATALOG_DIR)
        if fname.endswith("_catalog.json")
    ))
    return _load_merged_catalog_cached(depts)


def load_merged_catalog(depts: List[str]) -> Dict[str, Course]:
    """Merge several department catalogs into one dict keyed by normalized code.

    Previously rebuilt this dict from scratch on every call -- including
    every single /api/plan request, chat message or settings-only toggle
    alike, even though the department set (and therefore the result) is
    almost always identical across a session. Now cached by the upper-
    cased department tuple, preserving the original list's order (and any
    duplicates) exactly so merge behavior is unchanged -- same staleness
    assumption as every other cached loader here: a catalog only changes
    on a re-scrape/deploy, not mid-session, so serving the same merged
    dict object back is safe.
    """
    depts_key = tuple(d.upper() for d in depts)
    return _load_merged_catalog_cached(depts_key)


@lru_cache(maxsize=1)
def load_gen_ed_courses() -> Dict[str, Any]:
    """PSU's approved Gen Ed course lists, keyed by domain code (GQ, GWS,
    GA, GHW, GH, GN, GS, INTER-D, IL, US). Scraped once via
    scripts/scrape_gen_ed.py; re-run that script to refresh."""
    if not os.path.exists(GEN_ED_PATH):
        return {}
    with open(GEN_ED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_gen_ed_learning_objectives() -> Dict[str, List[str]]:
    """PSU's GenEd Learning Objective tags (Faculty Senate Policy 141-00),
    keyed by normalized course code -> list of full canonical objective
    names (e.g. "Critical and Analytical Thinking"). Purely descriptive
    metadata, separate from the domain system in load_gen_ed_courses() and
    not (yet) wired into any requirement-satisfaction logic. Scraped once
    via scripts/scrape_gen_ed_learning_objectives.py; re-run that script
    to refresh."""
    if not os.path.exists(GEN_ED_LEARNING_OBJECTIVES_PATH):
        return {}
    with open(GEN_ED_LEARNING_OBJECTIVES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _gen_ed_credits(raw: str, fallback: float) -> float:
    """Gen Ed credit fields are sometimes a range ('1-12'); take the low end
    so a recommended course never overshoots a semester's credit budget."""
    m = re.match(r"([\d.]+)", raw or "")
    return float(m.group(1)) if m else fallback


@lru_cache(maxsize=1)
def _gen_ed_domain_membership() -> Dict[str, Set[str]]:
    """Reverse index of load_gen_ed_courses(): course code -> the set of
    Gen Ed domain codes it appears on (a course can be approved for more
    than one domain, e.g. ART 116N is both GQ and GA). Used by
    plan_progress() to retroactively match an already-completed course
    against an open, single-domain Gen Ed slot. Cached for the same reason
    load_gen_ed_courses() itself is -- data/gen_ed_courses.json is static
    per-process."""
    membership: Dict[str, Set[str]] = {}
    for domain_code, entry in load_gen_ed_courses().items():
        for c in entry["courses"]:
            membership.setdefault(norm_code(c["code"]), set()).add(domain_code)
    return membership


# Foundations (GWS, GQ) and Knowledge Domain Breadth (GA, GH, GN, GS, GHW)
# -- the two Gen Ed components PSU's own official Bulletin explicitly bars
# Inter-Domain courses from. Every single line item under both components
# carries the identical footnote "Inter-Domain courses may not be used for
# this requirement" (confirmed against the current Baccalaureate Degree
# General Education Requirements PDF) -- an Inter-Domain-tagged course is
# only eligible for Integrative Studies (its own INTER-D bucket) or
# Exploration (which explicitly allows Inter-Domain courses), never these
# seven. Cultural Diversity (IL, US) is NOT in this set -- that's a
# separate overlay layer a course can combine with Inter-Domain (see
# plan_progress's own Cultural Diversity matching pass).
_INTER_DOMAIN_RESTRICTED_DOMAINS = {"GWS", "GQ", "GA", "GH", "GN", "GS", "GHW"}


def _blocked_as_inter_domain(code: str, domain: str) -> bool:
    """True if `code` can't count toward `domain` because it's Inter-Domain-
    tagged and `domain` is one of the seven Foundations/Knowledge-Domain-
    Breadth lines that bars Inter-Domain courses outright (see
    _INTER_DOMAIN_RESTRICTED_DOMAINS' own doc comment) -- checked against
    the course's REAL scraped domain tags, not re-derived by counting how
    many Knowledge Domains it happens to carry (some Inter-Domain courses,
    e.g. ART 116N, combine just one Knowledge Domain with a Foundations
    one, which a Knowledge-Domain-count heuristic alone would miss)."""
    return domain in _INTER_DOMAIN_RESTRICTED_DOMAINS and "INTER-D" in _gen_ed_domain_membership().get(code, ())


@lru_cache(maxsize=1)
def _gen_ed_course_titles() -> Dict[str, str]:
    """Reverse index of load_gen_ed_courses(): course code -> its title, for
    display purposes (compute_gen_ed_detail's ambiguousCourses). A course
    appears under the same title on every domain list it's cross-listed on,
    so first-one-wins is safe."""
    titles: Dict[str, str] = {}
    for entry in load_gen_ed_courses().values():
        for c in entry["courses"]:
            titles.setdefault(norm_code(c["code"]), c.get("title") or "")
    return titles


def _pick_gen_ed_course(
    domain: str,
    catalog: Dict[str, Course],
    exclude_dept: Optional[str],
    completed: Set[str],
    exclude: Set[str],
    preferred_codes: Optional[Set[str]] = None,
    bonus_domains: Optional[Set[str]] = None,
) -> Optional[Tuple[str, str, float]]:
    """First eligible course for a Gen Ed domain slot: not already
    completed/picked, and not in the student's own major department (the
    'Firewall' rule — major-prefix courses can't double-count as Gen Ed,
    except Inter-Domain/Integrative Studies, which are exempt by policy).
    `completed` gates real prereq checks for courses we've also scraped a
    department catalog for; most Gen Ed courses aren't in any scraped
    catalog, so they're offered prereq-unchecked, same as a student
    browsing the Gen Ed list directly.

    preferred_codes are courses the student explicitly asked for: if any of
    them is otherwise eligible for this domain (passes every check below),
    it's returned instead of the list's first eligible entry — ties among
    multiple eligible preferred codes break by the domain list's own order,
    same as the no-preference fallback. When none of preferred_codes is
    eligible (or it's None/empty), behavior is byte-identical to omitting
    it: first eligible course in list order.

    bonus_domains are OTHER Gen Ed domains this plan currently has a still-
    open slot for (see recommend_semester's own call site) — not an
    explicit student request like preferred_codes, so it never outranks
    one, but among candidates with no preferred match, a course ALSO
    approved for one of these domains is returned instead of the list's
    first eligible entry: taking it resolves two open requirements from
    one recommended enrollment instead of one (mirrors plan_progress's own
    retroactive Cultural Diversity matching, confirmed against PSU's real
    course-search tool — a course tagged both Inter-Domain and US offers
    "Add to all requirements above" crediting both at once). When
    bonus_domains is None/empty, behavior is byte-identical to omitting it.
    """
    domains = load_gen_ed_courses()
    entry = domains.get(domain)
    if not entry:
        return None
    firewall_exempt = domain == "INTER-D"
    preferred = {norm_code(c) for c in preferred_codes} if preferred_codes else None
    membership = _gen_ed_domain_membership() if bonus_domains else None
    first_eligible: Optional[Tuple[str, str, float]] = None
    first_bonus_eligible: Optional[Tuple[str, str, float]] = None
    for c in entry["courses"]:
        code = norm_code(c["code"])
        if code in exclude:
            continue
        if not firewall_exempt and exclude_dept and code.startswith(f"{exclude_dept} "):
            continue
        if _blocked_as_inter_domain(code, domain):
            continue
        # Same "excluded from recommendations unless the student asks for
        # them" rule score_recommendations/_pick_open_elective already
        # apply (special topics, internships, First-Year Seminars, ...) --
        # a real, approved Gen Ed course like AFAM 83 "First-Year Seminar
        # in African American Studies" shouldn't get auto-picked just
        # because it's on the domain's list, but an explicit request for
        # it by code still wins, same as preferred_codes already does below.
        if _EXCLUDE_NAME_RE.search(c["title"] or "") and not (preferred and code in preferred):
            continue
        course = catalog.get(code)
        if course:
            if not prereqs_satisfied(course, completed):
                continue
            if not concurrent_satisfied(course, exclude):
                continue
            if not excludes_satisfied(course, completed):
                continue
        result = (code, c["title"], _gen_ed_credits(c.get("credits", ""), 3.0))
        if not preferred and not bonus_domains:
            return result
        if first_eligible is None:
            first_eligible = result
        if preferred and code in preferred:
            return result
        if (
            bonus_domains
            and first_bonus_eligible is None
            and membership.get(code, set()) & bonus_domains
            # The bonus domains this caller passes are always US/IL (see
            # recommend_semester's own call site), never INTER-D, so
            # they're never Firewall-exempt -- a major-department course
            # can legitimately be eligible for ITS OWN Inter-Domain slot
            # (that domain's exemption) while still being unable to ALSO
            # count for that major's US/IL slot, so the bonus annotation
            # must not claim it would.
            and not (exclude_dept and code.startswith(f"{exclude_dept} "))
        ):
            first_bonus_eligible = result
    return first_bonus_eligible or first_eligible


_COURSE_NUMBER_RE = re.compile(r"^[A-Z]+\s+(\d+)")

# A bare trailing "H" immediately after the course number is Penn State's
# consistent marker for the honors section of the same course (unlike W,
# N, or Y suffixes, which denote a genuinely different course) — e.g.
# "MATH 220H" is Honors Matrices, the same content as "MATH 220" Matrices.
_HONORS_SUFFIX_RE = re.compile(r"^([A-Z]+ \d+)H$")


def _honors_base_code(code: str) -> Optional[str]:
    """If `code` is the honors-suffixed variant of another course, return
    that course's base code; otherwise None."""
    m = _HONORS_SUFFIX_RE.match(code)
    return m.group(1) if m else None


def _is_effectively_completed(code: str, completed: Set[str]) -> bool:
    """True if `code` itself is completed, or the student completed its
    honors variant / base course instead — completed is otherwise matched
    by exact code only, which would treat "MATH 220" and "MATH 220H" as
    two unrelated courses."""
    if code in completed:
        return True
    base = _honors_base_code(code)
    if base is not None and base in completed:
        return True
    return f"{code}H" in completed


def _pick_open_elective(
    catalog: Dict[str, Course],
    completed: Set[str],
    exclude: Set[str],
    *,
    min_level: Optional[int] = None,
    max_level: Optional[int] = None,
    exclude_exact: Optional[Iterable[str]] = None,
    exclude_prefixes: Optional[Iterable[str]] = None,
    prefer_prefixes: Optional[List[str]] = None,
) -> Optional[Tuple[str, str, float]]:
    """First eligible course for a "pick almost anything, except this
    denylist" slot — PSU's real Department List / Supporting Course /
    Technical Elective requirements, which name what to avoid far more
    precisely than what to take. Unlike _pick_gen_ed_course (one official
    university-wide list), this searches every course in the plan's own
    loaded catalog — real but intentionally narrower than "any course at
    Penn State": only departments the plan itself already pulls in (see
    `departments` in the plan JSON) are ever candidates, so a department the
    major has no other reason to load is never suggested even though the
    real degree audit would allow it. Widening that requires adding the
    department to the plan's own `departments` list.

    exclude_exact / exclude_prefixes encode a handbook's explicit denylist.
    prefer_prefixes tries departments in that order first, matching a
    handbook's own stated default instead of an arbitrary alphabetical pick.
    max_level caps the course number (inclusive) — e.g. a bulletin's
    "300-level" category (300-399) is distinct from a separate "400-level"
    category in the same plan, and min_level alone can't tell them apart.

    Independent study / special topics / co-op / foreign study / thesis
    courses are never picked by default (the same `_EXCLUDE_NAME_RE`
    convention already used in score_recommendations) — every department
    has its own version of these, and a generic "pick almost anything" slot
    recommending one by default is always wrong: they need a faculty
    sponsor or petition a real student doesn't have yet, not an auto-pick.
    """
    exclude_exact_set = {norm_code(c) for c in (exclude_exact or [])}
    exclude_prefix_tuple = tuple(exclude_prefixes or ())

    def eligible(code: str, course: Course) -> bool:
        if code in exclude or code in exclude_exact_set:
            return False
        if any(code.startswith(f"{p} ") for p in exclude_prefix_tuple):
            return False
        if _EXCLUDE_NAME_RE.search(course.name or ""):
            return False
        if _is_effectively_completed(code, completed):
            return False
        if min_level is not None or max_level is not None:
            m = _COURSE_NUMBER_RE.match(code)
            if not m:
                return False
            level = int(m.group(1))
            if min_level is not None and level < min_level:
                return False
            if max_level is not None and level > max_level:
                return False
        if not prereqs_satisfied(course, completed):
            return False
        if not excludes_satisfied(course, completed):
            return False
        return True

    codes = sorted(catalog.keys())
    if prefer_prefixes:
        def sort_key(code: str):
            for i, p in enumerate(prefer_prefixes):
                if code.startswith(f"{p} "):
                    return (i, code)
            return (len(prefer_prefixes), code)
        codes = sorted(codes, key=sort_key)

    for code in codes:
        course = catalog[code]
        if eligible(code, course):
            return code, course.name, course.credits
    return None


# ---------------------------------------------------------------------------
# Prerequisite / eligibility helpers
# ---------------------------------------------------------------------------

def _norm_groups(groups: List[Set[str]]) -> List[Set[str]]:
    return [{norm_code(x) for x in g} for g in groups]


def prereqs_satisfied(course: Course, completed: Set[str]) -> bool:
    return all(g & completed for g in _norm_groups(course.prereq_groups))


def concurrent_satisfied(course: Course, completed_or_planned: Set[str]) -> bool:
    return all(g & completed_or_planned for g in _norm_groups(course.concurrent_groups))


def missing_prereqs(course: Course, completed: Set[str]) -> List[List[str]]:
    return [sorted(g) for g in _norm_groups(course.prereq_groups) if not g & completed]


def exclusion_conflict(course: Course, completed: Set[str]) -> Set[str]:
    """Subset of `completed` that disqualifies `course` — a flat OR check
    ("if you've completed ANY of these, you may not also take/count this
    course"), unlike prereq_groups' AND-of-OR-groups. Empty = no conflict."""
    return {norm_code(x) for x in course.excludes} & {norm_code(c) for c in completed}


def excludes_satisfied(course: Course, completed: Set[str]) -> bool:
    return not exclusion_conflict(course, completed)


# ---------------------------------------------------------------------------
# Math placement — real PSU ALEKS ladder
# ---------------------------------------------------------------------------
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


@lru_cache(maxsize=None)
def _unlock_index(depts_key: Tuple[str, ...]) -> Dict[str, int]:
    """code -> number of catalog courses that (transitively) require it."""
    catalog = load_merged_catalog(list(depts_key))
    direct: Dict[str, Set[str]] = {}
    for code, course in catalog.items():
        for g in _norm_groups(course.prereq_groups) + _norm_groups(course.concurrent_groups):
            for dep in g:
                direct.setdefault(dep, set()).add(code)

    memo: Dict[str, Set[str]] = {}

    def reach(code: str, stack: Set[str]) -> Set[str]:
        if code in memo:
            return memo[code]
        if code in stack:  # defensive: cyclic data
            return set()
        stack.add(code)
        out: Set[str] = set()
        for child in direct.get(code, ()):
            out.add(child)
            out |= reach(child, stack)
        stack.discard(code)
        memo[code] = out
        return out

    return {code: len(reach(code, set())) for code in catalog}


def unlock_count(code: str, depts: List[str]) -> int:
    return _unlock_index(tuple(sorted(d.upper() for d in depts))).get(norm_code(code), 0)


# ---------------------------------------------------------------------------
# Chat course matching
# ---------------------------------------------------------------------------

_NOT_COURSE_WORDS = {
    "AND", "OR", "THE", "FOR", "TOOK", "SEM", "YEAR", "TERM", "TOP",
    "GPA", "GEN", "ED", "AP", "IB", "GHW", "FYS", "NEXT", "TAKE", "ALL",
    # A transcript's own GPA-summary rows ("Term Totals 18.000 18.000
    # 18.000 72.000", "Cum Totals 53.000 ...") regex-match the same
    # shape as a real course mention -- "Totals" immediately followed by
    # a 2-3 digit running-credit total looks exactly like "DEPT ###" to
    # COURSE_CODE_RE. Confirmed live against a real transcript export:
    # every term's running-total line leaked into "unmatched" as junk
    # like "TOTALS 18", "TOTALS 53" -- noise no real course was ever
    # dropped for, so it's filtered outright rather than surfaced as a
    # hint.
    "TOTALS",
    # Same story for a transcript's AP/IB "Test Credits" section -- "AP
    # Calculus AB 01/01/2023" reads as "AB" immediately followed by the
    # date's leading "01", producing an "AB 01" hint that isn't a real
    # course mention. "AB" isn't a real PSU department, so this is
    # always safe to drop (see the mention_code-in-catalog escape hatch
    # above -- a real "AB ###" course would still get through).
    "AB",
}

# Full department-name words a student might type instead of PSU's real
# short course-code prefix ("physics 211" instead of "PHYS 211") --
# COURSE_CODE_RE's own dept-prefix capture is capped at 6 letters (long
# enough for every real PSU prefix), so "PHYSICS" (7) can never match it
# directly and a mention like "physics 211" was silently dropped. Handled
# as its own small, explicit lookup rather than just widening that cap,
# so this can't start treating an arbitrary long English word ahead of a
# number ("completed 10 courses") as a course-code mention.
DEPT_NAME_ALIASES: Dict[str, str] = {
    "PHYSICS": "PHYS",
    "CHEMISTRY": "CHEM",
    "STATISTICS": "STAT",
    "PSYCHOLOGY": "PSYCH",
    "SOCIOLOGY": "SOC",
    "ECONOMICS": "ECON",
    "PHILOSOPHY": "PHIL",
    "BIOLOGY": "BIOL",
    # Expanded past the one department (Physics) that first surfaced this
    # bug to cover every other real PSU subject with a single-word full
    # name that doesn't literally equal its short catalog prefix -- a
    # spelled-out mention in ANY of these majors hit the identical silent
    # -drop bug, not just Physics. Only single-word official subject names
    # are listed here (COURSE_CODE_RE's alias slot is one token) -- compound
    # names ("Computer Science," "Electrical Engineering," "Political
    # Science," ...) aren't included since a student typing those out
    # wouldn't produce a single word immediately before the course number
    # anyway, so the underlying bug doesn't apply to them the same way.
    "ACCOUNTING": "ACCTG",
    "AGRICULTURE": "AG",
    "AGRONOMY": "AGRO",
    "AGROECOLOGY": "AGECO",
    "ANTHROPOLOGY": "ANTH",
    "ARCHITECTURE": "ARCH",
    "ASTRONOMY": "ASTRO",
    "BIOETHICS": "BIOET",
    "BIOTECHNOLOGY": "BIOTC",
    "CHINESE": "CHNS",
    "COMMUNICATIONS": "COMM",
    "CRIMINOLOGY": "CRIM",
    "CYBERSECURITY": "CYBER",
    "EDUCATION": "EDUC",
    "ENGLISH": "ENGL",
    "ENGINEERING": "ENGR",
    "ENTOMOLOGY": "ENT",
    "FINANCE": "FIN",
    "FORESTRY": "FOR",
    "FRENCH": "FR",
    "GEOGRAPHY": "GEOG",
    "GEOSCIENCES": "GEOSC",
    "GERMAN": "GER",
    "HEBREW": "HEBR",
    "HISTORY": "HIST",
    "HORTICULTURE": "HORT",
    "ITALIAN": "IT",
    "JAPANESE": "JAPNS",
    "KINESIOLOGY": "KINES",
    "KOREAN": "KOR",
    "LINGUISTICS": "LING",
    "MATHEMATICS": "MATH",
    "MANAGEMENT": "MGMT",
    "MARKETING": "MKTG",
    "METEOROLOGY": "METEO",
    "MICROBIOLOGY": "MICRB",
    "MINING": "MNG",
    "NURSING": "NURS",
    "NUTRITION": "NUTR",
    "PHOTOGRAPHY": "PHOTO",
    "RUSSIAN": "RUS",
    "SPANISH": "SPAN",
    "SURVEYING": "SUR",
    "THEATRE": "THEA",
    "THEATER": "THEA",
    "TURFGRASS": "TURF",
    "WILDLIFE": "WILDL",
}
# Lookahead-only (no number captured here) and requires a real 2-3 digit
# course number specifically -- "physics 1"/"physics 2" (the sequence-number
# phrasing already handled by COURSE_ALIASES below) must NOT be rewritten
# here, or the literal "PHYSICS 1" text that pass searches for would already
# be gone by the time it runs.
_DEPT_NAME_RE = re.compile(
    r"\b(" + "|".join(DEPT_NAME_ALIASES) + r")\b(?=\s*-?\s*\d{2,3}[A-Z]{0,2}\b)"
)

# DEPT_NAME_ALIASES intentionally excludes multi-word official names (see
# its own comment) -- but the regex above only requires the aliased word to
# sit immediately before the course number, with no check on what precedes
# THAT word. So "Electrical Engineering 210" still matches on the tail word
# "Engineering" -> ENGR, producing the phantom code "ENGR 210" (masking the
# real course EE 210); "Special Education 400" matches on "Education" ->
# EDUC, silently recording the real-but-wrong EDUC 400.
#
# A first attempt at guarding against this used an ALLOWLIST of "filler"
# words that were the only things permitted to precede the aliased word,
# blocking expansion for anything else. That inverted the actual odds: a
# real multi-word official compound name is the rare case, while ordinary
# sentences constantly put some other word right before the aliased word --
# including the app's own most common completion triggers ("completed",
# "passed", "finished", ...), none of which were in the filler list. So
# "I completed Physics 211" got blocked exactly like "Electrical
# Engineering 210" was supposed to be, and the whole mention silently
# vanished.
#
# Fixed as a DENYLIST instead: a small, explicit set of real PSU compound-
# major/department modifier words that are known to precede one of
# DEPT_NAME_ALIASES' tail words in a genuine official multi-word name.
# Only a preceding word in THIS set blocks expansion; every other preceding
# word (any ordinary verb, pronoun, article, or completion trigger a
# student would actually type) permits it, same as a clause boundary with
# no preceding word at all (start of string, or right after punctuation --
# _PRECEDING_WORD_RE simply finds none there).
#
# SCOPED PER TAIL WORD (round-2 regression fix): a first cut at this
# denylist was one flat, unscoped set checked with no awareness of which
# DEPT_NAME_ALIASES tail word was actually being matched -- so a modifier
# seeded to guard only one compound (e.g. "PHYSICAL" for "Physical
# Education") silently blocked every OTHER alias tail word it happened to
# precede too: "physical chemistry 457"/"physical geography 010" got
# blocked by the same "PHYSICAL" entry that was only ever meant to guard
# "Physical Education", and likewise "BIOLOGICAL" (seeded for "Biological
# Engineering") blocked "biological anthropology 021", "AGRICULTURAL"
# (seeded for "Agricultural Engineering") blocked "agricultural economics
# 104" -- all silently vanishing with no match at all. Keyed by the exact
# alias tail word (DEPT_NAME_ALIASES key / regex group(1) match) so a
# modifier only ever blocks the ONE compound it was actually seeded for.
_DEPT_NAME_BLOCK_WORDS: Dict[str, Set[str]] = {
    # ... ENGINEERING (Aerospace/Agricultural/Architectural/Biological/
    # Biomedical/Chemical/Civil/Computer/Electrical/Energy/Environmental/
    # Industrial/Mechanical/Mining/Nuclear/Petroleum Engineering are all
    # real official or commonly-shortened PSU major names)
    "ENGINEERING": {
        "AEROSPACE", "AGRICULTURAL", "ARCHITECTURAL", "BIOLOGICAL",
        "BIOMEDICAL", "CHEMICAL", "CIVIL", "COMPUTER", "ELECTRICAL", "ENERGY",
        "ENVIRONMENTAL", "INDUSTRIAL", "MECHANICAL", "MINING", "NUCLEAR",
        "PETROLEUM",
    },
    # ... EDUCATION (Adult/Career [and Technical]/Early [Childhood]/
    # Elementary [and Kindergarten]/Physical/Secondary/Special/Workforce
    # Education [and Development])
    "EDUCATION": {
        "ADULT", "CAREER", "CHILDHOOD", "EARLY", "ELEMENTARY", "PHYSICAL",
        "SECONDARY", "SPECIAL", "WORKFORCE",
    },
    # ... ARCHITECTURE (Landscape Architecture)
    "ARCHITECTURE": {"LANDSCAPE"},
    # ... HISTORY (Art History)
    "HISTORY": {"ART"},
    # ... MANAGEMENT (Risk Management, Supply Chain Management,
    # Hospitality Management)
    "MANAGEMENT": {"CHAIN", "HOSPITALITY", "RISK"},
}
_PRECEDING_WORD_RE = re.compile(r"([A-Z']+)\s*$")

# "MATH 140, 141" or "MATH 140 and 141" -- a student listing several course
# numbers under one department once, expecting each to count. COURSE_CODE_RE
# only ever anchors a dept prefix to the number immediately next to it and
# has no memory of an earlier one in the same sentence, so today only the
# first number in a run like this is ever recognized and the rest silently
# vanish. Requires at least one comma/and-joined continuation, so a lone
# "MATH 140" is left untouched.
_MULTI_COURSE_RUN_RE = re.compile(
    r"\b[A-Z]{2,6}\s*-?\s*\d{2,3}[A-Z]{0,2}"
    r"(?:(?:\s*,\s*(?:AND\s+)?|\s+AND\s+)\d{2,3}[A-Z]{0,2})+\b"
)


def _expand_dept_names(raw: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        word = m.group(1)
        # Look only at what's immediately before this word (bounded search,
        # not a new substring) -- restricted to a real preceding *word*,
        # since anything else (digits, punctuation, nothing) means there's
        # no compound-name signal and this is safe to expand.
        prev = _PRECEDING_WORD_RE.search(m.string, 0, m.start(1))
        if prev and prev.group(1) in _DEPT_NAME_BLOCK_WORDS.get(word, ()):
            return word  # tail of a longer compound name -- leave as-is
        return DEPT_NAME_ALIASES[word]

    return _DEPT_NAME_RE.sub(repl, raw)


def _expand_multi_course_mentions(raw: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        run = m.group(0)
        dept = re.match(r"[A-Z]{2,6}", run).group(0)
        nums = re.findall(r"\d{2,3}[A-Z]{0,2}", run)
        return " ".join(f"{dept} {n}" for n in nums)

    return _MULTI_COURSE_RUN_RE.sub(repl, raw)


def match_courses_in_text(text: str, catalog: Dict[str, Course]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Find course mentions in free-form text and resolve them against the catalog.

    Returns (matched, unmatched): matched entries carry code/name/credits so the
    UI can show the student exactly what was understood.
    """
    raw = (text or "").upper()
    raw = _expand_dept_names(raw)
    raw = _expand_multi_course_mentions(raw)
    matched: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    seen: Set[str] = set()
    # A course-code-shaped alias (e.g. a cross-listed "CMPEN 315" -> "CMPSC
    # 315") gets resolved correctly by the alias pass below, but its raw
    # text ALSO looks like a real course-code mention to COURSE_CODE_RE --
    # without this, the second pass re-processes the same "CMPEN 315" text
    # as a literal, nonexistent course and dumps it in unmatched, so the
    # same mention shows up as both correctly credited AND "couldn't
    # match" at once. Tracking which raw mentions the alias pass already
    # claimed lets the second pass skip them.
    claimed_mentions: Set[str] = set()

    def add(code: str, mention: str):
        code = norm_code(code)
        if code in seen:
            return
        course = catalog.get(code)
        if course:
            seen.add(code)
            matched.append({
                "code": code,
                "name": course.name,
                "credits": course.credits,
                "mention": mention.strip(),
            })
        else:
            unmatched.append(mention.strip())

    for alias, code in COURSE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", raw):
            add(code, alias.title())
            claimed_mentions.add(norm_code(alias))

    for m in COURSE_CODE_RE.finditer(raw):
        dept, num = m.groups()
        mention_code = norm_code(f"{dept} {num}")
        # _NOT_COURSE_WORDS exists to filter ordinary-English false positives
        # ("prerequisite FOR 200 level courses"), but a couple of its entries
        # (FOR, IB) are ALSO real PSU department prefixes. Checking the
        # catalog first lets a genuinely cataloged course through even when
        # its dept token is on the stopword list, while still treating that
        # same token as plain English when this student's currently-loaded
        # catalog has no such department (mention_code not in catalog).
        if dept in _NOT_COURSE_WORDS and mention_code not in catalog:
            continue
        if mention_code in claimed_mentions:
            continue
        add(f"{dept} {num}", m.group(0))

    return matched, unmatched


# ---------------------------------------------------------------------------
# Bulk / inverse completion ("I'm a junior", "everything except my last year")
# ---------------------------------------------------------------------------

# Standing -> semesters already completed (a freshman has 0 done; a senior
# has finished 6 of a typical 8-semester plan and has one year left).
_CLASS_STANDING_SEMESTERS = {
    "freshman": 0,
    "sophomore": 2,
    "junior": 4,
    "senior": 6,
}

_YEARS_COMPLETED_RE = re.compile(
    r"\bcompleted\s+(\d+)\s+years?\b|\b(\d+)\s+years?\s*(?:of\s+(?:college|school))?\s*"
    r"(?:done|completed|finished)\b",
    re.IGNORECASE,
)

# "everything/all ... except/but ..." — deliberately loose (up to ~40 chars
# between the two anchors) so it catches "everything except my last year" and
# "all of my classes but these three courses" alike.
_EXCEPT_RE = re.compile(r"\b(?:everything|all)\b.{0,40}?\b(?:except|but)\b", re.IGNORECASE | re.DOTALL)
_LAST_YEAR_RE = re.compile(r"\b(?:last|final|senior)\s+year\b", re.IGNORECASE)


def detect_bulk_completion(prompt: str, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Detect class-standing / elapsed-years / 'everything except' phrasing
    and translate it into a semester cutoff (every item at or before that
    semester index counts as already completed).

    Must be run on the RAW prompt, not a clause from app.py's
    _split_clauses — that function already splits on " but ", so "everything
    but my last year" would be cut in half before this ever saw it.
    """
    if not prompt:
        return None
    low = prompt.lower()
    total_semesters = len(plan.get("semesters", []))

    if _EXCEPT_RE.search(low):
        if _LAST_YEAR_RE.search(low):
            return {
                "semesters_done": max(total_semesters - 2, 0),
                "description": "everything except your last year",
            }
        # "everything except <named courses>" — those courses are carved out
        # by the caller (via match_courses_in_text on this same prompt) and
        # passed as excluded_codes to apply_bulk_completion; here the whole
        # plan is in scope.
        return {"semesters_done": total_semesters, "description": "everything except the named course(s)"}

    for word, semesters in _CLASS_STANDING_SEMESTERS.items():
        if re.search(rf"\b{word}\b", low):
            return {"semesters_done": semesters, "description": f"{word} standing"}

    m = _YEARS_COMPLETED_RE.search(low)
    if m:
        years = int(m.group(1) or m.group(2))
        return {
            "semesters_done": min(years * 2, total_semesters),
            "description": f"{years} year{'s' if years != 1 else ''} completed",
        }

    return None


def apply_bulk_completion(
    plan: Dict[str, Any],
    catalog: Dict[str, Course],
    semesters_done: int,
    excluded_codes: Optional[Set[str]] = None,
) -> Tuple[Set[str], Set[int]]:
    """Mark every plan item at or before `semesters_done` as done.

    Course items contribute one representative option code each — never one
    already in excluded_codes, and never one already claimed by an earlier
    item in this same call, since two items sharing an option pool must land
    on two distinct codes (the same concern _ranked_options's own docstring
    calls out for the interactive per-semester picker). Slot items (no real
    course code) contribute their id instead, for the caller to pass through
    as consumed_slots. Returns (completed_codes, slot_ids).
    """
    excluded = {norm_code(c) for c in (excluded_codes or set())}
    completed_codes: Set[str] = set()
    slot_ids: Set[int] = set()
    claimed: Set[str] = set()

    for sem, item in _iter_plan_items(plan):
        if sem.get("index", 0) > semesters_done:
            continue
        if item.get("type") == "course":
            for code in item.get("options", []):
                code = norm_code(code)
                if code in excluded or code in claimed:
                    continue
                claimed.add(code)
                completed_codes.add(code)
                break
        else:
            slot_ids.add(item["id"])

    return completed_codes, slot_ids


# ---------------------------------------------------------------------------
# Plan progress (pure — no plan mutation)
# ---------------------------------------------------------------------------

def _item_category(item: Dict[str, Any]) -> str:
    """Bucket a plan item for the Progress page's by-requirement-type
    breakdown. Every prescribed/required course counts as 'major' — PSU
    itself double-counts some of these into Gen Ed (e.g. MATH 140), so a
    course item is never re-labeled just because it happens to also satisfy
    a Foundations requirement. Slot items are categorized by their tagged
    domain or label, since that's all a plan item carries.

    Every Gen Ed slot -- single-domain ("gen_ed": "GA") or a multi-domain
    choice ("gen_ed": ["GA", "GH"]) alike -- files under one flat "gen_ed"
    bucket. A per-domain split (a separate bar for Arts, Health & Wellness,
    Interdomain, ...) was tried and deliberately reverted: the Progress
    page should read as one clear "General education" line, not a dozen
    fragmented ones. NOTE: plan_progress()'s Gen Ed leftover-absorption
    pass (a completed course retroactively crediting an open Gen Ed slot)
    still resolves by real domain internally via item["gen_ed"] directly,
    completely independent of this function -- only the DISPLAY bucket a
    credited course lands in was reverted here, not the matching logic
    itself."""
    if item.get("type") == "course":
        return "major"
    if item.get("gen_ed"):
        return "gen_ed"
    label = (item.get("label") or "").lower()
    if "gen ed" in label:
        return "gen_ed"
    if "world language" in label:
        return "world_language"
    if "supporting" in label or "department-approved" in label or "business breadth" in label:
        return "supporting"
    if "elective" in label:
        return "elective"
    return "other"


def plan_progress(
    plan: Dict[str, Any],
    completed: Set[str],
    *,
    consumed_slots: Optional[Set[int]] = None,
    gen_ed_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Determine which plan items are satisfied.

    Course items are satisfied when one of their options was completed (each
    completed course can satisfy only one item). Pattern slots (e.g.
    CMPSC/CMPEN 4XX) absorb leftover completed courses; single-domain Gen Ed
    slots (item["gen_ed"] is a plain string, e.g. "GA") do the same against
    PSU's approved domain lists (see _gen_ed_domain_membership), honoring the
    same major-department Firewall rule _pick_gen_ed_course enforces for
    forward-looking recommendations. Every other slot (multi-domain
    "gen_ed": ["GA", "GH"] choice slots included -- see _item_category's
    docstring for why those can't be resolved this way) is done only when
    listed in consumed_slots (used by the semester simulation and by
    apply_bulk_completion's bulk-completion shortcut) -- neither of which
    ever runs over a student-supplied `completed` list, which is exactly
    the gap the pattern-slot and Gen Ed leftover absorption below closes.

    gen_ed_overrides (course code -> domain code, e.g. {"ART 116N": "GA"})
    steers the single-domain Gen Ed absorption pass below for a course that's
    genuinely ambiguous IN THIS PLAN -- approved for 2+ domains this plan has
    an open slot for (see compute_gen_ed_detail, which reports exactly that
    ambiguous set). An override is honored only when the named domain is one
    of that course's real ambiguous options here; anything else (a domain the
    course isn't approved for, a non-ambiguous course, an unknown course or
    domain code) is silently ignored and that course falls back to today's
    default first-plan-order-match resolution -- see _gen_ed_ambiguous_domains
    below. None/empty leaves every existing code path byte-identical.

    Callers who want math-placement waivers (see math_placement_satisfied)
    to count as "completed" should pass an already-expanded `completed` —
    see expand_math_placement — so a waived course also unlocks any OTHER
    real catalog prereq chain that names it, not just its own plan item.
    """
    completed = {norm_code(c) for c in completed}
    consumed_slots = consumed_slots or set()
    gen_ed_overrides = gen_ed_overrides if isinstance(gen_ed_overrides, dict) else {}
    used: Set[str] = set()
    done_ids: Set[int] = set()
    done_with: Dict[int, str] = {}
    credits_done = 0.0
    total_credits = 0.0

    # code -> the (primary) requirement-type bucket the course actually
    # satisfied -- lets a caller label a completed course "Gen Ed" /
    # "Major requirement" / etc. the same way the Progress page's checklist
    # labels not-yet-taken ones (see recommend_semester's "category" pick
    # field). Deliberately the item's own _item_category, not any
    # also_satisfies extra -- one clear label per course, not every bucket
    # it happens to double-count into.
    code_categories: Dict[str, str] = {}
    # code -> whether the item it satisfied was an Entrance-to-Major
    # requirement, same reasoning as code_categories -- a not-yet-taken ETM
    # course already gets this from recommend_semester's own "etm" pick
    # field, but a completed one has no pick to read it from.
    code_etm: Dict[str, bool] = {}

    by_category: Dict[str, Dict[str, float]] = {}

    def _cat(name: str) -> Dict[str, float]:
        return by_category.setdefault(
            name, {"done_items": 0, "total_items": 0, "credits_done": 0.0, "total_credits": 0.0}
        )

    pattern_slots = []
    # type: "slot" items tagged with a SINGLE Gen Ed domain string (e.g.
    # "gen_ed": "GA") and not already done via consumed_slots -- collected
    # here, same pass as pattern_slots, so both can absorb leftover
    # completed courses further down. A slot naming a LIST of domains
    # ("gen_ed": ["GA", "GH"]) is deliberately excluded -- see the
    # absorption loop below and _item_category's docstring for why only a
    # single, unambiguous domain is safe to retroactively resolve.
    gen_ed_slots = []
    # Domains resolved via consumed_slots this call -- i.e. by a caller
    # (build_full_plan's own simulation loop, or apply_bulk_completion) that
    # marked a single-domain Gen Ed slot done WITHOUT telling plan_progress
    # which real course code resolved it (consumed_slots only ever carries
    # item ids). A course can be approved for more than one domain (e.g.
    # ART 116N is both GQ and GA) -- so a course the simulation genuinely
    # picked to satisfy one domain's slot can still be sitting in
    # `completed`/`leftovers` afterward, looking exactly like an untouched
    # leftover to a LATER, unrelated open slot of a DIFFERENT cross-listed
    # domain. Recorded here so the absorption pass below can refuse to
    # re-spend a leftover on any domain it overlaps with -- see that pass's
    # own comment for the full reasoning and the real regression this
    # guards (a 4-year ACCTG plan finishing a term early because an
    # Inter-Domain slot silently absorbed a course already spent on a
    # separate US-domain slot two terms earlier).
    consumed_gen_ed_domains: Set[str] = set()
    for sem, item in _iter_plan_items(plan):
        credits = float(item.get("credits") or 0)
        total_credits += credits
        # also_satisfies (set by merge_plans when a minor requirement widens
        # an existing major item instead of duplicating it, e.g. STAT 318
        # counting toward both a CMPSC major and a Statistics minor) tags
        # this item into an EXTRA category bucket on top of its normal one —
        # never present on any plan built before this feature, so this is a
        # pure no-op for every single-major request.
        cats = [_cat(_item_category(item))]
        for tag in item.get("also_satisfies", []):
            cats.append(_cat(tag))
        for cat in cats:
            cat["total_items"] += 1
            cat["total_credits"] += credits
        if item.get("type") == "course":
            hit = next((o for o in item["options"] if o in completed and o not in used), None)
            if hit:
                used.add(hit)
                done_ids.add(item["id"])
                done_with[item["id"]] = hit
                code_categories[hit] = _item_category(item)
                code_etm[hit] = bool(item.get("etm"))
                credits_done += credits
                for cat in cats:
                    cat["done_items"] += 1
                    cat["credits_done"] += credits
        else:
            if item["id"] in consumed_slots:
                done_ids.add(item["id"])
                credits_done += credits
                for cat in cats:
                    cat["done_items"] += 1
                    cat["credits_done"] += credits
                ge = item.get("gen_ed")
                if isinstance(ge, str) and ge:
                    consumed_gen_ed_domains.add(ge)
            elif item.get("match"):
                pattern_slots.append(item)
            elif isinstance(item.get("gen_ed"), str) and item.get("gen_ed"):
                gen_ed_slots.append(item)

    # Developmental/placement math codes (see expand_math_placement) are
    # excluded here even when a caller's `completed` includes them without a
    # matching plan item — they're either synthetic (a waiver, never really
    # taken) or, if genuinely completed, not the kind of course that
    # sensibly "counts as an elective" the way this list is described.
    leftovers = [
        c for c in sorted(completed)
        if c not in used and c not in NON_DEGREE_APPLICABLE_MATH and _math_placement_tier(c) is None
    ]
    for item in pattern_slots:
        rx = re.compile(item["match"])
        hit = next((c for c in leftovers if rx.match(c)), None)
        if hit:
            leftovers.remove(hit)
            done_ids.add(item["id"])
            done_with[item["id"]] = hit
            code_categories[hit] = _item_category(item)
            code_etm[hit] = bool(item.get("etm"))
            credits = float(item.get("credits") or 0)
            credits_done += credits
            cat = _cat(_item_category(item))
            cat["done_items"] += 1
            cat["credits_done"] += credits

    # Single-domain Gen Ed slots absorb a leftover completed course the
    # same way pattern_slots do just above -- a student who already took a
    # real Gen Ed course (transcript upload, "I took ART 116N" in chat,
    # apply_bulk_completion never having run this session, ...) should get
    # retroactive credit for it instead of it silently landing in
    # extra_courses just because it wasn't literally the course the
    # semester simulation would have picked.
    #
    # Deliberately runs AFTER pattern_slots, not before or interleaved: a
    # pattern_slot's regex (e.g. the CMPSC/CMPEN 4XX technical elective) is
    # a narrower, far more specific match than "any of the dozens-to-
    # hundreds of courses on one Gen Ed domain's approved list," so giving
    # pattern_slots first claim on a leftover means a course eligible for
    # both never gets snatched by the broader Gen Ed match first, starving
    # the more specific major requirement. The reverse order would let a
    # generic Gen Ed slot grab a course a technical-elective slot actually
    # needed, with no way to recover it (each completed course satisfies at
    # most one item -- see `used`/`leftovers` above).
    #
    # gen_ed_slots is walked in the same plan/semester order
    # _iter_plan_items() yields (it was appended in that order above), and
    # each slot takes the first-alphabetical matching leftover -- both
    # deterministic, not an arbitrary tie-break -- for the rare case where
    # one completed course's domains could satisfy more than one open slot,
    # or a plan has more than one open slot for the same domain.
    if gen_ed_slots:
        membership = _gen_ed_domain_membership()
        # Same major-vs-department derivation recommend_semester uses for
        # its own Firewall check (see that function's `major_dept`) --
        # plan_progress has no separate parameter for it since `plan`
        # already carries everything needed to derive it.
        major_dept = plan.get("major") if plan.get("major") in plan.get("departments", []) else None

        # For each leftover, which of THIS plan's currently-open
        # single-domain slots (gen_ed_slots -- fixed at this point, before
        # any of them get absorbed below) it could actually land on --
        # same domain-membership + Firewall + gen_ed_exclude + cross-listed-
        # consumed-domain checks the absorption loop below applies per slot,
        # just aggregated across every open slot for one course. A course
        # with 2+ entries here is a genuinely ambiguous one (mirrors
        # compute_gen_ed_detail's own ambiguousCourses definition); a
        # gen_ed_overrides entry only ever takes effect for one of those --
        # never for a course this plan doesn't have a real choice for, even
        # if the named domain happens to be technically valid globally.
        def _open_domains_for(code: str) -> Set[str]:
            opts: Set[str] = set()
            if membership.get(code, set()) & consumed_gen_ed_domains:
                return opts
            for slot_item in gen_ed_slots:
                d = slot_item["gen_ed"]
                if d not in membership.get(code, ()):
                    continue
                if d != "INTER-D" and major_dept and code.startswith(f"{major_dept} "):
                    continue
                if _blocked_as_inter_domain(code, d):
                    continue
                if code in {norm_code(c) for c in slot_item.get("gen_ed_exclude", [])}:
                    continue
                opts.add(d)
            return opts

        effective_overrides: Dict[str, str] = {}
        for code, domain in gen_ed_overrides.items():
            code = norm_code(code)
            domain = str(domain).strip().upper()
            if not code or not domain:
                continue
            open_domains = _open_domains_for(code)
            if len(open_domains) >= 2 and domain in open_domains:
                effective_overrides[code] = domain

        for item in gen_ed_slots:
            domain = item["gen_ed"]
            # Inter-Domain/Integrative Studies is exempt from the Firewall
            # rule by PSU policy -- same carve-out _pick_gen_ed_course
            # applies for forward-looking recommendations.
            firewall_exempt = domain == "INTER-D"
            # A major can narrow a domain further than the university-wide
            # list (item's own gen_ed_exclude, e.g. a course a department
            # handbook considers too similar to one its majors already
            # take) -- honored here too, same as recommend_semester's own
            # slot_exclude, so a course that could never have been
            # RECOMMENDED for this slot doesn't get retroactively credited
            # to it either.
            slot_exclude = {norm_code(c) for c in item.get("gen_ed_exclude", [])}
            hit = next(
                (
                    c for c in leftovers
                    if domain in membership.get(c, ())
                    # A leftover cross-listed into some OTHER domain that
                    # was already resolved via consumed_slots (opaquely --
                    # no code attached) is presumed to be the very course
                    # that resolved it, not a genuinely untouched leftover
                    # -- see consumed_gen_ed_domains' own comment above.
                    and not (membership.get(c, ()) & consumed_gen_ed_domains)
                    and c not in slot_exclude
                    and (firewall_exempt or not (major_dept and c.startswith(f"{major_dept} ")))
                    and not _blocked_as_inter_domain(c, domain)
                    # A genuinely ambiguous leftover with a validated
                    # gen_ed_overrides entry may only land on ITS named
                    # domain's slot -- every other course (no entry, an
                    # invalid one, or not ambiguous here) matches exactly
                    # as before.
                    and (c not in effective_overrides or effective_overrides[c] == domain)
                ),
                None,
            )
            if hit:
                leftovers.remove(hit)
                done_ids.add(item["id"])
                done_with[item["id"]] = hit
                code_categories[hit] = _item_category(item)
                code_etm[hit] = bool(item.get("etm"))
                credits = float(item.get("credits") or 0)
                credits_done += credits
                cat = _cat(_item_category(item))
                cat["done_items"] += 1
                cat["credits_done"] += credits

        # PSU's Cultural Diversity requirement (US/IL) rides on top of the
        # 45-credit Foundations/Knowledge-Domain/Integrative/Exploration
        # structure rather than competing with it for its own dedicated
        # course -- confirmed live against PSU's own real course-search
        # tool (a course tagged both Inter-Domain and US, e.g. AGBM 170N,
        # offers "Add to all requirements above" crediting Inter-Domain AND
        # US from ONE enrollment) and PSU's advising FAQ ("even if a course
        # has both Inter-Domain and [other] designations, the same
        # credits... can only be applied to one requirement" -- singular
        # "one requirement" there means Inter-Domain vs. that OTHER
        # designation, not Inter-Domain vs. US, which the tool's own UI
        # explicitly stacks). So a course already resolved to one gen_ed
        # slot above that ALSO carries a US and/or IL tag can additionally
        # close a SEPARATE still-open US/IL slot -- and, symmetric to that,
        # a course already resolved to a US/IL slot that's ALSO tagged
        # Inter-Domain/Knowledge-Domain/Exploration can additionally close
        # one of THOSE if it's still open. Which direction the primary loop
        # above happened to claim first depends only on plan order (it
        # walks gen_ed_slots and takes the first eligible leftover per
        # slot) -- e.g. ACCTG's own plan lists a US slot in semester 1 and
        # its Inter-Domain slots much later, so AGBM 170N (GN/GS/INTER-D/US)
        # resolves the US slot directly up there, leaving Inter-Domain
        # still open for THIS pass to close. Never spending a second
        # leftover course on either.
        #
        # Deliberately never touches `used`/`leftovers` -- the course isn't
        # consumed AGAIN, just additionally credited -- so this can't
        # reintroduce the regression consumed_gen_ed_domains guards above
        # (that was ambiguous DOMAIN GUESSING for a single, first-time
        # consumption; this is an explicit second credit off a resolution
        # that already happened).
        #
        # Same FAQ, elsewhere: "a course that lists 'US/IL' will only apply
        # toward one or the other... not both" -- cultural_used enforces
        # exactly that: a code only ever picks up ONE Cultural Diversity
        # credit (US or IL) this way, on top of whatever non-cultural
        # domain it already resolved (or vice versa) -- never both a US and
        # an IL slot, and never two non-cultural slots (the `!=` check
        # below requires exactly one side of any new pairing to be
        # cultural). extended enforces the other half: a code gets AT MOST
        # ONE extra credit here on top of its one primary resolution --
        # never a second (e.g. AGBM 170N closing BOTH of ACCTG's two open
        # Inter-Domain slots, which would double-count a single 3-credit
        # enrollment against 6 credits of distinct requirement).
        resolved_domain_by_code: Dict[str, str] = {
            done_with[item["id"]]: item["gen_ed"]
            for item in gen_ed_slots
            if item["id"] in done_ids and done_with.get(item["id"])
        }
        extended: Set[str] = set()
        for item in gen_ed_slots:
            domain = item["gen_ed"]
            if item["id"] in done_ids:
                continue
            domain_cultural = domain in ("US", "IL")
            slot_exclude = {norm_code(c) for c in item.get("gen_ed_exclude", [])}
            # Same Firewall rule the primary loop applies per-domain --
            # exempt only when the slot BEING FILLED is itself INTER-D,
            # regardless of which domain the candidate course originally
            # resolved.
            firewall_exempt = domain == "INTER-D"
            candidate = next(
                (
                    code for code in sorted(resolved_domain_by_code)
                    if domain in membership.get(code, ())
                    # Exactly one side of the pairing must be Cultural
                    # Diversity -- two non-cultural domains never stack,
                    # and (via `extended` below) neither do US+IL.
                    and domain_cultural != (resolved_domain_by_code[code] in ("US", "IL"))
                    and code not in extended
                    and code not in slot_exclude
                    and (firewall_exempt or not (major_dept and code.startswith(f"{major_dept} ")))
                ),
                None,
            )
            if candidate:
                # Marks this box satisfied (done_ids/done_with -- truthful:
                # the content requirement genuinely IS met) but deliberately
                # does NOT add its credits to credits_done, here or in
                # cat["credits_done"]. Those credits were already counted
                # once, off the course's PRIMARY resolution -- adding them
                # again here would report more real credit-hours earned
                # than the student actually has (confirmed against a real
                # plan: a single 3-credit course must never inflate the
                # overall "credits earned" stat to 6). PSU's total degree
                # credit requirement doesn't shrink just because two
                # requirement rows share one course -- the credit-hours
                # this box's own slot would have needed still have to come
                # from some OTHER real course the student takes, same as a
                # real degree audit tracks "requirement satisfied" and
                # "total credits earned" as two separate, non-double-
                # counted things.
                done_ids.add(item["id"])
                done_with[item["id"]] = candidate
                code_categories.setdefault(candidate, _item_category(item))
                code_etm.setdefault(candidate, bool(item.get("etm")))
                extended.add(candidate)
                cat = _cat(_item_category(item))
                cat["done_items"] += 1

    for cat in by_category.values():
        cat["credits_done"] = round(cat["credits_done"], 1)
        cat["total_credits"] = round(cat["total_credits"], 1)
        cat["percent"] = round(100 * cat["credits_done"] / cat["total_credits"]) if cat["total_credits"] else 0

    total_items = sum(1 for _ in _iter_plan_items(plan))

    return {
        "done_ids": done_ids,
        "done_with": done_with,
        "done_items": len(done_ids),
        "total_items": total_items,
        "credits_done": round(credits_done, 1),
        "total_credits": round(total_credits, 1),
        "extra_courses": leftovers,  # completed courses that don't map to the plan
        "by_category": by_category,
        "code_categories": code_categories,
        "code_etm": code_etm,
    }


def compute_gen_ed_detail(
    plan: Dict[str, Any],
    completed: Set[str],
    progress: Dict[str, Any],
    gen_ed_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Additive, display-only structure for the Gen Ed browsing/override UI --
    built entirely by DESCRIBING an already-resolved `progress` (the return
    value of plan_progress(), called with this same gen_ed_overrides so the
    two stay consistent), never by re-deriving or second-guessing it. Two
    pieces:

    "slots" -- every Gen Ed plan item (single-domain or multi-domain choice
    alike, mirroring exactly which items _item_category treats as Gen Ed --
    any item with a non-empty "gen_ed") with its real id/label/credits and
    whether/how progress resolved it.

    "ambiguousCourses" -- completed courses that have a genuine choice of
    domain IN THIS PLAN: approved (per _gen_ed_domain_membership) for 2+
    domains this plan has an open single-domain slot for. "Open" here means
    open absent this course's own placement -- a course currently credited
    to one of its own eligible domains still lists that domain (its slot is
    "done" precisely because this course filled it), plus any OTHER eligible
    domain that still has a genuinely open slot elsewhere in the plan.
    """
    done_ids: Set[int] = progress["done_ids"]
    done_with: Dict[int, str] = progress["done_with"]
    membership = _gen_ed_domain_membership()
    titles = _gen_ed_course_titles()
    major_dept = plan.get("major") if plan.get("major") in plan.get("departments", []) else None

    single_items: List[Dict[str, Any]] = []
    slots_out: List[Dict[str, Any]] = []
    for _sem, item in _iter_plan_items(plan):
        # Mirrors _item_category exactly: a "course" item (e.g. MATH 140
        # tagged "gen_ed": "GQ" -- PSU itself double-counts some required
        # courses into Gen Ed) is always "major", never "gen_ed", regardless
        # of its own gen_ed tag -- see that function's docstring. Only a
        # non-course ("slot") item's gen_ed tag describes a real, generic
        # Gen Ed box a leftover completed course can retroactively fill;
        # plan_progress's own gen_ed_slots collection applies this identical
        # exclusion (its course-item branch never touches gen_ed at all), so
        # skipping it here too keeps this function truthful to what
        # plan_progress actually resolved.
        if item.get("type") == "course":
            continue
        ge = item.get("gen_ed")
        if not ge:
            continue
        domains = [ge] if isinstance(ge, str) else list(ge)
        slots_out.append({
            "id": item["id"],
            "label": item.get("label") or "",
            "domains": domains,
            "is_choice": len(domains) > 1,
            "credits": float(item.get("credits") or 0),
            "done": item["id"] in done_ids,
            "satisfied_by": done_with.get(item["id"]),
        })
        if isinstance(ge, str) and ge:
            single_items.append(item)

    # domain -> the still-open (not-done) single-domain slot items for it.
    open_by_domain: Dict[str, List[Dict[str, Any]]] = {}
    for item in single_items:
        if item["id"] not in done_ids:
            open_by_domain.setdefault(item["gen_ed"], []).append(item)

    def _domain_open_for(code: str, domain: str) -> bool:
        firewall_exempt = domain == "INTER-D"
        if _blocked_as_inter_domain(code, domain):
            return False
        for slot_item in open_by_domain.get(domain, ()):
            if code in {norm_code(c) for c in slot_item.get("gen_ed_exclude", [])}:
                continue
            if not firewall_exempt and major_dept and code.startswith(f"{major_dept} "):
                continue
            return True
        return False

    # code -> the domain of the single-domain slot it's CURRENTLY credited
    # to, if any (done_with only ever carries a code for a single-domain
    # gen_ed item here -- a multi-domain choice slot resolved via
    # consumed_slots never gets a done_with entry, see plan_progress above).
    current_domain_of: Dict[str, str] = {}
    for item in single_items:
        code = done_with.get(item["id"])
        if code:
            current_domain_of[code] = item["gen_ed"]

    # Both current_domain_of and extra_courses are already derived FROM
    # `completed` inside plan_progress, so this intersection is a no-op in
    # the normal case -- kept as a defensive guard against `progress` ever
    # being computed against a different completed set than the one passed
    # in here (see the api_plan() call site, which always passes the same
    # `completed_for_planning` to both).
    completed_norm = {norm_code(c) for c in completed}
    candidates = (set(current_domain_of) | {
        c for c in progress.get("extra_courses", []) if c in membership
    }) & completed_norm

    ambiguous_out: List[Dict[str, Any]] = []
    for code in sorted(candidates):
        own_domain = current_domain_of.get(code)
        eligible = {d for d in membership.get(code, ()) if _domain_open_for(code, d)}
        if own_domain:
            eligible.add(own_domain)
        if len(eligible) < 2:
            continue
        # own_domain is the real, already-resolved outcome when this course
        # is actually credited somewhere; a still-unmatched leftover (rare --
        # only possible when a competing course won every one of its
        # eligible slots first) has no real outcome to report, so this falls
        # back to a deterministic placeholder rather than guessing which
        # slot the absorption pass would have picked.
        current = own_domain or sorted(eligible)[0]
        ambiguous_out.append({
            "code": code,
            "name": titles.get(code, ""),
            "eligible_domains": sorted(eligible),
            "current_domain": current,
        })

    return {"slots": slots_out, "ambiguous_courses": ambiguous_out}


# ---------------------------------------------------------------------------
# Next-semester recommendation
# ---------------------------------------------------------------------------

def _ranked_options(
    item: Dict[str, Any],
    catalog: Dict[str, Course],
    exclude: Set[str],
    completed: Set[str],
    preferred: Optional[Dict[str, int]] = None,
):
    """Every option for a course item, in preference order: catalog-present
    and not-yet-completed first, then not-yet-completed, then catalog-present,
    then anything not excluded (e.g. not offered in summer). Each option
    appears once, in its best tier.

    `completed` (already-earned courses) is de-prioritized, not excluded: two
    items that share an option pool (e.g. two "ENGL 15 or CAS 100A/B" writing
    boxes) must land on two distinct courses instead of both perpetually
    recommending whichever option was earned first — that starved the other
    item forever since a course, once completed, can't satisfy a second item
    (see plan_progress's one-completed-course-per-item rule).

    `preferred` breaks ties WITHIN a tier (stable — otherwise-equal options
    keep their original relative order): a code -> priority map (lower
    sorts first; a code absent from the map sorts last, see
    _codes_needed_as_prereqs) tied to whether some OTHER still-outstanding
    item in the plan needs it as a prereq/concurrent course. This is what
    lets a minor's own hidden-prereq chain resolve for free when it happens
    to share an "any one of these" pool with the major (e.g. a major's
    generic intro-programming item listing CMPSC 101/121/131/200/201, when
    a minor elsewhere needs specifically CMPSC 131 or CMPSC 121 to unlock
    its own next course) instead of the pool defaulting to whichever option
    is listed first and leaving the minor's own chain permanently stuck —
    see merge_plans' docstring for the fuller story of why this collision
    happens in the first place.
    """
    options = item.get("options", [])
    preferred = preferred or {}
    tiers = [
        [o for o in options if o in catalog and o not in exclude and not _is_effectively_completed(o, completed)],
        [o for o in options if o not in exclude and not _is_effectively_completed(o, completed)],
        [o for o in options if o in catalog and o not in exclude],
        [o for o in options if o not in exclude],
    ]
    seen: Set[str] = set()
    for tier in tiers:
        for o in sorted(tier, key=lambda o: preferred.get(o, 2)):
            if o not in seen:
                seen.add(o)
                yield o


def _codes_needed_as_prereqs(
    plan: Dict[str, Any], catalog: Dict[str, Course], done_ids: Set[int],
) -> Dict[str, int]:
    """Priority map for the `preferred` tie-breaker above: 0 for a code that
    is the SOLE option in some other still-outstanding item's own
    prereq/concurrent group (a hard, no-alternative requirement — e.g. a
    course whose only enforced concurrent option is MATH 140), 1 for a code
    that's merely one of several OR'd alternatives elsewhere (soft —
    picking it isn't uniquely necessary, some other alternative could
    satisfy that same requirement instead), and no entry (2, via .get's
    default) for a code nothing downstream needs at all. A hard requirement
    always wins a tie over a soft one — the reverse of MATH 140 losing to
    MATH 110 in a "some group merely mentions it" pass would silently
    reintroduce the exact bug this mechanism exists to fix. Only options
    belonging to NOT-YET-DONE items count: once an item is satisfied,
    nothing further needs to prefer unlocking it."""
    priority: Dict[str, int] = {}

    def note(code: str, tier: int) -> None:
        if priority.get(code, 2) > tier:
            priority[code] = tier

    for _, item in _iter_plan_items(plan):
        if item["id"] in done_ids or item.get("type") != "course":
            continue
        for code in item.get("options", []):
            course = catalog.get(code)
            if not course:
                continue
            for g in list(course.prereq_groups) + list(course.concurrent_groups):
                tier = 0 if len(g) == 1 else 1
                for c in g:
                    note(norm_code(c), tier)
    return priority


def _pick_option(
    item: Dict[str, Any],
    catalog: Dict[str, Course],
    exclude: Optional[Set[str]] = None,
    completed: Optional[Set[str]] = None,
    preferred: Optional[Set[str]] = None,
) -> Optional[str]:
    """Preferred option for a course item — see _ranked_options for the
    preference order. Used where only a label/index is needed, not
    eligibility (recommend_semester's scan_once checks each ranked option's
    prereqs individually instead of committing to just this first pick)."""
    return next(_ranked_options(item, catalog, exclude or set(), completed or set(), preferred), None)


def _item_credits(item: Dict[str, Any], code: Optional[str], catalog: Dict[str, Course]) -> float:
    if code and code in catalog and catalog[code].credits is not None:
        return float(catalog[code].credits)
    return float(item.get("credits") or 3.0)


def recommend_semester(
    plan: Dict[str, Any],
    catalog: Dict[str, Course],
    completed: Set[str],
    *,
    consumed_slots: Optional[Set[int]] = None,
    max_credits: Optional[float] = None,
    include_slots: bool = True,
    exclude_codes: Optional[Set[str]] = None,
    excluded_codes: Optional[Set[str]] = None,
    preferred_codes: Optional[Set[str]] = None,
    gen_ed_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Pick the best prereq-safe course load for one semester.

    Walks the flowchart in semester order; a course is chosen when its enforced
    prereqs are completed and concurrent requirements are met by completed or
    same-term picks. Slots (GEN ED etc.) fill the remaining credit budget.
    exclude_codes skips specific courses (e.g. not offered in summer);
    excluded_codes is unioned into the same skip set but carries a distinct
    reason — courses the student explicitly said they don't want, so they're
    never picked even when otherwise eligible. preferred_codes are courses
    the student explicitly asked for: threaded into _ranked_options' own
    `preferred` tie-break so a wanted course wins ties within a shared option
    pool (e.g. an "either A or B" requirement slot) — it never bypasses
    eligibility (prereqs/concurrent/exclusion checks still run as normal),
    it only affects which otherwise-tied option is picked first. Pass an
    already math-placement-expanded `completed` (see expand_math_placement)
    for waivers to apply here too. gen_ed_overrides is passed straight
    through to plan_progress's own Gen Ed retroactive-matching pass -- see
    that function's docstring.
    """
    completed = {norm_code(c) for c in completed}
    consumed_slots = consumed_slots or set()
    exclude_codes = {norm_code(c) for c in (exclude_codes or set())} | {
        norm_code(c) for c in (excluded_codes or set())
    }
    max_credits = float(max_credits or plan.get("max_credits_per_semester") or 17)
    depts = plan.get("departments", [])
    major_dept = plan.get("major") if plan.get("major") in depts else None

    progress = plan_progress(
        plan, completed, consumed_slots=consumed_slots, gen_ed_overrides=gen_ed_overrides,
    )
    done_ids = progress["done_ids"]
    # Computed once per call, not per item — see _ranked_options' docstring
    # for why this is what lets a multi-option pool (e.g. a major's generic
    # "any intro programming course" slot) resolve to whichever option a
    # minor elsewhere actually needs, instead of an arbitrary default.
    needed_codes = _codes_needed_as_prereqs(plan, catalog, done_ids)
    if preferred_codes:
        # A student-requested course wins ties within a shared option pool:
        # merged into the same priority map _codes_needed_as_prereqs builds
        # (0 = highest priority) rather than a separate mechanism, since
        # _ranked_options only understands one `preferred` map. Uses min()
        # so this can only ever raise a code's priority, never demote one
        # that's already a hard downstream requirement (tier 0).
        needed_codes = dict(needed_codes)
        for code in {norm_code(c) for c in preferred_codes}:
            needed_codes[code] = min(needed_codes.get(code, 2), 0)

    picks: List[Dict[str, Any]] = []
    picked_ids: Set[int] = set()
    picked_codes: Set[str] = set()

    def current_load() -> float:
        return sum(p["credits"] for p in picks)

    def scan_once() -> bool:
        """Pick the first eligible item in strict flowchart order; True if one was added.

        Courses and slots are considered together so a freshman's first term
        looks like the flowchart's semester 1 (incl. GEN ED) instead of
        cramming later courses in first.
        """
        # This plan's still-open (not done, not yet picked THIS call) single-
        # domain US/IL slots -- recomputed fresh each scan_once() call since
        # picked_ids grows with every item the outer loop has picked so far.
        # Passed as bonus_domains below so a course recommended for some
        # OTHER open domain that's ALSO US/IL-tagged gets preferred over an
        # arbitrary first-eligible pick, and see the post-pick block further
        # down for actually closing that second slot from the same pick.
        open_cultural_domains: Set[str] = {
            it["gen_ed"]
            for _s, it in _iter_plan_items(plan)
            if it["id"] not in done_ids and it["id"] not in picked_ids
            and isinstance(it.get("gen_ed"), str) and it["gen_ed"] in ("US", "IL")
        }

        for sem, item in _iter_plan_items(plan):
            if item["id"] in done_ids or item["id"] in picked_ids:
                continue

            if item.get("type") == "slot":
                if not include_slots:
                    continue
                gen_ed_domain = item.get("gen_ed")
                if gen_ed_domain:
                    # A slot can name one domain ("GA") or a small preference
                    # list ("GA/GH" combined slots) — try each in order and
                    # use the first that yields an eligible course.
                    domains = [gen_ed_domain] if isinstance(gen_ed_domain, str) else list(gen_ed_domain)
                    # Some majors narrow a Gen Ed domain further than the
                    # university-wide list — e.g. a department handbook
                    # excluding specific courses in that domain it considers
                    # too similar to a course its own majors already take,
                    # or too elementary. A plan item opts into this via its
                    # own gen_ed_exclude list; every other major's slots leave it
                    # unset and see no behavior change.
                    slot_exclude = {norm_code(c) for c in item.get("gen_ed_exclude", [])}
                    pick = None
                    matched_domain = None
                    for d in domains:
                        pick = _pick_gen_ed_course(
                            d, catalog, major_dept, completed,
                            completed | picked_codes | slot_exclude | exclude_codes,
                            preferred_codes=preferred_codes,
                            bonus_domains=open_cultural_domains if d not in ("US", "IL") else None,
                        )
                        if pick:
                            matched_domain = d
                            break
                    if pick:
                        code, title, ge_credits = pick
                        # The slot's own declared credits (e.g. 1.5 for a
                        # GHW half-credit term) reflect the real bulletin
                        # plan's per-term total and take priority over the
                        # picked course's own credit count, which was never
                        # part of that calibration.
                        if item.get("credits"):
                            ge_credits = float(item["credits"])
                        if current_load() + ge_credits > max_credits + 0.25:
                            continue
                        picked_ids.add(item["id"])
                        picked_codes.add(code)
                        reason = f"Semester {sem['index']} {item.get('label', 'Gen Ed')} requirement — satisfies {matched_domain}."
                        # Purely informational: if this recommended course
                        # is ALSO Cultural Diversity-tagged (US/IL), say so
                        # in the reason -- but deliberately does NOT mark
                        # any other slot done or skip recommending it a
                        # course. PSU's total degree credit requirement
                        # doesn't shrink just because two requirement rows
                        # end up sharing one course, so the credit-hours a
                        # separate US/IL slot needs still have to come from
                        # some real course; only plan_progress's own
                        # retroactive pass (run against courses the student
                        # has ACTUALLY completed, not simulated future
                        # picks) is positioned to know whether that other
                        # slot ends up genuinely needing a course of its
                        # own once this one is really taken. This note just
                        # tells the student the extra value up front,
                        # before that.
                        if matched_domain not in ("US", "IL") and open_cultural_domains:
                            cultural_hit = _gen_ed_domain_membership().get(code, set()) & open_cultural_domains
                            if cultural_hit and not (major_dept and code.startswith(f"{major_dept} ")):
                                names = " and ".join(sorted(cultural_hit))
                                reason += f" Bonus: also approved for {names} Cultural Diversity."
                        picks.append({
                            "item_id": item["id"],
                            "code": code,
                            "name": title,
                            "credits": ge_credits,
                            "type": "course",
                            "flowchart_semester": sem["index"],
                            "etm": False,
                            "unlocks": 0,
                            "options": [],
                            "reason": reason,
                            "category": _item_category(item),
                        })
                        return True
                elif item.get("open_elective"):
                    # A "pick almost anything except this denylist"
                    # requirement (Department List / Supporting Course /
                    # Technical Elective) — see _pick_open_elective's
                    # docstring. Falls through to the generic unfilled
                    # placeholder below if nothing in the plan's own loaded
                    # departments is eligible, same graceful-degradation
                    # behavior as a Gen Ed domain slot that can't find a pick.
                    pick = _pick_open_elective(
                        catalog, completed, completed | picked_codes | exclude_codes,
                        min_level=item.get("elective_min_level"),
                        max_level=item.get("elective_max_level"),
                        exclude_exact=item.get("elective_exclude"),
                        exclude_prefixes=item.get("elective_exclude_prefixes"),
                        prefer_prefixes=item.get("elective_prefer"),
                    )
                    if pick:
                        code, title, oe_credits = pick
                        if item.get("credits"):
                            oe_credits = float(item["credits"])
                        if current_load() + oe_credits > max_credits + 0.25:
                            continue
                        picked_ids.add(item["id"])
                        picked_codes.add(code)
                        picks.append({
                            "item_id": item["id"],
                            "code": code,
                            "name": title,
                            "credits": oe_credits,
                            "type": "course",
                            "flowchart_semester": sem["index"],
                            "etm": False,
                            "unlocks": 0,
                            "options": [],
                            "reason": f"Semester {sem['index']} {item.get('label', 'Elective')} requirement — an eligible course.",
                            "category": _item_category(item),
                        })
                        return True
                credits = float(item.get("credits") or 3.0)
                if current_load() + credits > max_credits + 0.25:
                    continue
                picked_ids.add(item["id"])
                picks.append({
                    "item_id": item["id"],
                    "code": None,
                    "name": item.get("label", "Elective"),
                    "credits": credits,
                    "type": "slot",
                    "flowchart_semester": sem["index"],
                    "etm": False,
                    "unlocks": 0,
                    "options": [],
                    "reason": f"Semester {sem['index']} requirement slot — pick any course satisfying it.",
                    "category": _item_category(item),
                })
                return True

            # Try every option in preference order, not just the first-ranked
            # one — an item like "CMPSC 101 (or 203)" must fall through to
            # 203 when 101 is prereq-blocked (e.g. by a MATH 110 track that
            # doesn't satisfy 101's specific MATH 140/141 requirement)
            # instead of leaving the whole item permanently unscheduled just
            # because its first-listed option isn't eligible yet.
            code = None
            credits = 0.0
            for candidate in _ranked_options(item, catalog, exclude_codes, completed | picked_codes, needed_codes):
                cand_credits = _item_credits(item, candidate, catalog)
                if current_load() + cand_credits > max_credits + 0.25:
                    continue
                cand_course = catalog.get(candidate)
                if cand_course:
                    if not prereqs_satisfied(cand_course, completed):
                        continue
                    if not concurrent_satisfied(cand_course, completed | picked_codes):
                        continue
                    if not excludes_satisfied(cand_course, completed):
                        continue
                code, credits = candidate, cand_credits
                break
            if not code:
                continue
            reason_bits = [f"Semester {sem['index']} on the {plan.get('major', '')} flowchart"]
            if item.get("etm"):
                reason_bits.append("Entrance-to-Major requirement")
            unlocks = unlock_count(code, depts)
            if unlocks:
                reason_bits.append(f"unlocks {unlocks} future course{'s' if unlocks != 1 else ''}")
            picked_ids.add(item["id"])
            picked_codes.add(code)
            picks.append({
                "item_id": item["id"],
                "code": code,
                "name": catalog[code].name if code in catalog else item.get("label", code),
                "credits": credits,
                "type": "course",
                "flowchart_semester": sem["index"],
                "etm": bool(item.get("etm")),
                "unlocks": unlocks,
                "options": item.get("options", []),
                "category": _item_category(item),
                "reason": "; ".join(reason_bits) + ".",
            })
            return True
        return False

    # Re-scan after each pick so same-term concurrent pairs resolve
    # (e.g. CMPSC 131 needs MATH 140 at least concurrently).
    guard = 0
    while scan_once():
        guard += 1
        if guard > 500:  # defensive; plan files are small
            break

    picks.sort(key=lambda p: (p["flowchart_semester"], p["type"] == "slot", -(p["unlocks"] or 0), p["item_id"]))

    # Explain the nearest blocked courses so students know what they're working toward.
    blocked: List[Dict[str, Any]] = []
    for sem, item in _iter_plan_items(plan):
        if item["id"] in done_ids or item["id"] in picked_ids or item.get("type") != "course":
            continue
        code = _pick_option(item, catalog)
        course = catalog.get(code) if code else None
        if course:
            miss = missing_prereqs(course, completed | picked_codes)
            conflict = exclusion_conflict(course, completed | picked_codes)
            if miss or conflict:
                entry = {
                    "code": code,
                    "name": course.name,
                    "flowchart_semester": sem["index"],
                    "missing": [" or ".join(g) for g in miss],
                }
                if conflict:
                    entry["excludedBy"] = sorted(conflict)
                blocked.append(entry)
        if len(blocked) >= 4:
            break

    return {
        "courses": picks,
        "total_credits": round(sum(p["credits"] for p in picks), 1),
        "blocked": blocked,
        "progress": progress,
    }


# ---------------------------------------------------------------------------
# Weighted recommendation scoring
# ---------------------------------------------------------------------------

# Courses excluded from recommendations unless the student asks for them.
# first[- ]year seminar covers real Gen Ed titles in both spellings ("First-
# Year Seminar in African American Studies", "Asian Studies First Year
# Seminar", ...) -- orientation-style courses that shouldn't be picked to
# fill a Gen Ed domain or open elective slot just because they're on the
# approved list.
_EXCLUDE_NAME_RE = re.compile(
    r"special topics|internship|independent stud|thesis|foreign stud|"
    r"individual stud|practicum|co-?op experience|first[- ]year seminar",
    re.IGNORECASE,
)

# Interest keywords students mention -> terms matched against course name/description.
INTEREST_TERMS: Dict[str, List[str]] = {
    "software engineering": ["software engineering", "software design"],
    "internship": ["internship"],
    "web": ["web"],
    "security": ["security", "cryptography"],
    "ai": ["artificial intelligence", "machine learning"],
    "machine learning": ["machine learning", "artificial intelligence"],
    "data science": ["data science", "data analysis", "statistics"],
    "systems": ["operating systems", "computer systems", "architecture"],
    "networks": ["network", "communication"],
    "graphics": ["graphics", "visual"],
    "game": ["game"],
    "databases": ["database"],
    "theory": ["theory", "algorithms", "computability"],
}

SCORE_BASE_ELIGIBLE = 50
SCORE_ON_FLOWCHART = 100
SCORE_NEXT_BLOCK = 40
SCORE_CORE = 30
SCORE_PER_UNLOCK = 5
SCORE_UNLOCK_CAP = 40
SCORE_INTEREST = 20
SCORE_WANTED = 60
SCORE_MULTI_GEN_ED = 30
PENALTY_SPECIAL = -40


def extract_interests(prompt: str) -> List[str]:
    p = (prompt or "").lower()
    return [k for k in INTEREST_TERMS if k in p]


def _plan_course_index(
    plan: Dict[str, Any],
    catalog: Dict[str, Course],
    done_ids: Set[int],
) -> Tuple[Dict[str, int], Set[str]]:
    """(preferred option -> flowchart semester) for OPEN items, plus every
    option code of already-satisfied items (so alternates like legacy
    CMPSC 121 after CMPSC 131 are never recommended)."""
    idx: Dict[str, int] = {}
    satisfied: Set[str] = set()
    for sem, item in _iter_plan_items(plan):
        if item.get("type") != "course":
            continue
        if item["id"] in done_ids:
            satisfied.update(item.get("options", []))
            continue
        code = _pick_option(item, catalog)
        if code:
            idx.setdefault(code, sem["index"])
    return idx, satisfied


def score_recommendations(
    plan: Dict[str, Any],
    catalog: Dict[str, Course],
    completed: Set[str],
    *,
    interests: Optional[List[str]] = None,
    max_credits: Optional[float] = None,
    top_n: Optional[int] = None,
    wanted_codes: Optional[Set[str]] = None,
    excluded_codes: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Deterministic weighted ranking of every eligible course.

    Eligibility (prereqs) is decided here in Python; scores explain priority.
    The LLM never sees ineligible courses and cannot alter scores.

    top_n defaults to a full semester's worth of courses rather than a fixed
    number: it's derived from the student's real max_credits_per_semester
    setting (same default-resolution as recommend_semester's max_credits --
    an explicit override, else the plan's own per-major value, else 17) at
    ~3 credits/course, so a 15-credit max recommends ~5 courses and an
    18-credit max recommends ~6. Pass an explicit top_n to override.

    wanted_codes: courses the student said they want, boosted (SCORE_WANTED)
    when they show up in this ranking. This only affects the score of a
    course that already made it past every eligibility check below (prereqs,
    concurrent, exclusions) — it can never force an ineligible course into
    the results, only rank an eligible-but-not-yet-picked one higher.

    excluded_codes: courses the student explicitly said they don't want.
    Same semantics as recommend_semester's excluded_codes -- a hard filter,
    not a de-prioritization, so an excluded course never appears in the
    ranked output at all.
    """
    completed = {norm_code(c) for c in completed}
    interests = interests or []
    wanted_codes = {norm_code(c) for c in (wanted_codes or set())}
    excluded_codes = {norm_code(c) for c in (excluded_codes or set())}
    if top_n is None:
        effective_max_credits = float(max_credits or plan.get("max_credits_per_semester") or 17)
        top_n = max(1, round(effective_max_credits / 3))
    depts = plan.get("departments", [])
    major_dept = plan.get("major") if plan.get("major") in depts else None
    progress = plan_progress(plan, completed)
    flowchart_idx, satisfied_options = _plan_course_index(
        plan, catalog, progress["done_ids"]
    )
    slot_patterns = [
        re.compile(item["match"])
        for _, item in _iter_plan_items(plan)
        if item.get("type") == "slot" and item.get("match")
    ]

    # Which Gen Ed domains this plan still has a genuinely open (not yet
    # done) single-domain slot for -- a course approved for BOTH a still-
    # open Cultural Diversity (US/IL) domain and a still-open non-cultural
    # one can satisfy two open requirement rows from one enrollment (see
    # plan_progress's own matching pass, confirmed against PSU's real
    # course-search tool), which is worth flagging as a real efficiency
    # signal below rather than scoring it the same as any other eligible
    # course. Only computed as two sets when there's genuinely a domain on
    # each side to pair -- most plans, at any given point, don't have both
    # kinds open at once, and membership lookups are wasted work then.
    open_gen_ed_domains: Set[str] = {
        it["gen_ed"]
        for _s, it in _iter_plan_items(plan)
        if it["id"] not in progress["done_ids"]
        and isinstance(it.get("gen_ed"), str) and it.get("gen_ed")
    }
    open_cultural_domains = open_gen_ed_domains & {"US", "IL"}
    open_noncultural_domains = open_gen_ed_domains - {"US", "IL"}
    multi_gen_ed_membership = (
        _gen_ed_domain_membership() if (open_cultural_domains and open_noncultural_domains) else {}
    )

    # Next expected flowchart semester = earliest semester with an incomplete course item.
    next_block = None
    for sem, item in _iter_plan_items(plan):
        if item.get("type") == "course" and item["id"] not in progress["done_ids"]:
            next_block = sem["index"]
            break

    wants_special = any(k in interests for k in ("internship",))

    results: List[Dict[str, Any]] = []
    for code, course in catalog.items():
        if code in completed:
            continue
        if code in excluded_codes:
            continue  # student explicitly said they don't want this course
        if code in satisfied_options:
            continue  # an alternate already covered this requirement
        if not prereqs_satisfied(course, completed):
            continue
        if not concurrent_satisfied(course, completed):
            continue
        if not excludes_satisfied(course, completed):
            continue

        is_special = bool(_EXCLUDE_NAME_RE.search(course.name or ""))
        if is_special and not wants_special:
            continue

        score = SCORE_BASE_ELIGIBLE
        reasons = ["You satisfy its prerequisites."]

        on_flowchart = code in flowchart_idx
        fills_slot = any(rx.match(code) for rx in slot_patterns)
        if on_flowchart:
            score += SCORE_ON_FLOWCHART
            reasons.append("It appears in the official major flowchart.")
            if next_block is not None and flowchart_idx[code] == next_block:
                score += SCORE_NEXT_BLOCK
                reasons.append(f"It belongs to your next flowchart semester (semester {next_block}).")
            score += SCORE_CORE
        elif fills_slot:
            score += SCORE_CORE
            reasons.append("It can fill a required elective slot on your flowchart.")

        unlocks = unlock_count(code, depts)
        if unlocks:
            bonus = min(SCORE_UNLOCK_CAP, SCORE_PER_UNLOCK * unlocks)
            score += bonus
            if unlocks >= 3:
                reasons.append(f"It unlocks {unlocks} future courses.")

        if open_cultural_domains and open_noncultural_domains:
            doms = multi_gen_ed_membership.get(code, set())
            # Firewall applies per-domain, same rule _pick_gen_ed_course
            # itself uses -- INTER-D is exempt, every other domain (US/IL
            # included) is not, regardless of which side of the pairing
            # it's on.
            firewalled = bool(major_dept and code.startswith(f"{major_dept} "))
            cultural_hit = sorted(d for d in doms & open_cultural_domains if d == "INTER-D" or not firewalled)
            # Also excludes a domain this course is barred from as an
            # Inter-Domain course (see _blocked_as_inter_domain) -- a
            # course carrying 2+ Knowledge-Domain tags (or a Foundations +
            # Knowledge-Domain combination) is Inter-Domain in the real
            # data and can't actually satisfy a plain GWS/GQ/GA/GH/GN/GS/
            # GHW slot at all, so it must never be credited here as if it
            # could.
            noncultural_hit = sorted(
                d for d in doms & open_noncultural_domains
                if (d == "INTER-D" or not firewalled) and not _blocked_as_inter_domain(code, d)
            )
            if cultural_hit and noncultural_hit:
                score += SCORE_MULTI_GEN_ED
                nd_name = load_gen_ed_courses().get(noncultural_hit[0], {}).get("name", noncultural_hit[0])
                cd_name = load_gen_ed_courses().get(cultural_hit[0], {}).get("name", cultural_hit[0])
                reasons.append(
                    f"It's approved for both your open {nd_name} and {cd_name} requirements — "
                    "one course can satisfy both."
                )

        blob = f"{course.name or ''} {course.description or ''}".lower()
        matched_interest = next(
            (k for k in interests for term in INTEREST_TERMS.get(k, []) if term in blob),
            None,
        )
        if matched_interest:
            score += SCORE_INTEREST
            reasons.append(f"It matches your interest in {matched_interest}.")

        if code in wanted_codes:
            score += SCORE_WANTED
            reasons.append("You asked for this course.")

        if is_special:
            score += PENALTY_SPECIAL
            reasons.append("Special-topics/internship style course — verify with your advisor.")

        results.append({
            "code": code,
            "name": course.name,
            "credits": course.credits,
            "score": score,
            "source": "Official Advising Flowchart" if on_flowchart else "Course Catalog",
            "reasons": reasons,
            "flowchart_semester": flowchart_idx.get(code),
            "unlocks": unlocks,
        })

    results.sort(key=lambda r: (-r["score"], r["flowchart_semester"] or 99, r["code"]))
    return results[:top_n]


def default_tips(progress: Dict[str, Any], blocked: List[Dict[str, Any]]) -> List[str]:
    tips = [
        "Prioritize required flowchart courses before electives.",
        "Verify section availability and campus scheduling before registration.",
    ]
    if blocked:
        b = blocked[0]
        tips.append(f"To unlock {b['code']}, complete: {'; '.join(b['missing'])}.")
    if progress.get("extra_courses"):
        tips.append("Some completed courses aren't on the flowchart — ask your advisor if they count as electives.")
    return tips


# ---------------------------------------------------------------------------
# Full plan simulation (real academic calendar)
# ---------------------------------------------------------------------------

SUMMER_MAX_CREDITS = 9.0

# Real PSU billing thresholds for a fall/spring term (not summer, which is
# already its own lower band via SUMMER_MAX_CREDITS and billed separately).
# Below MIN_FULL_TIME_CREDITS a student is registered part-time and billed
# per-credit instead of the flat full-time rate; above
# MAX_CREDITS_NO_EXTRA_FEE, additional per-credit charges apply on top of
# the flat rate. These are purely informational annotations on top of
# whatever a plan's own max_credits_per_semester already scheduled — some
# majors' real degree audits (2 plans at 20cr, 1 at 21cr in this catalog)
# genuinely need a term above 19cr, and that's a real property of the
# major, not something to silently cap away here.
MIN_FULL_TIME_CREDITS = 12.0
MAX_CREDITS_NO_EXTRA_FEE = 19.0


def _term_stream(allow_summer: bool, today: "datetime.date"):
    """Yield upcoming (kind, year) terms starting after today's term.

    Cycle: SPRING (Jan-Apr) -> SUMMER (May-Jul) -> FALL (Aug-Dec) -> SPRING...
    Summers are skipped unless allow_summer.
    """
    m, y = today.month, today.year
    if m <= 4:
        kind, year = "SPRING", y
    elif m <= 7:
        kind, year = "SUMMER", y
    else:
        kind, year = "FALL", y

    def nxt(k: str, yr: int) -> Tuple[str, int]:
        if k == "SPRING":
            return "SUMMER", yr
        if k == "SUMMER":
            return "FALL", yr
        return "SPRING", yr + 1

    kind, year = nxt(kind, year)
    while True:
        if kind == "SUMMER" and not allow_summer:
            kind, year = nxt(kind, year)
            continue
        yield kind, year
        kind, year = nxt(kind, year)


def build_full_plan(
    plan: Dict[str, Any],
    catalog: Dict[str, Course],
    completed: Set[str],
    *,
    start_year: Optional[int] = None,
    grad_years: int = 4,
    allow_summer: bool = False,
    summer_unavailable: Optional[Set[str]] = None,
    today: Optional["datetime.date"] = None,
    max_terms: int = 24,
    initial_consumed_slots: Optional[Set[int]] = None,
    max_credits: Optional[float] = None,
    excluded_codes: Optional[Set[str]] = None,
    preferred_codes: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Simulate real terms (Fall 2026, Spring 2027, ...) until every plan item
    is scheduled.

    - Graduation goal = Spring of (start_year + grad_years).
    - Summer terms (if allowed) carry a lower credit cap and skip courses the
      student reported as unavailable in summer; alternates are substituted
      when an option group has one.
    - If the goal can't be met, extra terms are flagged and a warning explains
      the shortfall instead of silently failing.
    - initial_consumed_slots: slot-type item ids to treat as already done
      before the simulation starts (e.g. from apply_bulk_completion) — a
      generic Gen Ed/elective box a non-freshman already completed but that
      has no real course code to add to `completed`.
    - Pass an already math-placement-expanded `completed` (see
      expand_math_placement) for waivers to apply to the simulation too.
    - max_credits: a student-chosen per-term cap (e.g. "keep me light");
      None falls through to recommend_semester's own default (the plan's
      own max_credits_per_semester, or 17) for every non-summer term, same
      as before this parameter existed. Summer terms always use the lower
      SUMMER_MAX_CREDITS regardless -- a student asking for a heavier
      regular-term load isn't asking for a heavier summer too.
    - excluded_codes: courses the student said they don't want, unioned into
      every term's exclude set (unlike summer_unavailable, this applies
      whether or not the term is a summer term) so they're never simulated
      into the plan even if otherwise eligible.
    - preferred_codes: courses the student explicitly asked for; passed
      through to every term's recommend_semester call so a wanted course
      wins ties within a shared option pool. Never bypasses eligibility.
    """
    import datetime

    today = today or datetime.date.today()
    start_year = int(start_year or today.year)
    grad_years = int(grad_years or 4)
    deadline_year = start_year + grad_years
    summer_unavailable = {norm_code(c) for c in (summer_unavailable or set())}
    excluded_codes = {norm_code(c) for c in (excluded_codes or set())}

    sim_completed = {norm_code(c) for c in completed}
    consumed_slots: Set[int] = set(initial_consumed_slots or set())
    terms: List[Dict[str, Any]] = []
    warnings: List[str] = []
    overtime = 0
    # True only when the simulation gives up because a required, no-
    # substitute plan item's every option is in excluded_codes (see the
    # "could not schedule" branch below) -- i.e. the plan will NEVER finish,
    # permanently, by the student's own choice, as opposed to merely being
    # blocked by something that could resolve itself (unmet prereqs elsewhere
    # in the flowchart, a max_terms cutoff, ...). Deliberately narrow: it
    # must NOT flip for those other "gave up early" causes, since existing,
    # accepted plan behavior for e.g. a real hidden-prereq gap already
    # reports goal.met from `overtime` alone (every simulated term still
    # landed within the deadline; the requirement just never got the chance
    # to be scheduled) -- widening this to any unfinished plan would
    # silently change that established, tested behavior instead of fixing
    # the one thing this round's bug 3 is actually about: a required course
    # the student excluded can never, by construction, become schedulable
    # again on its own, unlike a prereq gap that's merely unlucky ordering.
    blocked_by_exclusion = False

    stream = _term_stream(allow_summer, today)

    for _ in range(max_terms):
        progress = plan_progress(plan, sim_completed, consumed_slots=consumed_slots)
        if progress["done_items"] >= progress["total_items"]:
            break

        kind, year = next(stream)
        is_summer = kind == "SUMMER"
        within_goal = year < deadline_year or (year == deadline_year and kind == "SPRING")

        rec = recommend_semester(
            plan, catalog, sim_completed,
            consumed_slots=consumed_slots,
            include_slots=True,
            max_credits=SUMMER_MAX_CREDITS if is_summer else max_credits,
            exclude_codes=summer_unavailable if is_summer else None,
            excluded_codes=excluded_codes,
            preferred_codes=preferred_codes,
        )

        if not rec["courses"]:
            if is_summer:
                continue  # nothing offered/eligible this summer — skip the term
            # Split the remaining, never-scheduled items by WHY they're
            # stuck, instead of lumping every leftover into one vague
            # "check prereq data" line. A required course-type item whose
            # every real option is in excluded_codes isn't a prereq-data
            # problem at all — it's permanently unschedulable because the
            # student chose to exclude it (or every alternative for it),
            # and that's a materially different, actionable fact: re-
            # including the course fixes it, whereas "check prereq data"
            # sends the student looking for a bug that isn't there.
            excluded_required: List[str] = []
            other_remaining: List[str] = []
            for _, item in _iter_plan_items(plan):
                if item["id"] in progress["done_ids"]:
                    continue
                opts = item.get("options") if item.get("type") == "course" else None
                if opts and excluded_codes and all(o in excluded_codes for o in opts):
                    # Name the actual excluded course, ignoring the
                    # exclusion here on purpose -- _pick_option's own
                    # `exclude` defaults to empty, so this still resolves
                    # to the real (excluded) code/name for a legible
                    # message instead of coming up empty.
                    code = _pick_option(item, catalog)
                    name = catalog[code].name if code and code in catalog else None
                    excluded_required.append(f"{code} ({name})" if code and name else (
                        code or item.get("label") or " or ".join(opts)
                    ))
                else:
                    other_remaining.append(_pick_option(item, catalog) or item.get("label", "?"))
            if excluded_required:
                blocked_by_exclusion = True
                warnings.append(
                    "This plan can't be completed as configured: "
                    + ", ".join(str(r) for r in excluded_required[:10])
                    + " — required by the flowchart with no substitute, but excluded at your "
                    "request. Remove it from your excluded-courses list to make graduation "
                    "achievable again."
                )
            if other_remaining:
                warnings.append(
                    "Could not schedule remaining requirements (check prereq data): "
                    + ", ".join(str(r) for r in other_remaining[:10])
                )
            break

        if not within_goal:
            overtime += 1

        # Real PSU billing status for this term — informational only, never
        # changes what gets scheduled (see MIN_FULL_TIME_CREDITS /
        # MAX_CREDITS_NO_EXTRA_FEE above). Summer terms have their own
        # separate, already-lower billing band, so they're exempt from the
        # full-time-status floor.
        credits_this_term = rec["total_credits"]
        below_full_time = (not is_summer) and credits_this_term < MIN_FULL_TIME_CREDITS
        above_flat_rate = (not is_summer) and credits_this_term > MAX_CREDITS_NO_EXTRA_FEE

        terms.append({
            "index": len(terms) + 1,
            "label": f"{kind.title()} {year}",
            "kind": kind,
            "year": year,
            "is_summer": is_summer,
            "within_goal": within_goal,
            "courses": rec["courses"],
            "total_credits": credits_this_term,
            "below_full_time": below_full_time,
            "above_flat_rate": above_flat_rate,
        })

        for p in rec["courses"]:
            if p["code"]:
                sim_completed.add(p["code"])
            # Always mark the plan item itself consumed too — a Gen Ed slot
            # resolved to a real course (code set, but the underlying plan
            # item is type "slot") still needs consumed_slots so
            # plan_progress() marks that specific slot item done; for a
            # plain course-type item this is a harmless no-op, since
            # plan_progress checks that item's options/completed instead.
            consumed_slots.add(p["item_id"])
    else:
        warnings.append(f"Plan did not finish within {max_terms} simulated terms.")

    if overtime:
        msg = (
            f"Graduating in {grad_years} years (by Spring {deadline_year}) doesn't fit — "
            f"{overtime} extra term(s) needed."
        )
        if not allow_summer:
            msg += " Enabling summer courses could close the gap."
        warnings.append(msg)

    # Deliberately NOT added to `warnings`: a light final semester (under
    # MIN_FULL_TIME_CREDITS) is routine and expected for many real majors'
    # own flowcharts (a capstone-only senior spring, say), not a scheduling
    # problem — `warnings == []` is this whole test suite's established
    # signal for "clean plan," so billing status is surfaced purely via the
    # per-term below_full_time/above_flat_rate flags above instead, for the
    # frontend to render as an informational badge rather than a warning.

    return {
        "terms": terms,
        "warnings": warnings,
        "goal": {
            "start_year": start_year,
            "grad_years": grad_years,
            "deadline": f"Spring {deadline_year}",
            "allow_summer": allow_summer,
            # `overtime == 0` alone used to stand in for "on track," but it
            # only counts terms that were actually simulated -- a required,
            # no-substitute course the student excluded makes the plan give
            # up via the "could not schedule" break BEFORE any term goes
            # over the deadline, so overtime stayed 0 (and met reported
            # True) even though that requirement can now never be
            # scheduled. `blocked_by_exclusion` (see its own comment above)
            # catches exactly that permanent case without touching the
            # existing, tested behavior for a plan that's merely blocked by
            # something else (e.g. an unlucky prereq-ordering gap).
            "met": overtime == 0 and not blocked_by_exclusion,
        },
    }


# ---------------------------------------------------------------------------
# Deterministic Mermaid diagram
# ---------------------------------------------------------------------------

def _mmd_id(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", code)


def build_unlock_map(
    plan: Dict[str, Any],
    catalog: Dict[str, Course],
    completed: Set[str],
    *,
    max_per_tier: int = 8,
) -> Dict[str, str]:
    """Three-tier unlock map: completed (green) -> unlocked next (blue)
    -> future unlocks (grey), with needed ETM courses highlighted red.

    Edges are real prerequisite links from the bulletin data. Scoped to the
    degree plan's courses so the graph stays readable (~20 nodes).
    """
    completed = {norm_code(c) for c in completed}
    progress = plan_progress(plan, completed)

    # Open (not yet satisfied) plan course items, flowchart order.
    open_courses: List[Tuple[int, str, bool]] = []
    seen_codes: Set[str] = set()
    for sem, item in _iter_plan_items(plan):
        if item.get("type") != "course" or item["id"] in progress["done_ids"]:
            continue
        code = _pick_option(item, catalog)
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        open_courses.append((sem["index"], code, bool(item.get("etm"))))

    etm_needed = {code for _, code, etm in open_courses if etm}

    # Tier 2 (blue): unlocked now — prereqs met by completed courses.
    next_tier: List[str] = []
    for _, code, _ in open_courses:
        course = catalog.get(code)
        if course and prereqs_satisfied(course, completed):
            next_tier.append(code)
    # Second pass for same-term concurrent requirements (e.g. CMPSC 131 + MATH 140).
    next_tier = [
        c for c in next_tier
        if concurrent_satisfied(catalog[c], completed | set(next_tier))
    ][:max_per_tier]

    # Tier 3 (grey): unlocked after taking the blue tier.
    after_next = completed | set(next_tier)
    future_tier: List[str] = []
    for _, code, _ in open_courses:
        if code in next_tier:
            continue
        course = catalog.get(code)
        if course and prereqs_satisfied(course, after_next) \
                and concurrent_satisfied(course, after_next):
            future_tier.append(code)
    future_tier = future_tier[:max_per_tier]

    # Tier 1 (green): completed courses that actually feed the shown tiers.
    def deps_of(code: str) -> Set[str]:
        course = catalog.get(code)
        if not course:
            return set()
        out: Set[str] = set()
        for g in _norm_groups(course.prereq_groups) + _norm_groups(course.concurrent_groups):
            out |= g
        return out

    feeding = set()
    for code in next_tier + future_tier:
        feeding |= deps_of(code) & completed
    completed_shown = sorted(feeding)[:max_per_tier] or sorted(completed)[:max_per_tier]

    lines = ["flowchart LR"]
    all_nodes: Dict[str, str] = {}  # code -> css class

    for c in completed_shown:
        all_nodes[c] = "done"
    for c in next_tier:
        all_nodes[c] = "etm" if c in etm_needed else "next"
    for c in future_tier:
        all_nodes[c] = "etm" if c in etm_needed else "future"

    for code in all_nodes:
        lines.append(f'{_mmd_id(code)}["{code}"]')

    edges: Set[str] = set()

    def add_edges(target: str, sources: Set[str]):
        for dep in sorted(deps_of(target) & sources):
            if dep != target:
                edges.add(f"{_mmd_id(dep)} --> {_mmd_id(target)}")

    shown_completed_set = set(completed_shown)
    for c in next_tier:
        add_edges(c, shown_completed_set)
    for c in future_tier:
        add_edges(c, set(next_tier) | shown_completed_set)

    lines.extend(sorted(edges))

    # Deterministic styling (never LLM-generated).
    lines.append("classDef done fill:#dcfce7,stroke:#16a34a,color:#166534")
    lines.append("classDef next fill:#dbeafe,stroke:#2563eb,color:#1e40af")
    lines.append("classDef future fill:#f1f5f9,stroke:#94a3b8,color:#475569")
    lines.append("classDef etm fill:#fee2e2,stroke:#dc2626,color:#991b1b")
    for css in ("done", "next", "future", "etm"):
        members = [_mmd_id(c) for c, cls in all_nodes.items() if cls == css]
        if members:
            lines.append(f"class {','.join(members)} {css}")

    n_etm = sum(1 for cls in all_nodes.values() if cls == "etm")
    explanation = (
        f"{len(completed_shown)} completed course(s) unlock {len(next_tier)} course(s) now "
        f"and {len(future_tier)} more after that"
        + (f" — {n_etm} Entrance-to-Major course(s) still needed (red)." if n_etm else ".")
    )
    return {"mermaid": "\n".join(lines), "explanation": explanation}


def build_course_graph(catalog: Dict[str, Course]) -> List[Dict[str, Any]]:
    """Every course in a scoped catalog, with its own real prerequisite
    groups and the reverse edge (what it unlocks) -- backs the Flowchart
    page's course-explorer search. Independent of any one student's
    completed courses or plan (unlike build_unlock_map, which is a live
    snapshot relative to `completed`) -- this is just the catalog's real
    structure. Scoped to whatever catalog the caller already resolved
    (e.g. one major's departments), not the whole PSU catalog.
    """
    # Reverse index: code -> courses that list it in any prereq group.
    unlocks: Dict[str, Set[str]] = {code: set() for code in catalog}
    for code, course in catalog.items():
        for group in _norm_groups(course.prereq_groups):
            for dep in group:
                if dep in unlocks:
                    unlocks[dep].add(code)

    return [
        {
            "code": code,
            "name": course.name,
            "credits": course.credits,
            "prereqs": [sorted(g) for g in _norm_groups(course.prereq_groups)],
            "unlocks": sorted(unlocks.get(code, set())),
        }
        for code, course in sorted(catalog.items())
    ]


def build_semester_flowchart(
    catalog: Dict[str, Course],
    completed: Set[str],
    full_plan_terms: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Full semester-by-semester flowchart: completed courses (green) ->
    the very next simulated term (red) -> every term after that (grey),
    with real prerequisite arrows colored to match their source node.

    Unlike build_unlock_map (a flat 3-tier snapshot), this spans the whole
    remaining path term-by-term — an alternative view of the same data
    the card-based 'full plan' UI shows, for students who want to see the
    prerequisite chain laid out visually across every term at once.
    """
    completed = {norm_code(c) for c in completed}

    def deps_of(code: str) -> Set[str]:
        course = catalog.get(code)
        if not course:
            return set()
        out: Set[str] = set()
        for g in _norm_groups(course.prereq_groups) + _norm_groups(course.concurrent_groups):
            out |= g
        return out

    lines = ["flowchart LR"]
    code_to_node: Dict[str, str] = {}
    node_class: Dict[str, str] = {}
    slot_counter = 0

    def add_course_node(code: str, css: str) -> str:
        node_id = f"N_{_mmd_id(code)}"
        if code not in code_to_node:
            code_to_node[code] = node_id
            node_class[node_id] = css
            lines.append(f'{node_id}["{code}"]')
        return code_to_node[code]

    def add_slot_node(label: str, css: str, sem_idx: int) -> str:
        nonlocal slot_counter
        slot_counter += 1
        node_id = f"SLOT_{sem_idx}_{slot_counter}"
        node_class[node_id] = css
        safe_label = (label or "Elective").replace('"', "'")
        lines.append(f'{node_id}["{safe_label}"]')
        return node_id

    # Completed courses get one subgraph — the student's chat/chip input
    # doesn't record which historical semester each was actually taken in,
    # so they can't be placed into individual past-term subgraphs.
    if completed:
        lines.append('subgraph SEM_DONE["Completed"]')
        for code in sorted(completed):
            add_course_node(code, "done")
        lines.append("end")

    # Future terms, one subgraph per simulated term: the first is what the
    # student needs to take right now (red); everything after is still
    # ahead (grey).
    for i, term in enumerate(full_plan_terms):
        css = "next" if i == 0 else "future"
        label = (term.get("label") or f"Term {i + 1}").replace('"', "'")
        lines.append(f'subgraph SEM_{i}["{label}"]')
        for p in term.get("courses", []):
            code = p.get("code")
            if code:
                add_course_node(norm_code(code), css)
            else:
                add_slot_node(p.get("name"), css, i)
        lines.append("end")

    # Real prerequisite/concurrent arrows between any two shown course nodes,
    # colored to match the arrow's source (dependency) node.
    edges: List[Tuple[str, str, str]] = []
    seen_edges: Set[Tuple[str, str]] = set()
    for code, node_id in code_to_node.items():
        for dep in sorted(deps_of(code)):
            dep_node = code_to_node.get(dep)
            if dep_node and dep_node != node_id and (dep_node, node_id) not in seen_edges:
                seen_edges.add((dep_node, node_id))
                edges.append((dep_node, node_id, node_class[dep_node]))

    for from_id, to_id, _ in edges:
        lines.append(f"{from_id} --> {to_id}")

    lines.append("classDef done fill:#dcfce7,stroke:#16a34a,color:#166534")
    lines.append("classDef next fill:#fee2e2,stroke:#dc2626,color:#991b1b")
    lines.append("classDef future fill:#f1f5f9,stroke:#94a3b8,color:#475569")
    for css in ("done", "next", "future"):
        members = [nid for nid, c in node_class.items() if c == css]
        if members:
            lines.append(f"class {','.join(members)} {css}")

    # linkStyle indices must match edge declaration order exactly.
    edge_colors = {"done": "#16a34a", "next": "#dc2626", "future": "#94a3b8"}
    for idx, (_, _, css) in enumerate(edges):
        lines.append(f"linkStyle {idx} stroke:{edge_colors[css]}")

    n_next = sum(1 for c in node_class.values() if c == "next")
    n_future = sum(1 for c in node_class.values() if c == "future")
    explanation = (
        f"{len(completed)} completed course(s) (green), {n_next} recommended for your next "
        f"term (red), and {n_future} course(s) in the terms after that (grey)."
    )
    return {"mermaid": "\n".join(lines), "explanation": explanation}


def build_mermaid(
    plan: Dict[str, Any],
    catalog: Dict[str, Course],
    completed: Set[str],
    next_courses: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Compact completed -> recommended diagram with real prereq edges."""
    completed = {norm_code(c) for c in completed}
    rec_codes = [p["code"] for p in next_courses if p.get("code")][:8]

    shown_completed = sorted(completed)[:8]
    lines = ["flowchart LR"]

    if shown_completed:
        lines.append('subgraph DONE["Completed"]')
        for c in shown_completed:
            lines.append(f'{_mmd_id(c)}["{c}"]')
        lines.append("end")

    lines.append('subgraph NEXT["Recommended next semester"]')
    if rec_codes:
        for c in rec_codes:
            lines.append(f'{_mmd_id(c)}["{c}"]')
    else:
        lines.append('EMPTY["Start here - no prerequisites needed"]')
    lines.append("end")

    edge_count = 0
    for c in rec_codes:
        course = catalog.get(c)
        if not course:
            continue
        for g in _norm_groups(course.prereq_groups):
            for dep in sorted(g & completed):
                if dep in shown_completed:
                    lines.append(f"{_mmd_id(dep)} --> {_mmd_id(c)}")
                    edge_count += 1
        for g in _norm_groups(course.concurrent_groups):
            for dep in sorted(g & set(rec_codes)):
                if dep != c:
                    lines.append(f"{_mmd_id(dep)} -.-> {_mmd_id(c)}")
                    edge_count += 1
        if edge_count > 24:
            break

    explanation = (
        f"Your position on the {plan.get('major', '')} {plan.get('catalog_year', '')} flowchart: "
        f"{len(completed)} course(s) completed, {len(rec_codes)} recommended next."
    )
    return {"mermaid": "\n".join(lines), "explanation": explanation}
