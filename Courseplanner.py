import re
import json
import os
import requests
from dataclasses import dataclass
from typing import List, Set, Dict
from bs4 import BeautifulSoup

# Matches course codes like MATH 140, CMPSC 131, STAT 414, EE 465, DS 220, etc.
COURSE_REGEX = re.compile(r"[A-Z]{2,5}\s*\d{2,3}[A-Z]?")

# Matches credit expressions like:
#  "3 Credits"
#  "1.5 Credits"
#  "1-9 Credits"
#  "1.5-3.0 Credits"
CREDIT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)(?:-\d+(?:\.\d+)?)?\s*Credits",
    re.IGNORECASE
)


@dataclass
class Course:
    code: str                 # e.g. "CMPSC 100"
    name: str                 # e.g. "Computer Fundamentals and Applications"
    credits: float | None
    prereq_groups: List[Set[str]]      # AND-of-ORs (Enforced Prerequisite)
    concurrent_groups: List[Set[str]]  # AND-of-ORs (Enforced Concurrent at Enrollment)


def parse_prereq_text(text: str) -> List[Set[str]]:
    """
    Kept for possible future text-based parsing.
    Currently we rely on parsing <a> tags near the <strong> labels.
    """
    text = text.replace("\xa0", " ")

    cleaned = re.sub(r"enforced\s+prerequisite[s]?:", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bprerequisite[s]?:", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"enforced\s+concurrent\s+at\s+enrollment[:]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"concurrent\s+at\s+enrollment[:]*", "", cleaned, flags=re.IGNORECASE)

    raw_groups = re.split(r"\band\b", cleaned, flags=re.IGNORECASE)

    groups: List[Set[str]] = []
    for g in raw_groups:
        courses = {
            m.group(0).upper().replace("  ", " ")
            for m in COURSE_REGEX.finditer(g)
        }
        if courses:
            groups.append(courses)

    return groups


def scrape_psu_cmpsc_catalog(url: str) -> Dict[str, Course]:
    resp = requests.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    catalog: Dict[str, Course] = {}

    # Each courseblock contains one full course entry
    blocks = soup.select("div.courseblock")

    for block in blocks:
        # Title line: "CMPSC 313: Assembly Language Programming"
        title_tag = block.select_one(".courseblocktitle")
        if not title_tag:
            continue

        title_text = title_tag.get_text(" ", strip=True)
        m = re.match(r"^(CMPSC)\s+(\d{2,3}[A-Z]?)\s*:\s*(.+)$", title_text)
        if not m:
            continue

        dept, num, name_with_credits = m.groups()
        code = f"{dept} {num}"

        # --- Credits: try credits-tag then fallback to title text ---
        credits: float | None = None
        credit_tag = block.select_one(".courseblockextra .hours, .coursecredits, .hours")
        if credit_tag:
            credit_text = credit_tag.get_text(" ", strip=True)
            cm = CREDIT_PATTERN.search(credit_text)
            if cm:
                try:
                    credits = float(cm.group(1))
                except ValueError:
                    credits = None

        if credits is None:
            m_cred = CREDIT_PATTERN.search(name_with_credits)
            if m_cred:
                try:
                    credits = float(m_cred.group(1))
                except ValueError:
                    credits = None

        # Clean the name: strip anything that looks like "1-9 Credits/..."
        name = re.sub(r"\d.*Credits.*$", "", name_with_credits).strip()
        name = re.sub(r"\d[-.]?$", "", name).rstrip()

        # --- Prereqs & Concurrent: ONLY
        #       "Enforced Prerequisite"
        #       "Enforced Concurrent at Enrollment"
        prereq_groups: List[Set[str]] = []
        concurrent_groups: List[Set[str]] = []

        prereq_section = block.select_one(".courseblockextra")
        if prereq_section:
            for strong in prereq_section.find_all("strong"):
                label = strong.get_text(" ", strip=True).lower()

                is_enforced_prereq = "enforced prerequisite" in label
                is_enforced_concurrent = "enforced concurrent at enrollment" in label

                if not (is_enforced_prereq or is_enforced_concurrent):
                    continue

                target_list = prereq_groups if is_enforced_prereq else concurrent_groups

                # 1) Courses in SAME paragraph as the label
                parent_p = strong.parent
                if parent_p:
                    group: Set[str] = set()
                    for a in parent_p.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            group.add(txt)
                    if group:
                        target_list.append(group)

                # 2) Courses in the next <ul> (if they use bullet lists)
                ul = strong.find_next("ul")
                if ul and prereq_section in ul.parents:
                    group2: Set[str] = set()
                    for a in ul.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            group2.add(txt)
                    if group2:
                        target_list.append(group2)

        catalog[code] = Course(
            code=code,
            name=name,
            credits=credits,
            prereq_groups=prereq_groups,
            concurrent_groups=concurrent_groups,
        )

    return catalog


