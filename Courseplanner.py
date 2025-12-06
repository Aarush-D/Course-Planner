import re
import requests
from dataclasses import dataclass
from typing import List, Set, Dict
from bs4 import BeautifulSoup

# Matches course codes like MATH 140, CMPSC 131, STAT 414, EE 465, DS 220, etc.
COURSE_REGEX = re.compile(r"[A-Z]{2,5}\s*\d{2,3}[A-Z]?")

@dataclass
class Course:
    code: str           # e.g. "CMPSC 100"
    name: str           # e.g. "Computer Fundamentals and Applications"
    credits: int | None
    prereq_groups: List[Set[str]]  # AND-of-ORs


def parse_prereq_text(text: str) -> List[Set[str]]:
    """
    Parse prerequisite-like text into AND-of-OR groups.

    Examples:
      'Enforced Prerequisite: MATH 21 or satisfactory performance ...'
      'Enforced Concurrent at Enrollment: MATH 110 or MATH 140'
    -> [{'MATH 21'}]
    -> [{'MATH 110', 'MATH 140'}]
    """

    # Normalize weird spaces
    text = text.replace("\xa0", " ")

    # Strip labels like "Enforced Prerequisite", "Prerequisite", etc.
    cleaned = re.sub(r"enforced\s+prerequisite[s]?:", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bprerequisite[s]?:", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"enforced\s+concurrent\s+at\s+enrollment[:]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"concurrent\s+at\s+enrollment[:]*", "", cleaned, flags=re.IGNORECASE)

    # Split on "and" (for AND-groups)
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
            # skip non-CMPSC or weirdly formatted titles
            continue

        dept, num, name = m.groups()
        code = f"{dept} {num}"

        # Credits
        credits: int | None = None
        credit_tag = block.select_one(".courseblockextra .hours, .coursecredits, .hours")
        if credit_tag:
            credit_text = credit_tag.get_text(" ", strip=True)
            cm = re.search(r"(\d+)", credit_text)
            if cm:
                try:
                    credits = int(cm.group(1))
                except ValueError:
                    credits = None

        # --- Prerequisites: look inside .courseblockextra section ---
        prereq_groups: List[Set[str]] = []

        prereq_section = block.select_one(".courseblockextra")
        if prereq_section:
            # Find all <strong> tags that look like prerequisite labels
            for strong in prereq_section.find_all("strong"):
                label = strong.get_text(strip=True).lower()
                if "requisite" not in label:
                    # only care about "Prerequisite", "Enforced Prerequisite",
                    # "Concurrent at Enrollment", etc.
                    continue

                # 1) Look for courses in the SAME paragraph as the label
                same_p_group: Set[str] = set()
                parent_p = strong.parent
                if parent_p:
                    for a in parent_p.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            same_p_group.add(txt)
                if same_p_group:
                    prereq_groups.append(same_p_group)

                # 2) Look for courses in any <ul> inside the prereq section
                for ul in prereq_section.find_all("ul"):
                    ul_group: Set[str] = set()
                    for a in ul.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            ul_group.add(txt)
                    if ul_group:
                        prereq_groups.append(ul_group)

        # Store in catalog
        catalog[code] = Course(
            code=code,
            name=name,
            credits=credits,
            prereq_groups=prereq_groups,
        )

    return catalog


# Optional helper for later: which courses are available given completed set
def can_take(course: Course, completed: set[str]) -> bool:
    """
    A course is available if:
      - It has no prereq groups, OR
      - For every AND-group, the student has at least one course in that group.
    """
    if not course.prereq_groups:
        return True
    for group in course.prereq_groups:
        if not (group & completed):
            return False
    return True


def available_courses(catalog: Dict[str, Course], completed: set[str]) -> list[Course]:
    out: list[Course] = []
    for c in catalog.values():
        if c.code in completed:
            continue
        if can_take(c, completed):
            out.append(c)
    return out


if __name__ == "__main__":
    url = "https://bulletins.psu.edu/university-course-descriptions/undergraduate/cmpsc/"
    catalog = scrape_psu_cmpsc_catalog(url)

    # Just dump the parsed courses + prereqs for inspection
    for code in sorted(catalog):
        c = catalog[code]
        print(f"{c.code} ({c.credits} cr) - {c.name}")
        print(f"  prereq_groups = {c.prereq_groups}")
    print("Total CMPSC courses parsed:", len(catalog))

    # Example of using the planner logic:
    # completed = {"MATH 21", "MATH 140", "CMPSC 131"}
    # avail = available_courses(catalog, completed)
    # print("\nWith completed =", completed, "you can take:")
    # for c in sorted(avail, key=lambda x: x.code):
    #     print(" -", c.code, "-", c.name)
