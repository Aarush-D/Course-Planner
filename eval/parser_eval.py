"""Eval 1 -- parser accuracy on labeled (synthetic) student messages.

Every case goes through the real route (POST /api/plan via Flask's test
client), so this measures what a student's message actually does to the app's
state, not a single regex in isolation.
"""
from __future__ import annotations

import collections
import json
from typing import Any, Dict, List

FIELDS = ("major", "start_year", "added", "removed", "wanted", "excluded")
SET_FIELDS = ("added", "removed", "wanted", "excluded")


def _expected(case: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    ctx = {**defaults, **case.get("context", {})}
    exp = case.get("expected", {})
    return {
        "major": exp.get("major", ctx["major"]),
        "start_year": exp.get("start_year", ctx["start_year"]),
        "added": sorted(exp.get("added", [])),
        "removed": sorted(exp.get("removed", [])),
        "wanted": sorted(exp.get("wanted", [])),
        "excluded": sorted(exp.get("excluded", [])),
    }


def _observe(client, case: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    ctx = {**defaults, **case.get("context", {})}
    r = client.post("/api/plan", json={
        "prompt": case["message"], "completed": list(ctx["completed"]),
        "major": ctx["major"], "start_year": ctx["start_year"], "grad_years": 4,
    })
    if r.status_code != 200:
        return {"_http_status": r.status_code, "_error": (r.get_json(silent=True) or {}).get("error")}
    state = (r.get_json() or {}).get("state", {})
    before = set(ctx["completed"])
    after = set(state.get("completed", []))
    return {
        "major": state.get("dept"),
        "start_year": state.get("startYear"),
        "added": sorted(after - before),
        "removed": sorted(before - after),
        "wanted": sorted(state.get("wantedCourses", [])),
        "excluded": sorted(state.get("excludedCourses", [])),
    }


def run(client, dataset_path: str) -> Dict[str, Any]:
    with open(dataset_path) as f:
        data = json.load(f)
    defaults = data["meta"]["defaults"]
    cases = data["cases"]
    n = len(cases)

    exact = 0
    field_correct = collections.Counter()
    # non-default = the label expects something to change in this field
    nd_total = collections.Counter()
    nd_correct = collections.Counter()
    # default = the label expects NO change; a wrong value is a false positive
    d_total = collections.Counter()
    d_wrong = collections.Counter()
    tp = collections.Counter(); fp = collections.Counter(); fn = collections.Counter()
    by_tag_total = collections.Counter(); by_tag_exact = collections.Counter()
    failures: List[Dict[str, Any]] = []
    http_errors = 0

    for case in cases:
        exp = _expected(case, defaults)
        obs = _observe(client, case, defaults)
        ctx = {**defaults, **case.get("context", {})}
        if "_http_status" in obs:
            http_errors += 1
            failures.append({"id": case["id"], "message": case["message"], "http_status": obs["_http_status"],
                             "error": obs.get("_error"), "expected": exp})
            for t in case["tags"]:
                by_tag_total[t] += 1
            for fld in FIELDS:
                nd = exp[fld] != (ctx["major"] if fld == "major" else ctx["start_year"] if fld == "start_year" else [])
                (nd_total if nd else d_total)[fld] += 1
                if not nd:
                    d_wrong[fld] += 1
            continue
        wrong = []
        for fld in FIELDS:
            ok = obs[fld] == exp[fld]
            field_correct[fld] += ok
            default_val = ctx["major"] if fld == "major" else ctx["start_year"] if fld == "start_year" else []
            if exp[fld] != default_val:
                nd_total[fld] += 1
                nd_correct[fld] += ok
            else:
                d_total[fld] += 1
                d_wrong[fld] += (not ok)
            if not ok:
                wrong.append(fld)
            if fld in SET_FIELDS:
                e, o = set(exp[fld]), set(obs[fld])
                tp[fld] += len(e & o); fp[fld] += len(o - e); fn[fld] += len(e - o)
        is_exact = not wrong
        exact += is_exact
        for t in case["tags"]:
            by_tag_total[t] += 1
            by_tag_exact[t] += is_exact
        if wrong:
            failures.append({"id": case["id"], "message": case["message"], "tags": case["tags"],
                             "wrong_fields": wrong, "expected": {k: exp[k] for k in wrong},
                             "observed": {k: obs[k] for k in wrong}})

    def frac(num, den):
        return {"numerator": int(num), "denominator": int(den), "rate": (round(num / den, 4) if den else None)}

    return {
        "dataset": {"path": dataset_path.split("eval/")[-1], "cases": n, "synthetic": data["meta"]["synthetic"],
                    "real_student_data": data["meta"]["real_student_data"]},
        "exact_match": frac(exact, n),
        "per_field_accuracy": {f: frac(field_correct[f], n) for f in FIELDS},
        "per_field_accuracy_when_a_change_is_expected": {f: frac(nd_correct[f], nd_total[f]) for f in FIELDS},
        "per_field_false_change_rate_when_no_change_expected": {f: frac(d_wrong[f], d_total[f]) for f in FIELDS},
        "item_level_set_fields": {
            f: {"true_positive": tp[f], "false_positive": fp[f], "false_negative": fn[f],
                "precision": frac(tp[f], tp[f] + fp[f]), "recall": frac(tp[f], tp[f] + fn[f])}
            for f in SET_FIELDS
        },
        "exact_match_by_tag": {t: frac(by_tag_exact[t], by_tag_total[t]) for t in sorted(by_tag_total)},
        "http_errors": http_errors,
        "failures": failures,
    }