def course_level(code: str) -> int | None:
    """
    Given 'CMPSC 132' -> 100
           'CMPSC 221' -> 200
    Returns 100, 200, 300, 400, etc. or None if it can't parse.
    """
    m = re.search(r"\b(\d{3})[A-Z]?\b", code)
    if not m:
        return None
    num = int(m.group(1))
    return (num // 100) * 100


def group_by_level(courses: list[Course]) -> dict[int, list[Course]]:
    """
    Group a list of Course objects into {100: [...], 200: [...], ...}
    """
    levels: dict[int, list[Course]] = {}
    for c in courses:
        lvl = course_level(c.code)
        if lvl is None:
            # Put weird ones in level 0 "Other"
            lvl = 0
        levels.setdefault(lvl, []).append(c)

    # Sort courses inside each level
    for lvl in levels:
        levels[lvl].sort(key=lambda x: x.code)

    return levels


# ---------- Eligibility / planner logic ----------

def can_take_this_term(course: Course, completed: set[str], planned: set[str]) -> bool:
    """
    A course is available THIS term if:

      - All Enforced Prerequisite groups are satisfied by COMPLETED courses.
      - All Enforced Concurrent at Enrollment groups are satisfied by
        COMPLETED ∪ PLANNED (so you can take them together this term).
    """

    # Enforced Prerequisite: must be satisfied by completed courses
    for group in course.prereq_groups:
        if not (group & completed):
            return False

    # Enforced Concurrent at Enrollment: can be completed OR planned
    completed_or_planned = completed | planned
    for group in course.concurrent_groups:
        if not (group & completed_or_planned):
            return False

    return True


def available_courses(catalog: Dict[str, Course], completed: set[str]) -> list[Course]:
    """
    Compute all courses the student can take THIS term, allowing
    concurrent enrollment.
    """
    planned: set[str] = set()

    while True:
        added_any = False

        for course in catalog.values():
            if course.code in completed or course.code in planned:
                continue

            if can_take_this_term(course, completed, planned):
                planned.add(course.code)
                added_any = True

        if not added_any:
            break

    return [catalog[code] for code in sorted(planned)]


def format_groups(groups: List[Set[str]]) -> str:
    """
    Turn an AND-of-ORs list into a human-friendly string.
    """
    if not groups:
        return "None"
    parts: list[str] = []
    for group in groups:
        if len(group) == 1:
            parts.append(next(iter(group)))
        else:
            parts.append("(" + " or ".join(sorted(group)) + ")")
    return " AND ".join(parts)


def format_credits(credits: float | None) -> str:
    """
    Format credits:
      3.0   -> '3 cr'
      1.5   -> '1.5 cr'
      None  -> ''
    """
    if credits is None:
        return ""
    if float(credits).is_integer():
        return f"{int(credits)} cr"
    return f"{credits} cr"


# ---------- JSON save/load helpers ----------

def catalog_to_json_dict(catalog: Dict[str, Course]) -> dict:
    out: dict = {}
    for code, course in catalog.items():
        out[code] = {
            "code": course.code,
            "name": course.name,
            "credits": course.credits,
            "prereq_groups": [sorted(list(group)) for group in course.prereq_groups],
            "concurrent_groups": [sorted(list(group)) for group in course.concurrent_groups],
        }
    return out


def catalog_from_json_dict(data: dict) -> Dict[str, Course]:
    catalog: Dict[str, Course] = {}
    for code, obj in data.items():
        prereq_groups = [set(group) for group in obj.get("prereq_groups", [])]
        concurrent_groups = [set(group) for group in obj.get("concurrent_groups", [])]
        course = Course(
            code=obj["code"],
            name=obj["name"],
            credits=obj.get("credits"),
            prereq_groups=prereq_groups,
            concurrent_groups=concurrent_groups,
        )
        catalog[code] = course
    return catalog


def save_catalog_to_json(path: str, catalog: Dict[str, Course]) -> None:
    data = catalog_to_json_dict(catalog)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_catalog_from_json(path: str) -> Dict[str, Course]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return catalog_from_json_dict(data)


def get_cmpsc_catalog() -> Dict[str, Course]:
    """
    Load CMPSC catalog from cache if present, otherwise scrape and cache it.
    Safe to use from both CLI and web UI.
    """
    url = "https://bulletins.psu.edu/university-course-descriptions/undergraduate/cmpsc/"
    cache_path = "cmpsc_catalog.json"

    if os.path.exists(cache_path):
        return load_catalog_from_json(cache_path)

    catalog = scrape_psu_cmpsc_catalog(url)
    save_catalog_to_json(cache_path, catalog)
    return catalog


# ---------- Main: interactive course planner (CLI) ----------

if __name__ == "__main__":
    catalog = get_cmpsc_catalog()
    print(f"Loaded {len(catalog)} CMPSC courses.\n")

    print("=== PSU CMPSC Course Planner ===")
    print("Enter the courses you have completed, separated by commas.")
    print("Examples:")
    print("  MATH 21")
    print("  MATH 140, CMPSC 131")
    print("  MATH 140, CMPSC 131, CMPSC 132\n")

    raw = input("Completed courses: ").strip()

    completed = {
        c.strip().upper().replace("  ", " ")
        for c in raw.split(",")
        if c.strip()
    }

    print("\nYou marked these as completed:")
    if completed:
        for c in sorted(completed):
            print(" -", c)
    else:
        print(" (none)")

    avail = available_courses(catalog, completed)

    # Split into non-concurrent and concurrent
    no_concurrent = [c for c in avail if not c.concurrent_groups]
    with_concurrent = [c for c in avail if c.concurrent_groups]

    print("\n=== Courses You Are Eligible to Take Next (NO Concurrent Requirement) ===")
    if not no_concurrent:
        print("None")
    else:
        grouped = group_by_level(no_concurrent)
        for lvl in sorted(grouped.keys()):
            label = "Other-level" if lvl == 0 else f"{lvl}-level"
            print(f"\n  -- {label} --")
            for course in grouped[lvl]:
                cred_str = format_credits(course.credits)
                if cred_str:
                    print(f"- {course.code} ({cred_str}) — {course.name}")
                else:
                    print(f"- {course.code} — {course.name}")
                if course.prereq_groups:
                    print(f"    Prereqs:    {format_groups(course.prereq_groups)}")
                print()

    print("\n=== Courses You Are Eligible to Take Next (WITH Enforced Concurrent at Enrollment) ===")
    if not with_concurrent:
        print("None")
    else:
        grouped = group_by_level(with_concurrent)
        for lvl in sorted(grouped.keys()):
            label = "Other-level" if lvl == 0 else f"{lvl}-level"
            print(f"\n  -- {label} --")
            for course in grouped[lvl]:
                cred_str = format_credits(course.credits)
                if cred_str:
                    print(f"- {course.code} ({cred_str}) — {course.name}")
                else:
                    print(f"- {course.code} — {course.name}")
                if course.prereq_groups:
                    print(f"    Prereqs:    {format_groups(course.prereq_groups)}")
                if course.concurrent_groups:
                    print(f"    Concurrent: {format_groups(course.concurrent_groups)}")
                print()

    print(
        f"\nTotal available: {len(avail)} "
        f"(no concurrent: {len(no_concurrent)}, with concurrent: {len(with_concurrent)})"
    )
