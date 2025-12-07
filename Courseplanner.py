import re
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
    prereq_groups: List[Set[str]]      # AND-of-ORs (hard prerequisites)
    concurrent_groups: List[Set[str]]  # AND-of-ORs (can be taken concurrently)


def parse_prereq_text(text: str) -> List[Set[str]]:
    """
    (Currently unused with the HTML-anchor parsing approach, but kept
    in case you later want to parse raw text-based prerequisites.)
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
            # skip non-CMPSC or weirdly formatted titles
            continue

        dept, num, name_with_credits = m.groups()
        code = f"{dept} {num}"

        # --- Credits: try to find a dedicated credits tag first ---
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

        # Fallback: if we still don't have credits, try to parse from title text
        if credits is None:
            m_cred = CREDIT_PATTERN.search(name_with_credits)
            if m_cred:
                try:
                    credits = float(m_cred.group(1))
                except ValueError:
                    credits = None

        # Clean the name: strip off anything starting with a digit followed by "Credits"
        # e.g. "Special Topics 1-9 Credits/Maximum of 9" -> "Special Topics"
        name = re.sub(r"\d.*Credits.*$", "", name_with_credits).strip()
        # Also clean weird trailing "1-" or "1." bits that came from ranges/decimals
        name = re.sub(r"\d[-.]?$", "", name).rstrip()

        # --- Prerequisites & Concurrent: look inside .courseblockextra section ---
        prereq_groups: List[Set[str]] = []
        concurrent_groups: List[Set[str]] = []

        prereq_section = block.select_one(".courseblockextra")
        if prereq_section:
            # Find all <strong> tags that label prerequisite-like info
            for strong in prereq_section.find_all("strong"):
                label = strong.get_text(strip=True).lower()

                # Decide whether this label is for prereqs or concurrent
                is_concurrent = "concurrent" in label
                is_prereq = "requisite" in label or "prerequisite" in label

                if not (is_concurrent or is_prereq):
                    continue

                target_list = concurrent_groups if is_concurrent else prereq_groups

                # 1) Courses in the SAME paragraph as the label
                same_p_group: Set[str] = set()
                parent_p = strong.parent
                if parent_p:
                    for a in parent_p.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            same_p_group.add(txt)
                if same_p_group:
                    target_list.append(same_p_group)

                # 2) Courses in any <ul> inside the prereq section
                #    (sometimes they list multiple options in a bullet list)
                for ul in prereq_section.find_all("ul"):
                    ul_group: Set[str] = set()
                    for a in ul.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            ul_group.add(txt)
                    if ul_group:
                        target_list.append(ul_group)

        # Store in catalog
        catalog[code] = Course(
            code=code,
            name=name,
            credits=credits,
            prereq_groups=prereq_groups,
            concurrent_groups=concurrent_groups,
        )

    return catalog


# ---------- Eligibility / planner logic ----------

def can_take_this_term(course: Course, completed: set[str], planned: set[str]) -> bool:
    """
    A course is available THIS term if:

      - All prereq groups are satisfied by COMPLETED courses:
          For every prereq AND-group, the student has at least one
          course from that group in `completed`.

      - All concurrent groups are satisfied by either COMPLETED or PLANNED:
          For every concurrent AND-group, the student has at least one
          course from that group in (completed ∪ planned).

    This lets us schedule a course concurrently with another course as
    long as that other course is itself schedulable this term.
    """

    # Hard prerequisites: must be fully in completed
    for group in course.prereq_groups:
        if not (group & completed):
            return False

    # Concurrent: can be either completed OR also planned this term
    completed_or_planned = completed | planned
    for group in course.concurrent_groups:
        if not (group & completed_or_planned):
            return False

    return True


def available_courses(catalog: Dict[str, Course], completed: set[str]) -> list[Course]:
    """
    Compute all courses the student can take THIS term, allowing
    concurrent enrollment.

    We iteratively grow a `planned` set of courses until no new ones
    can be added.
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

    Example: [{'MATH 110', 'MATH 140'}, {'CMPSC 131'}]
    -> "(MATH 110 or MATH 140) AND (CMPSC 131)"
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


# ---------- Main: interactive course planner ----------

if __name__ == "__main__":
    url = "https://bulletins.psu.edu/university-course-descriptions/undergraduate/cmpsc/"
    print("Fetching CMPSC catalog from PSU bulletin...")
    catalog = scrape_psu_cmpsc_catalog(url)
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

    print("\n=== Courses You Are Eligible to Take Next (This Term) ===")
    if not avail:
        print("No additional CMPSC courses available with current prerequisites.")
    else:
        for course in avail:
            cred_str = format_credits(course.credits)
            if cred_str:
                print(f"- {course.code} ({cred_str}) — {course.name}")
            else:
                print(f"- {course.code} — {course.name}")
            print(f"    Prereqs:    {format_groups(course.prereq_groups)}")
            print(f"    Concurrent: {format_groups(course.concurrent_groups)}")

    print("\nTotal available:", len(avail))
