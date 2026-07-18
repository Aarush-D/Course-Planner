"""Transfer Credit feature: PA community college distance ranking, plus a
cached-equivalency scaffold for PSU's Transfer Credit Tool data.

Scope (see docs/EXPANSION_PLAN.md §5): Pennsylvania community colleges only,
for now — nationwide is a planned follow-up. Distance is straight-line
(Haversine) from a student's zip code to each college's main campus, using a
real Census Gazetteer-derived zip coordinate table (data/pa_zip_coords.json)
so no external geocoding API is needed.

The equivalency cache itself (data/transfer_equivalencies.json) is NOT
populated yet — LionPATH's Transfer Credit Tool is a stateful PeopleSoft form
with no public API, and a reliable sample of its results table hasn't been
captured yet. The schema and refresh-scheduling logic below are built and
tested against synthetic data so the scraper (once built) only has to
produce records matching EquivalencyRecord's shape.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, TypedDict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
ZIP_COORDS_PATH = os.path.join(DATA_DIR, "pa_zip_coords.json")
COLLEGES_PATH = os.path.join(DATA_DIR, "pa_community_colleges.json")
EQUIVALENCIES_PATH = os.path.join(DATA_DIR, "transfer_equivalencies.json")

EARTH_RADIUS_MILES = 3958.8


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance between two lat/lng points, in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a)))


_zip_coords_cache: Optional[Dict[str, List[float]]] = None


def _load_zip_coords() -> Dict[str, List[float]]:
    global _zip_coords_cache
    if _zip_coords_cache is None:
        with open(ZIP_COORDS_PATH, "r", encoding="utf-8") as f:
            _zip_coords_cache = json.load(f)
    return _zip_coords_cache


def zip_to_coords(zip_code: str) -> Optional[tuple[float, float]]:
    """Look up a 5-digit zip's (lat, lng). Currently PA-only (see module
    docstring) — returns None for zips outside the bundled table rather than
    guessing, so callers can surface a clear "not supported yet" message."""
    z = (zip_code or "").strip()[:5]
    coords = _load_zip_coords().get(z)
    return (coords[0], coords[1]) if coords else None


_colleges_cache: Optional[List[Dict[str, Any]]] = None


def load_community_colleges() -> List[Dict[str, Any]]:
    global _colleges_cache
    if _colleges_cache is None:
        with open(COLLEGES_PATH, "r", encoding="utf-8") as f:
            _colleges_cache = json.load(f)["colleges"]
    return _colleges_cache


def nearest_colleges(zip_code: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Every bundled community college, sorted closest to furthest from the
    student's zip. Returns [] if the zip isn't in the PA table."""
    origin = zip_to_coords(zip_code)
    if not origin:
        return []
    lat0, lng0 = origin

    ranked = []
    for c in load_community_colleges():
        d = haversine_miles(lat0, lng0, c["lat"], c["lng"])
        ranked.append({**c, "distance_miles": round(d, 1)})
    ranked.sort(key=lambda c: c["distance_miles"])
    return ranked[:limit] if limit else ranked


# ---------------------------------------------------------------------------
# Equivalency cache (schema confirmed 2026-07-18 against a real PDF export
# from LionPATH — Delaware County CCC's ENG 100 -> PSU ENGL 15 — one record
# seeded; broader PA coverage still needs more samples).
# ---------------------------------------------------------------------------

class EquivalencyRecord(TypedDict, total=False):
    psu_course: str            # e.g. "ENGL 15", normalized via planner_engine.norm_code
    psu_course_id: str         # PSU's internal numeric catalog ID, e.g. "016510" (traceability only)
    institution_id: str        # LionPATH institution ID, e.g. "100123622"
    institution_name: str      # e.g. "Delaware County Community College"
    transfer_course_code: str  # the equivalent course at that institution
    transfer_course_title: str
    credits: float
    effective_date: str        # ISO date the equivalency became valid
    expiry_date: Optional[str] # ISO date it stops being valid, if any
    scraped_at: str            # ISO date we last confirmed this from LionPATH


def load_equivalency_cache() -> Dict[str, List[EquivalencyRecord]]:
    """psu_course (normalized) -> list of equivalency records. Empty dict
    until the scraper is built and a first pass has run."""
    if not os.path.exists(EQUIVALENCIES_PATH):
        return {}
    with open(EQUIVALENCIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_equivalency_cache(cache: Dict[str, List[EquivalencyRecord]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(EQUIVALENCIES_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, sort_keys=True)


def soonest_expiring(
    cache: Dict[str, List[EquivalencyRecord]],
    *,
    limit: int = 10,
) -> List[EquivalencyRecord]:
    """Records with an expiry_date, soonest first — drives refresh priority
    per Aarush's spec: re-scrape whichever cached course-acceptance is
    closest to expiring, not on a flat calendar schedule. Records with no
    expiry_date (open-ended equivalencies) are excluded — nothing to refresh
    urgently about those."""
    dated: List[EquivalencyRecord] = [
        r for records in cache.values() for r in records if r.get("expiry_date")
    ]
    dated.sort(key=lambda r: r["expiry_date"])
    return dated[:limit]


def rank_colleges_for_courses(
    zip_code: str,
    psu_courses: List[str],
    cache: Optional[Dict[str, List[EquivalencyRecord]]] = None,
) -> List[Dict[str, Any]]:
    """Consolidated recommendation across multiple requested courses.

    Sorted by (# of the requested courses this college covers, descending),
    then distance (ascending) — "recommend the community college with the
    most transfer credits offered; if tied, closest first." Colleges that
    cover zero of the requested courses are still included (sorted last) so
    the caller can see the full closest-to-furthest picture, matching
    "consolidate the listing and give it to them showing all the possible
    options from closest to furthest."
    """
    cache = cache if cache is not None else load_equivalency_cache()
    wanted = {c.strip().upper() for c in psu_courses}

    covers: Dict[str, set] = {}
    for course in wanted:
        for rec in cache.get(course, []):
            covers.setdefault(rec["institution_id"], set()).add(course)

    ranked = nearest_colleges(zip_code)
    for c in ranked:
        matched = covers.get(c["institution_id"], set())
        c["courses_covered"] = sorted(matched)
        c["courses_covered_count"] = len(matched)

    ranked.sort(key=lambda c: (-c["courses_covered_count"], c["distance_miles"]))
    return ranked
