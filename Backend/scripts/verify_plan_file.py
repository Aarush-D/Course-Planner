"""Verify one or more degree-plan files the way the repo's own gates do.

    python scripts/verify_plan_file.py CODE [CODE ...] [--year 2026] [--grad-years 4]

For each plan it checks, and prints PASS/FAIL per check:
  * required keys present, `major` matches the filename, code is [A-Z]+ only
  * `campus` names are all real campuses
  * every course option is a real course in the merged catalog of the plan's
    own `departments` (fabricated or stale codes fail here)
  * a simulation (build_full_plan, default 4 years) schedules everything:
    no warnings, goal met. --grad-years 5 is only legitimate for a plan whose
    real credit total/prereq chain genuinely overflows 8 terms; it must then
    also be added to _GRAD_YEARS_OVERRIDE in tests.py with that reason.
Exit status is non-zero if any plan fails. Read-only.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import planner_engine as engine  # noqa: E402

REQUIRED = ("major", "catalog_year", "title", "source", "notes", "max_credits_per_semester", "departments", "semesters")


def verify(code: str, year: int, grad_years: int = 4) -> List[str]:
    problems: List[str] = []
    path = os.path.join(engine.DEGREE_PLAN_DIR, f"{code}-{year}.json")
    if not re.fullmatch(r"[A-Z]+", code):
        problems.append("major code must be letters only (the whole-repo test globs [A-Z]+-YYYY.json)")
    if not os.path.exists(path):
        return problems + [f"missing file {path}"]
    with open(path) as f:
        plan = json.load(f)
    for k in REQUIRED:
        if k not in plan:
            problems.append(f"missing key {k!r}")
    if plan.get("major") != code:
        problems.append(f"major field {plan.get('major')!r} != filename code {code!r}")
    if plan.get("catalog_year") != year:
        problems.append(f"catalog_year {plan.get('catalog_year')!r} != {year}")
    raw = plan.get("campus")
    for c in (raw if isinstance(raw, list) else ([raw] if raw else [])):
        if c not in engine.PSU_CAMPUSES:
            problems.append(f"unknown campus {c!r}")
    if not raw:
        problems.append("campus missing (a branch-campus plan must name its campus)")
    if problems:
        return problems

    loaded = engine.load_degree_plan(code, year)
    if loaded is None:
        return ["engine.load_degree_plan returned None"]
    catalog = engine.load_merged_catalog(loaded["departments"])
    for _, item in engine._iter_plan_items(loaded):
        if item.get("type") == "course":
            for opt in item.get("options", []):
                if engine.norm_code(opt) not in catalog:
                    problems.append(f"course option {opt!r} not in catalog for departments {loaded['departments']}")
    # Pinned to July 1 of the catalog year, exactly like the repo-wide gate
    # (tests.py test_all_years_load_and_graduate_cleanly): without it, "today"
    # is the real date and an already-started term makes any plan look late.
    fp = engine.build_full_plan(
        loaded, catalog, set(), start_year=year, grad_years=grad_years, today=datetime.date(year, 7, 1),
    )
    for w in fp["warnings"]:
        problems.append(f"warning: {w}")
    goal = fp.get("goal", {})
    if goal.get("met") is not True:
        problems.append(f"goal not met: {goal}")
    return problems


def main(argv: List[str]) -> int:
    year = 2026
    grad_years = 4
    if "--year" in argv:
        i = argv.index("--year")
        year = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--grad-years" in argv:
        i = argv.index("--grad-years")
        grad_years = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__)
        return 2
    failed = 0
    for code in argv:
        problems = verify(code.upper(), year, grad_years)
        if problems:
            failed += 1
            print(f"FAIL {code}-{year}")
            for p in problems:
                print(f"   - {p}")
        else:
            print(f"PASS {code}-{year}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
