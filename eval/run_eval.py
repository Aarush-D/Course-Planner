#!/usr/bin/env python3
"""Evaluation harness for the Coursy backend.

    python eval/run_eval.py                       # all four evals
    python eval/run_eval.py --only parser,plans   # the deterministic two (what CI runs)
    python eval/run_eval.py --llm-prompts 10      # smaller LLM run

Evals
  1. parser  -- labeled synthetic student messages -> extracted state
  2. plans   -- every degree plan + N random completed sets, checked by an
                independent prerequisite checker (independent_checker.py)
  3. llm     -- reply faithfulness and plan stability, LLM on vs off
  4. rag     -- Backend/rag_eval.py Recall@k

Uses Flask's test client; no server is started. Nothing in Backend/ or
Frontend/ is modified, and no numbers are adjusted: every metric is reported
as measured, with numerator and denominator.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "Backend")

# Reproducibility: pin string-hash ordering. (Plan validity is the same under
# other seeds -- checked -- but graph.edges ordering is not.)
if os.environ.get("PYTHONHASHSEED") != "0":
    os.execve(sys.executable, [sys.executable] + sys.argv, {**os.environ, "PYTHONHASHSEED": "0"})

os.environ["USE_OLLAMA"] = "0"          # evals 1 and 2 must not depend on an LLM
os.environ.setdefault("RATELIMIT_ENABLED", "0")
sys.path.insert(0, HERE)
sys.path.insert(0, BACKEND)

EVALS = ("parser", "plans", "llm", "rag")


def git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return ""


def breakdown(violations, backend_dir):
    """Diagnostic grouping of violations by apparent cause. This does NOT
    change any count; it only helps a reader see what the strict checker
    flagged. Categories are mutually exclusive and evaluated in this order."""
    depts_cache = {}

    def plan_depts(major, year):
        k = (major, year)
        if k not in depts_cache:
            try:
                with open(os.path.join(backend_dir, "degree_plans", f"{major}-{year}.json")) as f:
                    depts_cache[k] = set(json.load(f).get("departments", []))
            except OSError:
                depts_cache[k] = set()
        return depts_cache[k]

    cats = collections.Counter()
    by_course = collections.Counter()
    for v in violations:
        dept = v["course"].split()[0]
        if v["developmental_math_gap"]:
            c = "developmental_math_gap (placement-waivable by PSU policy, strict checker does not waive)"
        elif dept not in plan_depts(v["major"], v["catalog_year"]):
            c = "course_department_not_in_plan_departments (engine's merged catalog does not contain the course)"
        elif v["kind"] == "concurrent":
            c = "corequisite_partner_not_scheduled_or_completed"
        else:
            c = "other_prerequisite_gap"
        cats[c] += 1
        by_course[f"{v['course']} [{v['kind']}]"] += 1
    return {"rows_by_category": dict(cats), "rows_by_course_top25": dict(by_course.most_common(25))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=",".join(EVALS), help="comma list of: " + ",".join(EVALS))
    ap.add_argument("--random-sets", type=int, default=250)
    ap.add_argument("--seed", type=int, default=20261015)
    ap.add_argument("--llm-prompts", type=int, default=None, help="limit LLM prompts (default: all 60)")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "latest.json"))
    args = ap.parse_args()
    wanted = [e.strip() for e in args.only.split(",") if e.strip()]
    for e in wanted:
        if e not in EVALS:
            ap.error(f"unknown eval {e!r}")

    os.chdir(BACKEND)
    results = {
        "date_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git("rev-parse", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--", "Backend", "eval", ":(exclude)eval/results")),
        "python": sys.version.split()[0],
        "evals_run": wanted,
        "metrics": {},
    }

    client = None
    if {"parser", "plans"} & set(wanted):
        import app as backend_app
        client = backend_app.app.test_client()

    if "parser" in wanted:
        import parser_eval
        r = parser_eval.run(client, os.path.join(HERE, "data", "parser_cases.json"))
        results["metrics"]["parser"] = r
        print(f"[1/4] parser: exact match {r['exact_match']['numerator']}/{r['exact_match']['denominator']}", flush=True)

    if "plans" in wanted:
        import plan_validity as pv
        from independent_checker import Catalog
        cat = Catalog(BACKEND)
        sw = pv.sweep(client, BACKEND, cat)
        rs = pv.random_sets(client, BACKEND, cat, args.random_sets, args.seed)
        for blk in (sw, rs):
            blk["violation_breakdown"] = breakdown(blk["violations"], BACKEND)

        def valid_ignoring_dev_math(blk, key_fn):
            bad_units = {key_fn(v) for v in blk["violations"] if not v["developmental_math_gap"]}
            return blk["total"] - len(bad_units) - len(blk["http_errors"])
        sw["valid_if_developmental_math_gaps_are_waived"] = {
            "numerator": valid_ignoring_dev_math(sw, lambda v: (v["major"], v["catalog_year"])),
            "denominator": sw["total"]}
        rs["valid_if_developmental_math_gaps_are_waived"] = {
            "numerator": valid_ignoring_dev_math(rs, lambda v: v["sample"]), "denominator": rs["total"]}
        # keep each violation row compact; the completed sets live once per sample
        for v in rs["violations"]:
            v.pop("completed", None)
        results["metrics"]["plan_validity"] = {
            "checker": "eval/independent_checker.py (reads raw catalog JSON; imports nothing from the engine)",
            "degree_plan_sweep": sw, "random_completed_sets": rs,
        }
        print(f"[2/4] plans: sweep {sw['valid']}/{sw['total']} valid, random {rs['valid']}/{rs['total']} valid", flush=True)

    if "llm" in wanted:
        import llm_eval
        r = llm_eval.run(os.path.join(HERE, "data", "llm_prompts.json"), BACKEND, args.llm_prompts)
        results["metrics"]["llm_faithfulness"] = r
        if r.get("skipped"):
            print(f"[3/4] llm: SKIPPED -- {r['reason']}", flush=True)
        else:
            a = r["a_raw_model_reply_mentions_code_not_in_verified_facts"]
            s_ = r["a_reply_shown_to_student_mentions_code_not_in_verified_facts"]
            g = r["raw_model_replies_discarded_by_app_guard"]
            b = r["b_plan_differs_llm_on_vs_off"]
            print(f"[3/4] llm: raw replies with unsupported code {a['numerator']}/{a['denominator']} "
                  f"(guard discarded {g['numerator']}/{g['denominator']}); shown to student "
                  f"{s_['numerator']}/{s_['denominator']}; plan differs {b['numerator']}/{b['denominator']}",
                  flush=True)

    if "rag" in wanted:
        try:
            import rag_eval
            index = rag_eval.load_index(rag_eval.INDEX_PATH)
            res = rag_eval.run_eval(index, rag_eval.RETRIEVAL_K)
            total = len(res)
            hits = [x for x in res if x["hit_rank"] is not None]
            ranks = collections.Counter(x["hit_rank"] for x in hits)
            results["metrics"]["rag_retrieval"] = {
                "skipped": False, "k": rag_eval.RETRIEVAL_K, "questions": total,
                "recall_at_k": {"numerator": len(hits), "denominator": total,
                                "rate": round(len(hits) / total, 4) if total else None},
                "hit_rank_distribution": {str(r): ranks[r] for r in sorted(ranks)},
                "embedding_model": "nomic-embed-text (Ollama)",
                "missed": [{"question": x["question"], "expected": x["expected"]}
                           for x in res if x["hit_rank"] is None],
            }
            print(f"[4/4] rag: Recall@{rag_eval.RETRIEVAL_K} {len(hits)}/{total}", flush=True)
        except Exception as e:  # noqa: BLE001
            results["metrics"]["rag_retrieval"] = {"skipped": True, "reason": f"{type(e).__name__}: {e}"}
            print(f"[4/4] rag: SKIPPED -- {type(e).__name__}: {e}", flush=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=1)
    print(f"wrote {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
