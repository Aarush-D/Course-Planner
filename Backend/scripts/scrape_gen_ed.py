"""One-off scraper for PSU's General Education course lists.

Fetches each Gen Ed category's approved-course table from
bulletins.psu.edu and writes Backend/data/gen_ed_courses.json.
Run manually (not part of the app's request path) whenever the
bulletin's Gen Ed lists need refreshing.
"""
import json
import re
import urllib.request

from bs4 import BeautifulSoup

CATEGORIES = {
    "GQ": ("Quantification", 6, "quantification"),
    "GWS": ("Writing/Speaking", 9, "writing-speaking"),
    "GA": ("Arts", 3, "arts"),
    "GHW": ("Health and Wellness", 3, "health-wellness"),
    "GH": ("Humanities", 3, "humanities"),
    "GN": ("Natural Sciences", 3, "natural-sciences"),
    "GS": ("Social and Behavioral Sciences", 3, "social-behavioral-sciences"),
    "INTER-D": ("Inter-Domain (Integrative Studies)", 6, "inter-domain"),
    "IL": ("International Cultures", 3, "international-cultures"),
    "US": ("United States Cultures", 3, "united-states-cultures"),
}

BASE = "https://bulletins.psu.edu/undergraduate/general-education/course-lists/{}/"


def norm_code(code: str) -> str:
    code = re.sub(r"\s+", " ", code.strip().upper())
    m = re.match(r"^([A-Z]+)\s*0*(\d.*)$", code)
    return f"{m.group(1)} {m.group(2)}" if m else code


def fetch_category(slug: str):
    url = BASE.format(slug)
    with urllib.request.urlopen(url, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    courses = []
    for row in soup.select("table.sc_courselist tr"):
        code_cell = row.select_one("td.codecol")
        if not code_cell:
            continue
        code_link = code_cell.select_one("a")
        raw_code = (code_link.get("title") or code_link.get_text()).strip() if code_link else code_cell.get_text().strip()
        tds = row.find_all("td")
        if len(tds) < 3:
            continue
        title = tds[1].get_text().strip()
        title = re.sub(r"\s+", " ", title)
        credits_txt = tds[2].get_text().strip()
        courses.append({
            "code": norm_code(raw_code),
            "title": title,
            "credits": credits_txt,
        })
    return courses


def main():
    out = {}
    for domain, (name, credits_required, slug) in CATEGORIES.items():
        print(f"Fetching {domain} ({name})...")
        courses = fetch_category(slug)
        print(f"  {len(courses)} courses")
        out[domain] = {
            "name": name,
            "credits_required": credits_required,
            "source": BASE.format(slug),
            "courses": courses,
        }
    with open("data/gen_ed_courses.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("Wrote data/gen_ed_courses.json")


if __name__ == "__main__":
    main()
