"""One-off scraper for PSU's GenEd Learning Objective tags (Faculty Senate
Policy 141-00).

These are separate, purely descriptive metadata from the Gen Ed domain
system (GQ/GWS/GA/GHW/GH/GN/GS/INTER-D/IL/US) already scraped by
scrape_gen_ed.py into data/gen_ed_courses.json. A Gen Ed-eligible course's
bulletin page also lists which of the 7 Learning Objectives it satisfies,
as lines like "GenEd Learning Objective: Crit and Analytical Think" inside
the same courseblockextra block scrape_psu_dept_catalog() already visits
(see Courseplanner.py). PSU spells some objectives out in full on the page
and abbreviates others -- LEARNING_OBJECTIVE_MAP below was built from a
live sample of real scraped output, not guessed; see the module-level
comment above it for how it was derived.

Scoped to exactly the 2,705 course codes / 196 departments already present
in data/gen_ed_courses.json's 10 domain lists (computed once and hardcoded
below as DEPARTMENTS, to avoid re-deriving it and to keep the two scrapers'
scope identical even if gen_ed_courses.json changes later).

Run manually (not part of the app's request path):
    cd Backend && python3 scripts/scrape_gen_ed_learning_objectives.py
Writes Backend/data/gen_ed_learning_objectives.json, mapping each
normalized course code to a list of full canonical objective-name strings.
Re-run whenever the bulletin's Gen Ed / Learning Objective tags change.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from bs4 import BeautifulSoup

# Backend/ itself (this file lives in Backend/scripts/) -- added to sys.path
# so `from Courseplanner import psu_dept_url` works regardless of the cwd
# this script happens to be launched from.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)
from Courseplanner import psu_dept_url  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; PSU-CoursePlanner-Research/1.0)"
REQUEST_DELAY_SECONDS = 0.3

OUT_PATH = os.path.join(_BACKEND_DIR, "data", "gen_ed_learning_objectives.json")
GEN_ED_COURSES_PATH = os.path.join(_BACKEND_DIR, "data", "gen_ed_courses.json")

# The 196 department prefixes covering every one of the 2,705 course codes
# across gen_ed_courses.json's 10 domain lists (GQ/GWS/GA/GHW/GH/GN/GS/
# INTER-D/IL/US), computed by extracting the prefix from each listed code.
# Hardcoded rather than recomputed at run time so this scraper's scope
# stays fixed even if gen_ed_courses.json is later re-scraped and changes.
DEPARTMENTS = [
    "AA", "AAS", "ABE", "ABSM", "ACCTG", "ADTED", "AED", "AEE", "AERSP", "AFAM",
    "AFR", "AG", "AGBM", "AGECO", "AGSC", "AIR", "AMST", "ANSC", "ANTH", "APLNG",
    "ARAB", "ARCH", "ART", "ARTH", "ASIA", "ASTRO", "AYFCE", "BA", "BBH", "BE",
    "BESC", "BIOET", "BIOL", "BISC", "BLAW", "BMB", "BME", "BRASS", "BRS", "CAMS",
    "CAS", "CC", "CE", "CED", "CHE", "CHEM", "CHNS", "CI", "CIED", "CIVCM",
    "CMAS", "CMLIT", "CMPEN", "CMPSC", "COMM", "CRIM", "CRIMJ", "CSD", "CYBER", "DA",
    "DANCE", "DART", "DIGIT", "DS", "EARTH", "EBF", "ECON", "EDPSY", "EDSGN", "EDTHP",
    "EDUC", "EE", "EGEE", "EME", "EMSC", "ENGL", "ENGR", "ENT", "ENVST", "ERM",
    "ESC", "ESL", "ETI", "FDSC", "FIN", "FOR", "FORT", "FR", "FRNAR", "FRNSC",
    "GAME", "GD", "GEOG", "GEOSC", "GER", "GLIS", "GREEK", "HCDD", "HDFS", "HEBR",
    "HHD", "HHUM", "HIST", "HM", "HONOR", "HORT", "HPA", "HRER", "HUM", "IB",
    "IE", "IEC", "INART", "INTAG", "INTST", "IST", "IT", "JAPNS", "JST", "KEYBD",
    "KINES", "KOR", "LA", "LANG", "LARCH", "LATIN", "LDT", "LER", "LHR", "LING",
    "LLED", "LTNST", "MATH", "MATSE", "ME", "MEDVL", "METEO", "MGMT", "MICRB", "MIS",
    "MKTG", "MUSIC", "NUCE", "NURS", "NUTR", "OLEAD", "OT", "PERCN", "PHIL", "PHOTO",
    "PHYS", "PLANT", "PLET", "PLSC", "POL", "PORT", "PPEM", "PSYCH", "PUBH", "PUBPL",
    "RHS", "RLST", "RM", "RPTM", "RSOC", "RUS", "SC", "SCIED", "SCM", "SLAV",
    "SOC", "SOCW", "SODA", "SOILS", "SPAN", "SPLED", "SPSY", "SRA", "SSED", "STAT",
    "STRNG", "STS", "SUST", "SWA", "SWENG", "THEA", "TURF", "UKR", "VBSC", "VOICE",
    "WFED", "WFS", "WGSS", "WMNST", "WP", "WWNDS",
]

LEARNING_OBJECTIVE_PREFIX = "GenEd Learning Objective:"

# PSU's abbreviated form (as it literally appears on the bulletin page,
# after the "GenEd Learning Objective: " prefix) -> the full canonical
# name from Faculty Senate Policy 141-00. Verified live against a sample
# of real scraped output (20 departments -- ENGL, MATH, HIST, ART, PHIL,
# KINES, CMPSC, PSYCH, COMM, MUSIC, DANCE, THEA, BIOL, CHEM, SOC, ECON,
# PHYS, STAT, WGSS, RLST -- which surfaced all 7 distinct forms and no
# others). Only 2 of the 7 are actually abbreviated on the page; the
# other 5 are spelled out in full already, so they map to themselves.
LEARNING_OBJECTIVE_MAP = {
    "Effective Communication": "Effective Communication",
    "Key Literacies": "Key Literacies",
    "Crit and Analytical Think": "Critical and Analytical Thinking",
    "Integrative Thinking": "Integrative Thinking",
    "Creative Thinking": "Creative Thinking",
    "Global Learning": "Global Learning",
    "Soc Resp and Ethic Reason": "Social Responsibility and Ethical Reasoning",
}


def norm_code(code: str) -> str:
    code = re.sub(r"\s+", " ", code.strip().upper())
    m = re.match(r"^([A-Z]+)\s*0*(\d.*)$", code)
    return f"{m.group(1)} {m.group(2)}" if m else code


def load_valid_codes() -> set:
    """The exact set of normalized course codes across gen_ed_courses.json's
    10 domain lists -- the real 2,705-course scope. DEPARTMENTS above is only
    a *department* filter (derived from these same codes' prefixes), so a
    department can legitimately contain courses -- e.g. cross-listed or
    newly-added Gen Ed courses not yet reflected in gen_ed_courses.json --
    that carry real Learning Objective tags on the live bulletin page but
    are NOT one of the 2,705 codes. Filtering the scrape result against this
    set (in main(), after parsing) is what actually enforces the docstring's
    "scoped to exactly the 2,705 course codes" claim; department scope alone
    does not."""
    with open(GEN_ED_COURSES_PATH, encoding="utf-8") as f:
        gen_ed_courses = json.load(f)
    codes = set()
    for domain in gen_ed_courses.values():
        for c in domain.get("courses", []):
            codes.add(norm_code(c["code"] if isinstance(c, dict) else c))
    return codes


def fetch_dept_html(dept: str) -> str:
    url = psu_dept_url(dept)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_dept_objectives(dept: str, html: str, unmapped_seen: set) -> dict:
    """Returns {course_code: [canonical objective names]} for every course
    in `dept`'s bulletin page that carries at least one GenEd Learning
    Objective tag. Any raw abbreviated string not in LEARNING_OBJECTIVE_MAP
    is recorded in `unmapped_seen` (and the tag is skipped, not guessed)."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}

    for block in soup.select("div.courseblock"):
        title_tag = block.select_one(".courseblocktitle")
        if not title_tag:
            continue
        title_text = title_tag.get_text(" ", strip=True)
        m = re.match(rf"^({dept})\s+(\d{{1,3}}[A-Z]?)\s*:\s*(.+)$", title_text)
        if not m:
            continue
        dept_code, num, _rest = m.groups()
        code = norm_code(f"{dept_code} {num}")

        extra = block.select_one(".courseblockextra")
        if not extra:
            continue

        objectives = []
        for p in extra.find_all("p"):
            line = p.get_text(" ", strip=True)
            if not line.startswith(LEARNING_OBJECTIVE_PREFIX):
                continue
            raw = line[len(LEARNING_OBJECTIVE_PREFIX):].strip()
            canonical = LEARNING_OBJECTIVE_MAP.get(raw)
            if canonical is None:
                unmapped_seen.add(raw)
                continue
            if canonical not in objectives:
                objectives.append(canonical)

        if objectives:
            out[code] = objectives

    return out


def main():
    result = {}
    failed_departments = []
    unmapped_seen = set()

    for i, dept in enumerate(DEPARTMENTS):
        print(f"[{i + 1}/{len(DEPARTMENTS)}] Fetching {dept}...")
        try:
            html = fetch_dept_html(dept)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            print(f"  FAILED: {e}")
            failed_departments.append(dept)
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        dept_objectives = parse_dept_objectives(dept, html, unmapped_seen)
        print(f"  {len(dept_objectives)} courses with Learning Objective tags")
        result.update(dept_objectives)

        time.sleep(REQUEST_DELAY_SECONDS)

    if unmapped_seen:
        print("\nWARNING: raw Learning Objective strings with no mapping (skipped, not written):")
        for raw in sorted(unmapped_seen):
            print(f"  {raw!r}")

    if failed_departments:
        print(f"\n{len(failed_departments)} department(s) failed to fetch:")
        for dept in failed_departments:
            print(f"  {dept}")

    # Department scope alone is broader than the 2,705-code scope (a scoped
    # department can hold Gen Ed-tagged courses -- e.g. cross-listed ones --
    # that aren't themselves one of the 2,705 codes yet). Drop anything
    # outside that exact set so the output matches the docstring's promise.
    valid_codes = load_valid_codes()
    out_of_scope = sorted(code for code in result if code not in valid_codes)
    if out_of_scope:
        print(f"\n{len(out_of_scope)} course(s) had Learning Objective tags but are outside "
              f"gen_ed_courses.json's {len(valid_codes)}-code scope (dropped, not written):")
        for code in out_of_scope:
            print(f"  {code}")
        result = {code: objs for code, objs in result.items() if code in valid_codes}

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)

    print(f"\nWrote {len(result)} courses to {OUT_PATH}")


if __name__ == "__main__":
    main()
