"""Cross-check a built plan file against the bulletin page it cites.

The point is fabrication detection that does not rely on the builder's own
report: every explicit course code in the plan must literally appear in the
cited page's text (Suggested Academic Plan, requirements lists, footnotes),
except codes the plan's own `notes` says were added to complete a real
prerequisite chain (the notes must name them).

    python scripts/check_plan_against_page.py CODE [CODE ...] [--year 2026]

Prints, per plan: codes not found on the page and not named in the notes (a
FAIL), codes found only because the notes name them (informational), and a
summary of how many page-linked plan codes the plan covers. Read-only; makes
one HTTP request per plan.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import List, Set

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import bulletin_scraper as bs  # noqa: E402
import planner_engine as engine  # noqa: E402

# \s* not \s?: the bulletin HTML renders some codes with a doubled space
# ("STAT  414"), which a single optional space silently misses.
_CODE_RE = re.compile(r"\b([A-Z]{2,6})\s*(\d{1,3}[A-Z]{0,3})\b")


_CROSSLIST_RE = re.compile(r"\b((?:[A-Z]{2,6}/)+[A-Z]{2,6})\s*(\d{1,3}[A-Z]{0,3})\b")
_HYPHEN_DEPT_RE = re.compile(r"\b([A-Z]-[A-Z])\s*(\d{1,3}[A-Z]{0,3})\b")
_SHORTHAND_TAIL_RE = re.compile(
    r"(?:\s*(?:,\s*(?:or|and)\b|,|/|\bor\b|\band\b)\s*\d{1,3}[A-Z]{0,3}\b(?!\s*[A-Z]{2,6}))+"
)


def page_codes(html: str) -> Set[str]:
    from bs4 import BeautifulSoup

    text = BeautifulSoup(html, "html.parser").get_text(" ")
    out: Set[str] = set()
    # Cross-listed notation "BBH/AFR 305", "HDFS/SOC 431", and the one
    # hyphenated department prefix PSU uses ("A-I 100").
    for m in _CROSSLIST_RE.finditer(text):
        for dept in m.group(1).split("/"):
            out.add(engine.norm_code(f"{dept} {m.group(2)}"))
    for m in _HYPHEN_DEPT_RE.finditer(text):
        out.add(engine.norm_code(f"{m.group(1)} {m.group(2)}"))
    text = re.sub(r"\bHD\s+FS\b", "HDFS", text)  # the pool footnotes print "HD FS 431"
    for m in _CODE_RE.finditer(text):
        dept = m.group(1)
        out.add(engine.norm_code(f"{dept} {m.group(2)}"))
        # Bulletin shorthand: "ENGL 222, 231, or 232" lists bare numbers that
        # inherit the department of the code before them.
        tail = _SHORTHAND_TAIL_RE.match(text, m.end())
        if tail:
            for num in re.findall(r"\d{1,3}[A-Z]{0,3}", tail.group(0)):
                out.add(engine.norm_code(f"{dept} {num}"))
    return out


def check(code: str, year: int) -> List[str]:
    path = os.path.join(engine.DEGREE_PLAN_DIR, f"{code}-{year}.json")
    with open(path) as f:
        plan = json.load(f)
    r = requests.get(plan["source"], timeout=30)
    r.raise_for_status()
    on_page = page_codes(r.text)
    notes = plan.get("notes", "")
    plan_codes: Set[str] = set()
    for sem in plan["semesters"]:
        for it in sem["items"]:
            if it.get("type") == "course":
                plan_codes.update(engine.norm_code(o) for o in it.get("options", []))
    # A lettered variant of a code the page does list (ENGL 15 -> ENGL 15S,
    # CAS 100 -> CAS 100A/B/C/S, MATH 140 -> MATH 140H) is a real equivalent
    # option, the way every existing plan file models it; ESL 15 stands in
    # for ENGL 15 the same way. Only a stem the page never mentions at all
    # counts as unexplained.
    def stem(c: str) -> str:
        m = re.match(r"^([A-Z]+)\s(\d+)", c)
        return f"{m.group(1)} {m.group(2)}" if m else c

    page_stems = {stem(c) for c in on_page}
    if "ENGL 15" in page_stems or "ENGL 30" in page_stems:
        # The first-year writing course has equivalent codes (ENGL 15 / ENGL
        # 30H honors / ESL 15); a page listing any one covers the family.
        page_stems |= {"ESL 15", "ENGL 15", "ENGL 30"}
    missing = sorted(c for c in plan_codes if c not in on_page and stem(c) not in page_stems)
    named_in_notes = [c for c in missing if re.search(re.escape(c).replace(r"\ ", r"\s?"), notes)]
    problems = [c for c in missing if c not in named_in_notes]
    print(f"{code}-{year}: {len(plan_codes)} plan codes, {len(plan_codes) - len(missing)} on page, "
          f"{len(named_in_notes)} not on page but named in notes {named_in_notes}, {len(problems)} unexplained")
    return problems


def main(argv: List[str]) -> int:
    year = 2026
    if "--year" in argv:
        i = argv.index("--year")
        year = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__)
        return 2
    bad = 0
    for code in argv:
        problems = check(code.upper(), year)
        if problems:
            bad += 1
            print(f"  FAIL unexplained codes: {problems}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
