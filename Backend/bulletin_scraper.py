"""Deterministic scraper for PSU bulletin PROGRAM pages -- the "Suggested
Academic Plan" table specifically -- as opposed to Courseplanner.py's
scrape_psu_dept_catalog(), which scrapes per-department COURSE
DESCRIPTIONS.

Why this exists: when an agent reads a bulletin page's rendered text to
reconstruct a degree plan by hand, it can invent a plausible-looking but
nonexistent course code. This happened for real during this project's own
catalog-year backfill -- a Haiku-tier builder invented "ART 420", "ART
445", "ART 490", and "ART 471" for the Art major, none of which exist
anywhere in PSU's real catalog, and the planner engine's own
self-verification reported 0 warnings anyway, because build_full_plan
schedules whatever code is in the JSON without checking it's real.

The fix here is structural, not a smarter prompt. PSU's own bulletin HTML
renders every REAL course code as a clickable <a> link to that course's
description page (confirmed by inspecting the live CMPSC bulletin page's
sc_plangrid table directly: "CMPSC 121 or 131" renders as two separate <a>
tags, "General Education Course" and "First-Year Seminar" render with no
link at all). Parsing the plan table and keeping ONLY <a>-linked text as a
candidate course code -- then cross-checking each one against the
already-scraped, real per-department catalogs -- makes fabrication
structurally impossible: a scraper can't invent a link that isn't in the
HTML it was given.

This module deliberately does NOT try to fully understand every bulletin
page's layout or produce a final degree_plans/*.json file by itself --
PSU's own pages are real and messy (some majors have no Suggested Academic
Plan table at all; see ARCBS/ENVSE's own notes elsewhere in this repo).
It does one job well: turn a bulletin URL into a structured list of
(semester, item) pairs where every claimed course code is either a real,
verified link or explicitly flagged as unverified -- and leaves the
judgment calls (which option to build, how to handle a stale code) to
whoever consumes this output.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from course_codes import norm_code

_CODE_WITH_DEPT_RE = re.compile(r"^([A-Z]{2,6})\s?(\d{1,3}[A-Z]{0,2})$")
_CODE_BARE_NUMBER_RE = re.compile(r"^(\d{1,3}[A-Z]{0,2})$")


@dataclass
class PlanItem:
    raw_text: str
    linked_codes: List[str] = field(default_factory=list)
    credits: Optional[float] = None
    is_generic_slot: bool = True


@dataclass
class PlanSemester:
    term_label: str
    items: List[PlanItem] = field(default_factory=list)
    subtotal_credits: Optional[float] = None


def archive_url_for_year(live_url: str, year: int, live_year: int) -> str:
    """Transform a live bulletin URL into that catalog year's real archive
    URL (bulletins.psu.edu/archive/{year}-{year+1}/...), or return the
    live URL unchanged if `year` is the current live year. This pattern
    is already real and fetchable -- every multi-year plan file already
    in this repo (e.g. CMPSC-2022.json) has a "source" field built exactly
    this way."""
    if year >= live_year:
        return live_url
    parsed = urlparse(live_url)
    marker = "/undergraduate/"
    idx = parsed.path.find(marker)
    if idx == -1:
        raise ValueError(f"Unexpected bulletin URL shape, no '{marker}' segment: {live_url}")
    suffix = parsed.path[idx + len(marker):]
    return f"{parsed.scheme}://{parsed.netloc}/archive/{year}-{year + 1}/undergraduate/{suffix}"


def fetch_bulletin_html(url: str, timeout: int = 30) -> Tuple[bool, str]:
    """Fetch a bulletin page. Returns (ok, html_or_error_message). Never
    raises -- a 404/network failure is a real, expected outcome (e.g. a
    major that didn't exist yet in an older catalog year, confirmed for
    real majors like NEURO/AIMA in this repo's own history) that callers
    must handle explicitly, not an exception to catch ad hoc."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        return False, f"request failed: {e}"
    if resp.status_code == 404:
        return False, "404 Not Found"
    if resp.status_code != 200:
        return False, f"unexpected status {resp.status_code}"
    return True, resp.text


def parse_total_credits(html: str) -> Optional[str]:
    """Extract the bulletin's own printed 'Total Credits NNN' or
    'Total Credits NNN-MMM' line, verbatim, as a cross-check anchor for
    whoever builds the final plan file."""
    m = re.search(r"Total\s+Credits?\s+(\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)", html, re.IGNORECASE)
    return m.group(1) if m else None


def _extract_linked_codes(cell) -> List[str]:
    """Real course codes ONLY -- every entry here corresponds to an actual
    <a> tag in the page's own HTML, never inferred from surrounding text.
    Handles PSU's real "121 or 131" shorthand (a bare number reuses the
    previous linked code's department prefix within the same cell)."""
    codes: List[str] = []
    last_dept: Optional[str] = None
    for a in cell.find_all("a"):
        txt = norm_code(a.get_text(strip=True))
        m = _CODE_WITH_DEPT_RE.match(txt.replace(" ", ""))
        if m:
            last_dept = m.group(1)
            codes.append(norm_code(f"{m.group(1)} {m.group(2)}"))
            continue
        m2 = _CODE_BARE_NUMBER_RE.match(txt.replace(" ", ""))
        if m2 and last_dept:
            codes.append(norm_code(f"{last_dept} {m2.group(1)}"))
    return codes


def _select_best_plan_table(soup) -> Optional[object]:
    """When a page has multiple plan tables (different specializations/campuses),
    select the most likely correct one. Strategy:
    1. If there's only one table, return it
    2. If there are multiple tables, identify which specialization by checking for
       signature courses:
       - MDE (Multidisciplinary Engineering Design): has all of EDSGN 401/402/403/410,
         EE 310/316, CMPEN 271. If multiple MDE tables exist (Abington, Brandywine),
         prefer Brandywine.
       - Otherwise return the first table (most pages have only one).
    This handles the case where Engineering BS has multiple options (MDE, Applied
    Materials, Alternative Energy, Design & Innovation) each with their own table."""
    tables = soup.find_all("table", class_="sc_plangrid")
    if not tables:
        return None
    if len(tables) == 1:
        return tables[0]

    # Multiple tables found. Try to identify the best one.
    # For ENGR programs with MDE option, look for ALL MDE signature courses.
    mde_signatures = {'EDSGN 401', 'EDSGN 402', 'EDSGN 403', 'EDSGN 410',
                      'EE 310', 'EE 316', 'CMPEN 271'}
    mde_candidates = []

    for table in tables:
        # Extract all linked codes from this table
        codes = set()
        for link in table.find_all("a"):
            text = link.get_text(strip=True)
            if text:
                codes.add(text.replace('\xa0', ' '))

        # Check if this table has ALL MDE signature courses
        if mde_signatures <= codes:
            # This is an MDE table. Get its context to check for campus.
            prev_heading = table.find_previous(['h3', 'h4'])
            context = prev_heading.get_text(strip=True) if prev_heading else ""
            is_brandywine = 'brandywine' in context.lower()
            mde_candidates.append((is_brandywine, table, context))

    if mde_candidates:
        # Prefer Brandywine if available, otherwise use first MDE table
        mde_candidates.sort(key=lambda x: not x[0])  # Sort with Brandywine first
        return mde_candidates[0][1]

    # If no MDE signature table found, return the first table
    return tables[0]


def parse_suggested_plan(html: str) -> dict:
    """Parse the <table class="sc_plangrid"> Suggested Academic Plan table
    into structured semesters. Every candidate course code comes ONLY from
    a real <a> hyperlink inside a plan-grid cell -- unlinked text in that
    same cell (footnote markers, "or", parenthetical Gen Ed tags) is never
    treated as a course code, and a cell with no real links at all is
    reported as a generic slot verbatim, never guessed at.

    When a page contains multiple plan tables (e.g., different specializations),
    prefers the MDE (Multidisciplinary Engineering Design) option table if present.

    Returns a dict:
        ok: bool -- False if this page genuinely has no such table (some
            majors really don't publish one; verify by eye before assuming
            this is a scraper bug, not a real gap).
        error: str | None
        semesters: [{"term_label", "items": [...], "subtotal_credits"}]
        items: [{"raw_text", "linked_codes", "credits", "is_generic_slot"}]
        printed_total_credits: str | None -- the bulletin's own stated total,
            for cross-checking against the sum of parsed subtotals.
    """
    printed_total = parse_total_credits(html)
    soup = BeautifulSoup(html, "html.parser")
    table = _select_best_plan_table(soup)
    if table is None:
        return {
            "ok": False,
            "error": (
                "no sc_plangrid table found on this page -- some majors "
                "genuinely have no Suggested Academic Plan section "
                "(confirmed for real majors elsewhere in this repo); do "
                "not fabricate a semester sequence to fill this gap"
            ),
            "semesters": [],
            "printed_total_credits": printed_total,
        }

    semesters: List[PlanSemester] = []
    current_pair: List[PlanSemester] = []

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        if len(cells) == 1:
            continue  # year-group header row, e.g. "First Year" -- informational only

        first_classes = cells[0].get("class") or []
        if "plangridtermhdr" in first_classes:
            term_cells = [c for c in cells if "plangridtermhdr" in (c.get("class") or [])]
            current_pair = [PlanSemester(term_label=c.get_text(strip=True)) for c in term_cells]
            semesters.extend(current_pair)
            continue

        if not current_pair:
            continue  # a row before any term header -- skip rather than guess

        # Pair cells into (term_idx, code_cell, hours_cell) in document
        # order, tracking term_idx explicitly rather than assuming every
        # row has one (code, hours) pair per term in strict left-to-right
        # alternation. PSU's real HTML uses a blank <td colspan="2"></td>
        # placeholder when one term has no entry for this particular grid
        # row but another term does (e.g. Fall has 7 courses, Spring has
        # 8) -- confirmed by direct inspection of a real archived page
        # (Acting BFA 2022-23, First Year Spring's 8th row). Treating that
        # placeholder as "just another cell" made the old positional
        # alternation misattribute the next real item to the wrong term
        # column entirely; skipping it by its own colspan keeps term_idx
        # correct instead.
        pairs = []
        term_idx = 0
        i = 0
        while i < len(cells):
            cell = cells[i]
            cls = cell.get("class") or []
            colspan = 1
            try:
                colspan = int(cell.get("colspan", 1) or 1)
            except ValueError:
                colspan = 1
            if colspan >= 2 and "hourscol" not in cls and cell.get_text(strip=True) == "":
                term_idx += max(1, colspan // 2)
                i += 1
                continue
            if i + 1 < len(cells):
                c1, c2 = cells[i], cells[i + 1]
                c1_cls, c2_cls = (c1.get("class") or []), (c2.get("class") or [])
                if "hourscol" not in c1_cls and "hourscol" in c2_cls:
                    pairs.append((term_idx, c1, c2))
                    term_idx += 1
                    i += 2
                    continue
            i += 1

        for sem_idx, code_cell, hours_cell in pairs:
            if sem_idx >= len(current_pair):
                continue
            raw_text = code_cell.get_text(" ", strip=True).replace("\xa0", " ")
            hours_text = hours_cell.get_text(strip=True)
            credits = None
            m = re.search(r"\d+(?:\.\d+)?", hours_text)
            if m:
                credits = float(m.group(0))

            if raw_text == "":
                if credits is not None:
                    current_pair[sem_idx].subtotal_credits = credits
                continue

            linked_codes = _extract_linked_codes(code_cell)
            current_pair[sem_idx].items.append(PlanItem(
                raw_text=raw_text,
                linked_codes=linked_codes,
                credits=credits,
                is_generic_slot=(len(linked_codes) == 0),
            ))

    return {
        "ok": True,
        "error": None,
        "semesters": [
            {
                "term_label": s.term_label,
                "subtotal_credits": s.subtotal_credits,
                "items": [
                    {
                        "raw_text": it.raw_text,
                        "linked_codes": it.linked_codes,
                        "credits": it.credits,
                        "is_generic_slot": it.is_generic_slot,
                    }
                    for it in s.items
                ],
            }
            for s in semesters
        ],
        "printed_total_credits": printed_total,
    }


def verify_codes_against_catalog(codes: Set[str], catalog: Dict[str, object]) -> Dict[str, bool]:
    """For each candidate code (any casing/spacing), report whether it's a
    real, known course in the given catalog dict (keyed by norm_code(),
    e.g. the output of engine.load_full_catalog() or
    engine.load_merged_catalog(departments)). A code that comes back False
    is either a genuinely stale/renumbered bulletin entry (needs a human
    or agent citation, same convention already used throughout this repo
    for e.g. "AFR 110" vs the real "AFR 110N") or a real scraper/fabrication
    problem -- never silently trust an unverified code into a plan file."""
    return {code: norm_code(code) in catalog for code in codes}


def scrape_and_verify(url: str, catalog: Dict[str, object]) -> dict:
    """Convenience wrapper: fetch, parse, and verify every linked code in
    one call. Returns parse_suggested_plan()'s dict plus an added
    "unverified_codes" list -- linked codes that don't resolve against the
    given catalog, which the caller MUST investigate (cite a real source
    for the discrepancy) rather than drop silently or trust blindly."""
    ok, html_or_err = fetch_bulletin_html(url)
    if not ok:
        return {"ok": False, "error": html_or_err, "semesters": [], "printed_total_credits": None, "unverified_codes": []}

    result = parse_suggested_plan(html_or_err)
    all_codes: Set[str] = set()
    for sem in result["semesters"]:
        for item in sem["items"]:
            all_codes.update(item["linked_codes"])

    verified = verify_codes_against_catalog(all_codes, catalog)
    result["unverified_codes"] = sorted(code for code, ok in verified.items() if not ok)
    return result


if __name__ == "__main__":
    import sys
    import json as _json

    if len(sys.argv) < 2:
        print("usage: python3 bulletin_scraper.py <bulletin_url> [year live_year]")
        sys.exit(1)

    target_url = sys.argv[1]
    if len(sys.argv) >= 4:
        target_url = archive_url_for_year(target_url, int(sys.argv[2]), int(sys.argv[3]))
        print(f"resolved archive URL: {target_url}", file=sys.stderr)

    ok, html_or_err = fetch_bulletin_html(target_url)
    if not ok:
        print(_json.dumps({"ok": False, "error": html_or_err}, indent=2))
        sys.exit(1)

    parsed = parse_suggested_plan(html_or_err)

    try:
        import planner_engine as engine
        full_catalog = engine.load_full_catalog()
        codes: Set[str] = set()
        for sem in parsed["semesters"]:
            for item in sem["items"]:
                codes.update(item["linked_codes"])
        verified = verify_codes_against_catalog(codes, full_catalog)
        parsed["unverified_codes"] = sorted(c for c, v in verified.items() if not v)
    except Exception as e:
        parsed["verification_error"] = f"could not load engine catalog for verification: {e}"

    print(_json.dumps(parsed, indent=2))
