"""Eval 2 -- plan validity, checked by independent_checker.py.

(a) Sweep: every file in Backend/degree_plans (all majors, all catalog years),
    simulated from an empty completed set through POST /api/plan.
(b) Random: N random, prerequisite-CLOSED completed sets (built with the
    independent checker, not the engine), each sent through POST /api/plan.

For every course the API schedules or recommends we check that its
prerequisites are satisfied by the completed set or by a strictly earlier
term (concurrent_groups may also be met in the same term).
"""
from __future__ import annotations

import glob
import json
import os
import random
import re
from typing import Any, Dict, List, Tuple

from independent_checker import Catalog, is_developmental_math_gap, looks_like_code, norm


def plan_files(backend_dir: str) -> List[Tuple[str, int]]:
    out = []
    for p in sorted(glob.glob(os.path.join(backend_dir, "degree_plans", "*.json"))):
        m = re.match(r"^([A-Z]+)-(\d{4})\.json$", os.path.basename(p))
        if m:
            out.append((m.group(1), int(m.group(2))))
    return out


def _code_of(entry: Dict[str, Any], cat: Catalog):
    """The course code a scheduled/recommended entry stands for, or None for a
    placeholder (an unfilled Gen Ed / elective box has no real course yet)."""
    for key in ("id", "code", "name"):
        v = entry.get(key)
        if isinstance(v, str) and looks_like_code(v) and cat.known(v):
            return norm(v)
    return None


def check_response(resp: Dict[str, Any], completed: List[str], cat: Catalog, ctx: Dict[str, Any]):
    """Returns (violations, courses_checked, placeholders_skipped)."""
    cp = resp.get("coursePlan") or {}
    violations: List[Dict[str, Any]] = []
    checked = skipped = 0

    def record(vs, where):
        for v in vs:
            v = dict(v)
            v.update(ctx)
            v["where"] = where
            v["developmental_math_gap"] = is_developmental_math_gap(v)
            violations.append(v)

    done = {norm(c) for c in completed}

    terms = (cp.get("fullPlan") or {}).get("terms") or []
    before = set(done)
    for t in terms:
        codes = []
        for entry in t.get("courses", []):
            c = _code_of(entry, cat)
            if c is None:
                skipped += 1
            else:
                codes.append(c)
        for c in codes:
            checked += 1
            record(cat.violations(c, before, [x for x in codes if x != c]), f"fullPlan:{t.get('label')}")
        before |= set(codes)

    ns = cp.get("nextSemester") or {}
    ns_codes = [c for c in (_code_of(e, cat) for e in ns.get("courses", [])) if c]
    for c in ns_codes:
        checked += 1
        record(cat.violations(c, done, [x for x in ns_codes if x != c]), "nextSemester")

    for rec in cp.get("recommendations") or []:
        c = _code_of(rec, cat)
        if c:
            checked += 1
            record(cat.violations(c, done, ns_codes), "recommendations")
    for raw in cp.get("eligible") or []:
        if isinstance(raw, str) and looks_like_code(raw) and cat.known(raw):
            checked += 1
            record(cat.violations(norm(raw), done, ns_codes), "eligible")
    return violations, checked, skipped


def _post(client, major: str, year: int, completed: List[str]):
    r = client.post("/api/plan", json={
        "prompt": "", "completed": completed, "major": major,
        "catalog_year": year, "start_year": year, "grad_years": 4,
    })
    return r.status_code, (r.get_json(silent=True) or {})


def sweep(client, backend_dir: str, cat: Catalog) -> Dict[str, Any]:
    files = plan_files(backend_dir)
    valid = 0
    errors: List[Dict[str, Any]] = []
    viol: List[Dict[str, Any]] = []
    bad_plans = []
    n_checked = n_skipped = 0
    for major, year in files:
        status, body = _post(client, major, year, [])
        if status != 200:
            errors.append({"major": major, "catalog_year": year, "http_status": status})
            continue
        vs, c, s = check_response(body, [], cat, {"major": major, "catalog_year": year})
        n_checked += c
        n_skipped += s
        if vs:
            viol.extend(vs)
            bad_plans.append(f"{major}-{year}")
        else:
            valid += 1
    return {
        "valid": valid, "total": len(files), "http_errors": errors,
        "plans_with_violations": bad_plans, "violations": viol,
        "courses_checked": n_checked, "placeholders_skipped": n_skipped,
    }


def random_closed_set(plan_json: Dict[str, Any], cat: Catalog, rng: random.Random) -> List[str]:
    """A completed-course set that is closed under prerequisites BY THE
    INDEPENDENT CHECKER: walk the plan's explicit courses in semester order,
    take each with some probability only if its prerequisites are already in
    the set. Always built from semesters actually reachable, never arbitrary."""
    cutoff = rng.randint(0, min(7, len(plan_json.get("semesters", []))))
    p = rng.uniform(0.35, 0.95)
    taken: List[str] = []
    for sem in plan_json.get("semesters", [])[:cutoff]:
        for item in sem.get("items", []):
            if item.get("type") != "course" or not item.get("options"):
                continue
            opt = norm(rng.choice(item["options"]))
            if opt in taken or not cat.known(opt) or rng.random() > p:
                continue
            if not cat.violations(opt, taken, ()):
                taken.append(opt)
    return taken


def random_sets(client, backend_dir: str, cat: Catalog, n: int, seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    files = plan_files(backend_dir)
    valid = 0
    errors, viol, bad = [], [], []
    n_checked = n_skipped = 0
    sizes = []
    for i in range(n):
        major, year = rng.choice(files)
        with open(os.path.join(backend_dir, "degree_plans", f"{major}-{year}.json")) as f:
            plan_json = json.load(f)
        completed = random_closed_set(plan_json, cat, rng)
        sizes.append(len(completed))
        status, body = _post(client, major, year, completed)
        ctx = {"sample": i, "major": major, "catalog_year": year, "completed": completed}
        if status != 200:
            errors.append({**ctx, "http_status": status})
            continue
        vs, c, s = check_response(body, completed, cat, ctx)
        n_checked += c
        n_skipped += s
        if vs:
            viol.extend(vs)
            bad.append(i)
        else:
            valid += 1
    return {
        "valid": valid, "total": n, "seed": seed, "http_errors": errors,
        "samples_with_violations": bad, "violations": viol,
        "courses_checked": n_checked, "placeholders_skipped": n_skipped,
        "completed_set_size": {"min": min(sizes), "max": max(sizes), "mean": round(sum(sizes) / len(sizes), 1)},
    }
