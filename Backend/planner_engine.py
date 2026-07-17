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

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set, Tuple

from Courseplanner import Course, load_catalog_from_json, save_catalog_to_json, scrape_psu_dept_catalog

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEGREE_PLAN_DIR = os.path.join(BASE_DIR, "degree_plans")
CATALOG_DIR = os.path.join(BASE_DIR, "catalogs")

COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6})\s*-?\s*(\d{2,3}[A-Z]{0,2})\b")

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

def list_degree_plans() -> List[Dict[str, Any]]:
    plans = []
    if not os.path.isdir(DEGREE_PLAN_DIR):
        return plans
    for fname in sorted(os.listdir(DEGREE_PLAN_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(DEGREE_PLAN_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            plans.append({
                "major": data.get("major", ""),
                "catalog_year": data.get("catalog_year"),
                "title": data.get("title", fname),
            })
        except Exception:
            continue
    return plans


def load_degree_plan(major: str, catalog_year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Load the plan for a major; latest catalog year if none requested."""
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


def _iter_plan_items(plan: Dict[str, Any]):
    for sem in plan.get("semesters", []):
        for item in sem.get("items", []):
            yield sem, item


# ---------------------------------------------------------------------------
# Merged catalog
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _load_dept_catalog_cached(dept: str) -> Optional[Dict[str, Course]]:
    dept = dept.upper()
    os.makedirs(CATALOG_DIR, exist_ok=True)
    path = os.path.join(CATALOG_DIR, f"{dept.lower()}_catalog.json")
    if os.path.exists(path):
        try:
            return load_catalog_from_json(path)
        except Exception:
            pass
    try:
        catalog = scrape_psu_dept_catalog(dept)
        save_catalog_to_json(path, catalog)
        return catalog
    except Exception:
        return None


def load_merged_catalog(depts: List[str]) -> Dict[str, Course]:
    """Merge several department catalogs into one dict keyed by normalized code."""
    merged: Dict[str, Course] = {}
    for dept in depts:
        cat = _load_dept_catalog_cached(dept.upper())
        if not cat:
            continue
        for code, course in cat.items():
            merged[norm_code(code)] = course
    return merged


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


@lru_cache(maxsize=4)
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
}


def match_courses_in_text(text: str, catalog: Dict[str, Course]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Find course mentions in free-form text and resolve them against the catalog.

    Returns (matched, unmatched): matched entries carry code/name/credits so the
    UI can show the student exactly what was understood.
    """
    raw = (text or "").upper()
    matched: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    seen: Set[str] = set()

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

    for m in COURSE_CODE_RE.finditer(raw):
        dept, num = m.groups()
        if dept in _NOT_COURSE_WORDS:
            continue
        add(f"{dept} {num}", m.group(0))

    return matched, unmatched


# ---------------------------------------------------------------------------
# Plan progress (pure — no plan mutation)
# ---------------------------------------------------------------------------

def plan_progress(
    plan: Dict[str, Any],
    completed: Set[str],
    *,
    consumed_slots: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    """Determine which plan items are satisfied.

    Course items are satisfied when one of their options was completed (each
    completed course can satisfy only one item). Pattern slots (e.g.
    CMPSC/CMPEN 4XX) absorb leftover completed courses; other slots are done
    only when listed in consumed_slots (used by the semester simulation).
    """
    completed = {norm_code(c) for c in completed}
    consumed_slots = consumed_slots or set()
    used: Set[str] = set()
    done_ids: Set[int] = set()
    done_with: Dict[int, str] = {}
    credits_done = 0.0
    total_credits = 0.0

    pattern_slots = []
    for sem, item in _iter_plan_items(plan):
        credits = float(item.get("credits") or 0)
        total_credits += credits
        if item.get("type") == "course":
            hit = next((o for o in item["options"] if o in completed and o not in used), None)
            if hit:
                used.add(hit)
                done_ids.add(item["id"])
                done_with[item["id"]] = hit
                credits_done += credits
        else:
            if item["id"] in consumed_slots:
                done_ids.add(item["id"])
                credits_done += credits
            elif item.get("match"):
                pattern_slots.append(item)

    leftovers = [c for c in sorted(completed) if c not in used]
    for item in pattern_slots:
        rx = re.compile(item["match"])
        hit = next((c for c in leftovers if rx.match(c)), None)
        if hit:
            leftovers.remove(hit)
            done_ids.add(item["id"])
            done_with[item["id"]] = hit
            credits_done += float(item.get("credits") or 0)

    total_items = sum(1 for _ in _iter_plan_items(plan))

    return {
        "done_ids": done_ids,
        "done_with": done_with,
        "done_items": len(done_ids),
        "total_items": total_items,
        "credits_done": round(credits_done, 1),
        "total_credits": round(total_credits, 1),
        "extra_courses": leftovers,  # completed courses that don't map to the plan
    }


# ---------------------------------------------------------------------------
# Next-semester recommendation
# ---------------------------------------------------------------------------

def _pick_option(
    item: Dict[str, Any],
    catalog: Dict[str, Course],
    exclude: Optional[Set[str]] = None,
) -> Optional[str]:
    """Preferred option for a course item: first catalog-present option that
    isn't excluded (e.g. not offered in summer) — falls back to alternates,
    so 'CAS 100A unavailable' can still pick CAS 100B."""
    exclude = exclude or set()
    for o in item.get("options", []):
        if o in catalog and o not in exclude:
            return o
    for o in item.get("options", []):
        if o not in exclude:
            return o
    return None


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
) -> Dict[str, Any]:
    """Pick the best prereq-safe course load for one semester.

    Walks the flowchart in semester order; a course is chosen when its enforced
    prereqs are completed and concurrent requirements are met by completed or
    same-term picks. Slots (GEN ED etc.) fill the remaining credit budget.
    exclude_codes skips specific courses (e.g. not offered in summer).
    """
    completed = {norm_code(c) for c in completed}
    consumed_slots = consumed_slots or set()
    exclude_codes = {norm_code(c) for c in (exclude_codes or set())}
    max_credits = float(max_credits or plan.get("max_credits_per_semester") or 17)
    depts = plan.get("departments", [])

    progress = plan_progress(plan, completed, consumed_slots=consumed_slots)
    done_ids = progress["done_ids"]

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
        for sem, item in _iter_plan_items(plan):
            if item["id"] in done_ids or item["id"] in picked_ids:
                continue

            if item.get("type") == "slot":
                if not include_slots:
                    continue
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
                })
                return True

            code = _pick_option(item, catalog, exclude_codes)
            if not code:
                continue
            credits = _item_credits(item, code, catalog)
            if current_load() + credits > max_credits + 0.25:
                continue
            course = catalog.get(code)
            if course:
                if not prereqs_satisfied(course, completed):
                    continue
                if not concurrent_satisfied(course, completed | picked_codes):
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
            if miss:
                blocked.append({
                    "code": code,
                    "name": course.name,
                    "flowchart_semester": sem["index"],
                    "missing": [" or ".join(g) for g in miss],
                })
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
_EXCLUDE_NAME_RE = re.compile(
    r"special topics|internship|independent stud|thesis|foreign stud|"
    r"individual stud|practicum|co-?op experience",
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
    top_n: int = 8,
) -> List[Dict[str, Any]]:
    """Deterministic weighted ranking of every eligible course.

    Eligibility (prereqs) is decided here in Python; scores explain priority.
    The LLM never sees ineligible courses and cannot alter scores.
    """
    completed = {norm_code(c) for c in completed}
    interests = interests or []
    depts = plan.get("departments", [])
    progress = plan_progress(plan, completed)
    flowchart_idx, satisfied_options = _plan_course_index(
        plan, catalog, progress["done_ids"]
    )
    slot_patterns = [
        re.compile(item["match"])
        for _, item in _iter_plan_items(plan)
        if item.get("type") == "slot" and item.get("match")
    ]

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
        if code in satisfied_options:
            continue  # an alternate already covered this requirement
        if not prereqs_satisfied(course, completed):
            continue
        if not concurrent_satisfied(course, completed):
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

        blob = f"{course.name or ''} {course.description or ''}".lower()
        matched_interest = next(
            (k for k in interests for term in INTEREST_TERMS.get(k, []) if term in blob),
            None,
        )
        if matched_interest:
            score += SCORE_INTEREST
            reasons.append(f"It matches your interest in {matched_interest}.")

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
) -> Dict[str, Any]:
    """Simulate real terms (Fall 2026, Spring 2027, ...) until every plan item
    is scheduled.

    - Graduation goal = Spring of (start_year + grad_years).
    - Summer terms (if allowed) carry a lower credit cap and skip courses the
      student reported as unavailable in summer; alternates are substituted
      when an option group has one.
    - If the goal can't be met, extra terms are flagged and a warning explains
      the shortfall instead of silently failing.
    """
    import datetime

    today = today or datetime.date.today()
    start_year = int(start_year or today.year)
    grad_years = int(grad_years or 4)
    deadline_year = start_year + grad_years
    summer_unavailable = {norm_code(c) for c in (summer_unavailable or set())}

    sim_completed = {norm_code(c) for c in completed}
    consumed_slots: Set[int] = set()
    terms: List[Dict[str, Any]] = []
    warnings: List[str] = []
    overtime = 0

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
            max_credits=SUMMER_MAX_CREDITS if is_summer else None,
            exclude_codes=summer_unavailable if is_summer else None,
        )

        if not rec["courses"]:
            if is_summer:
                continue  # nothing offered/eligible this summer — skip the term
            remaining = [
                _pick_option(item, catalog) or item.get("label", "?")
                for _, item in _iter_plan_items(plan)
                if item["id"] not in progress["done_ids"]
            ]
            warnings.append(
                "Could not schedule remaining requirements (check prereq data): "
                + ", ".join(str(r) for r in remaining[:10])
            )
            break

        if not within_goal:
            overtime += 1

        terms.append({
            "index": len(terms) + 1,
            "label": f"{kind.title()} {year}",
            "kind": kind,
            "year": year,
            "is_summer": is_summer,
            "within_goal": within_goal,
            "courses": rec["courses"],
            "total_credits": rec["total_credits"],
        })

        for p in rec["courses"]:
            if p["code"]:
                sim_completed.add(p["code"])
            else:
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

    return {
        "terms": terms,
        "warnings": warnings,
        "goal": {
            "start_year": start_year,
            "grad_years": grad_years,
            "deadline": f"Spring {deadline_year}",
            "allow_summer": allow_summer,
            "met": overtime == 0,
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
