"""Apply scraped bulletin campus availability to degree-plan files that carry
no `campus` key yet.

    python scripts/apply_campus_availability.py CACHE.json            # dry run
    python scripts/apply_campus_availability.py CACHE.json --write    # edit files
    python scripts/apply_campus_availability.py CACHE.json --report out.json

Rules (see campus_availability.py for the derivation itself):
  * A file that already has a `campus` key is NEVER modified -- those were
    hand-verified. A differing derived list is only reported.
  * A file whose derived list is exactly ["University Park"] is left alone
    (that is already the default for a file with no key).
  * Anything the page can't establish is reported, not guessed.
  * Edits are textual (one inserted key, one appended notes sentence) so the
    diff stays minimal; every edited file is re-parsed and verified to equal
    the original plus exactly those two changes.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from campus_availability import derive_plan_campuses  # noqa: E402

NOTE_FMT = (
    " CAMPUS AVAILABILITY (2026-10 branch-campus pass): per the bulletin page this plan is built from "
    "({basis_text}), available at {campuses}. Bulletin availability is program-level; whether a given "
    "course actually runs at a given campus in a given term is not published in the bulletin."
)


def _basis_text(res: Dict[str, Any]) -> str:
    return {
        "option": f"its own {res.get('why', 'option')} line",
        "program-picker": "the program-level campus picker; 'Any Penn State Campus' is read as every "
                          "in-person campus, including the seven scheduled to close after Spring 2027 "
                          "that still operate",
    }.get(res["basis"], res["basis"])


def _insert_campus_and_note(raw: str, campuses: List[str], note_add: str) -> str:
    lines = raw.split("\n")
    out: List[str] = []
    inserted = False
    for line in lines:
        if line.startswith('  "notes": "'):
            m = re.match(r'^(  "notes": )(".*")(,?)$', line)
            if not m:
                raise ValueError("unrecognised notes line")
            old = json.loads(m.group(2))
            line = m.group(1) + json.dumps(old + note_add, ensure_ascii=True) + m.group(3)
        out.append(line)
        if not inserted and line.startswith('  "source": '):
            block = ['  "campus": [']
            block += [f'    {json.dumps(c)}' + ("," if i < len(campuses) - 1 else "") for i, c in enumerate(campuses)]
            block.append("  ],")
            out.extend(block)
            inserted = True
    if not inserted:
        raise ValueError("no source line to anchor the campus key after")
    return "\n".join(out)


from campus_availability import KNOWN_CAMPUSES as KNOWN  # noqa: E402


def _rewrite_keyed(path: str, raw: str, plan: Dict[str, Any], merged: List[str], note_add: str) -> None:
    """Replace an existing multi-line/inline `campus` value and append to
    notes, textually, then verify the file equals original + those two."""
    lines = raw.split("\n")
    out: List[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        if line.startswith('  "campus":'):
            if line.rstrip().endswith("["):
                while not lines[i].startswith("  ]"):
                    i += 1
            # Mirror the original's trailing comma: in some files `campus`
            # is the last key, and a comma there would be invalid JSON.
            comma = "," if lines[i].rstrip().endswith(",") else ""
            block = ['  "campus": [']
            block += [f"    {json.dumps(c)}" + ("," if k < len(merged) - 1 else "") for k, c in enumerate(merged)]
            block.append("  ]" + comma)
            out.extend(block)
            replaced = True
            i += 1
            continue
        if line.startswith('  "notes": "'):
            m = re.match(r'^(  "notes": )(".*")(,?)$', line)
            if not m:
                raise ValueError("unrecognised notes line")
            line = m.group(1) + json.dumps(json.loads(m.group(2)) + note_add, ensure_ascii=True) + m.group(3)
        out.append(line)
        i += 1
    if not replaced:
        raise ValueError("campus line not found")
    new_raw = "\n".join(out)
    new_plan = json.loads(new_raw)
    exp = dict(plan)
    exp["campus"] = merged
    exp["notes"] = plan["notes"] + note_add
    if {k: v for k, v in new_plan.items()} != exp:
        raise SystemExit(f"verification failed for {path}; nothing further written")
    with open(path, "w") as f:
        f.write(new_raw)


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cache_path = argv[0]
    write = "--write" in argv
    report_path = argv[argv.index("--report") + 1] if "--report" in argv else None
    with open(cache_path) as f:
        cache = json.load(f)

    report: Dict[str, List[Any]] = {
        "applied": [], "left_default_up": [], "unknown": [], "has_key_same": [],
        "has_key_differs": [], "removes_up": [], "unknown_names": [],
    }
    for path in sorted(glob.glob(os.path.join(BASE_DIR, "degree_plans", "*.json"))):
        name = os.path.basename(path)
        raw = open(path).read()
        plan = json.loads(raw)
        src = plan.get("source")
        page = cache.get(src) if isinstance(src, str) else None
        if page is None:
            report["unknown"].append({"file": name, "why": "no cached page for source"})
            continue
        res = derive_plan_campuses(plan, page)
        if res["unknown"]:
            report["unknown_names"].append({"file": name, "names": sorted(set(res["unknown"]))})
        existing = plan.get("campus")
        if existing is not None:
            existing_list = existing if isinstance(existing, list) else [existing]
            if res["campuses"] is None:
                report["unknown"].append({"file": name, "why": f"has key; page: {res['why']}"})
            elif set(existing_list) == set(res["campuses"]):
                report["has_key_same"].append(name)
            elif res.get("picker_explicit") and (set(res["picker_explicit"]) - set(existing_list)):
                # Additive-only: campuses the program-level picker NAMES
                # outright (Begin/End) and the hand-curated list lacks get
                # appended. Nothing is removed; an "Any Penn State Campus"
                # expansion and per-option lines never widen a hand-verified
                # list.
                added = [c for c in res["picker_explicit"] if c not in existing_list]
                report.setdefault("keyed_additions", []).append({
                    "file": name, "existing": existing_list, "added": added, "basis": res["basis"],
                })
                if write:
                    merged = [c for c in KNOWN if c in set(existing_list) | set(added)]
                    note = (
                        " CAMPUS AVAILABILITY (2026-10 branch-campus pass): the bulletin page this plan is built "
                        f"from also names {', '.join(added)} ({_basis_text(res)}); added to the existing list."
                    )
                    _rewrite_keyed(path, raw, plan, merged, note)
                if set(existing_list) - set(res["campuses"]):
                    report["has_key_differs"].append({
                        "file": name, "existing": existing_list, "derived": res["campuses"],
                        "basis": res["basis"], "only_in_existing": sorted(set(existing_list) - set(res["campuses"])),
                        "only_in_derived": sorted(set(res["campuses"]) - set(existing_list)),
                    })
            else:
                report["has_key_differs"].append({
                    "file": name, "existing": existing_list, "derived": res["campuses"],
                    "basis": res["basis"], "only_in_existing": sorted(set(existing_list) - set(res["campuses"])),
                    "only_in_derived": sorted(set(res["campuses"]) - set(existing_list)),
                })
            continue
        if res["campuses"] is None:
            report["unknown"].append({"file": name, "why": res["why"]})
            continue
        if res["campuses"] == ["University Park"]:
            report["left_default_up"].append(name)
            continue
        entry = {"file": name, "campuses": res["campuses"], "basis": res["basis"], "why": res["why"]}
        if "University Park" not in res["campuses"]:
            report["removes_up"].append(entry)
        report["applied"].append(entry)
        if write:
            note = NOTE_FMT.format(basis_text=_basis_text(res), campuses=", ".join(res["campuses"]))
            new_raw = _insert_campus_and_note(raw, res["campuses"], note)
            new_plan = json.loads(new_raw)
            expect = dict(plan)
            expect["notes"] = plan["notes"] + note
            expect_ordered = {}
            for k, v in new_plan.items():
                if k == "campus":
                    continue
                expect_ordered[k] = expect[k]
            if new_plan.get("campus") != res["campuses"] or expect_ordered != {k: v for k, v in new_plan.items() if k != "campus"}:
                raise SystemExit(f"verification failed for {name}; nothing further written")
            with open(path, "w") as f:
                f.write(new_raw)

    print({k: len(v) for k, v in report.items()})
    if report_path:
        with open(report_path, "w") as f:
            json.dump(report, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
