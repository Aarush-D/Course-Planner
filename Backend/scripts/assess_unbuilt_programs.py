"""Assess every undergraduate baccalaureate program on bulletins.psu.edu that
has no degree-plan file yet, and report how buildable each one is.

Why: building a plan file by hand from a bulletin page is where fabricated
course codes crept in before (see bulletin_scraper.py's own docstring). This
runs the deterministic scraper over every unbuilt program and records the
facts that decide whether it can be built safely -- so the build queue is a
lookup, not a re-investigation:

  * does a Suggested Academic Plan table parse at all
  * do per-semester item credits sum to each printed subtotal
  * does the grand total match the printed total
  * does every linked course code exist in the real catalogs
  * which departments the plan needs that have no catalog file yet

    python scripts/assess_unbuilt_programs.py PROGRAMS_INDEX.json OUT.json

PROGRAMS_INDEX.json is the parse of https://bulletins.psu.edu/programs/
(href, title, degree, campuses_filter per tile). Read-only against the
bulletin; writes only OUT.json.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Set

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import bulletin_scraper as bs  # noqa: E402
import planner_engine as engine  # noqa: E402

DELAY_S = 0.2


def _norm(u: str) -> str:
    u = re.sub(r"^https://bulletins\.psu\.edu", "", u)
    return re.sub(r"^/archive/\d{4}-\d{4}", "", u).rstrip("/")


def built_urls() -> Set[str]:
    out: Set[str] = set()
    for f in glob.glob(os.path.join(BASE_DIR, "degree_plans", "*.json")):
        with open(f) as fh:
            s = json.load(fh).get("source")
        if isinstance(s, str):
            out.add(_norm(s))
    return out


def assess_one(url: str, catalog: Dict[str, Any], catalog_depts: Set[str]) -> Dict[str, Any]:
    res = bs.scrape_and_verify(url, catalog)
    if not res.get("ok", True) and res.get("error"):
        return {"parsed": False, "why": res["error"]}
    sems = res.get("semesters") or []
    if not sems:
        return {"parsed": False, "why": "no Suggested Academic Plan table parsed"}
    mismatched = []
    depts: Set[str] = set()
    n_items = 0
    for sem in sems:
        total = 0.0
        for it in sem["items"]:
            n_items += 1
            total += it.get("credits") or 0
            for c in it.get("linked_codes", []):
                m = re.match(r"^([A-Z]{2,6})\s", engine.norm_code(c) + " ")
                if m:
                    depts.add(m.group(1))
        printed = sem.get("subtotal_credits")
        if printed is not None and abs(total - printed) > 0.01:
            mismatched.append({"semester": sem.get("label"), "sum": total, "printed": printed})
    missing_depts = sorted(d for d in depts if d not in catalog_depts)
    return {
        "parsed": True,
        "semesters": len(sems),
        "items": n_items,
        "printed_total_credits": res.get("printed_total_credits"),
        "subtotal_mismatches": mismatched,
        "unverified_codes": res.get("unverified_codes", []),
        "departments": sorted(depts),
        "missing_catalog_departments": missing_depts,
        "clean": not mismatched and not res.get("unverified_codes") and not missing_depts,
    }


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    index_path, out_path = argv[0], argv[1]
    with open(index_path) as f:
        programs = json.load(f)
    have = built_urls()
    todo = [p for p in programs if "Baccalaureate Degree" in p["degree"] and _norm(p["href"]) not in have]
    catalog = engine.load_full_catalog()
    catalog_depts = {
        os.path.basename(p)[: -len("_catalog.json")].upper()
        for p in glob.glob(os.path.join(BASE_DIR, "catalogs", "*_catalog.json"))
    }
    results: Dict[str, Any] = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            results = json.load(f)
    print(f"{len(todo)} unbuilt baccalaureate programs, {len(results)} already assessed")
    for i, p in enumerate(todo, 1):
        url = "https://bulletins.psu.edu" + p["href"]
        if url in results and results[url].get("parsed") is not None:
            continue
        try:
            r = assess_one(url, catalog, catalog_depts)
        except Exception as e:  # keep going; record why
            r = {"parsed": False, "why": f"{type(e).__name__}: {e}"}
        r.update({"title": p["title"], "campuses": p["campuses_filter"]})
        results[url] = r
        if i % 10 == 0:
            with open(out_path, "w") as f:
                json.dump(results, f, indent=1)
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(DELAY_S)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=1, sort_keys=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
