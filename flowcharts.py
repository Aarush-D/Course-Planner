# flowcharts.py
import re
import os
import json
import time
from typing import Dict, List, Tuple, Optional
import requests
from bs4 import BeautifulSoup

FLOWCHARTS_PAGE = "https://advising.engr.psu.edu/degree-requirements/flow-charts.aspx"
CACHE_DIR = ".flowchart_cache"
COURSE_REGEX = re.compile(r"\b[A-Z]{2,5}\s*\d{2,3}[A-Z]?\b")
SEMESTER_HEADER = re.compile(r"^\s*(\d+)(st|nd|rd|th)\s+Semester\s*$", re.IGNORECASE)
DEFAULT_TIMEOUT = 60

def _ensure_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)

def fetch_flowchart_pdf_links(force_refresh: bool = False) -> Dict[str, str]:
    _ensure_cache()
    cache_path = os.path.join(CACHE_DIR, "flowchart_links.json")

    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    resp = requests.get(FLOWCHARTS_PAGE, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    links: Dict[str, str] = {}
    for a in soup.select('a[href]'):
        href = a.get("href", "")
        if "/assets/flow-charts/" not in href:
            continue
        if not href.lower().endswith(".pdf"):
            continue

        text = (a.get_text(" ", strip=True) or "").upper()
        m = re.search(r"$begin:math:text$\(\[A\-Z\]\{2\,5\}\)$end:math:text$", text)
        if m:
            major = m.group(1)
        else:
            filename = href.split("/")[-1]
            major = filename.split("-")[0].upper()

        if href.startswith("/"):
            href = "https://advising.engr.psu.edu" + href

        links[major] = href

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(links, f, indent=2)

    return links

def download_pdf(url: str, *, cache_seconds: int = 7 * 24 * 3600) -> bytes:
    _ensure_cache()
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", url.split("/")[-1])
    path = os.path.join(CACHE_DIR, safe_name)

    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age <= cache_seconds:
            with open(path, "rb") as f:
                return f.read()

    r = requests.get(url, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    data = r.content
    with open(path, "wb") as f:
        f.write(data)
    return data

def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = [(p.extract_text() or "") for p in reader.pages]
        return "\n".join(parts)
    except Exception:
        pass

    try:
        from pdfminer.high_level import extract_text
        import io
        return extract_text(io.BytesIO(pdf_bytes))
    except Exception as e:
        raise RuntimeError("Install `pypdf` or `pdfminer.six` for PDF text extraction.") from e

def parse_flowchart_semesters(pdf_text: str) -> Dict[int, List[str]]:
    lines = [ln.strip() for ln in pdf_text.splitlines() if ln.strip()]
    semesters: Dict[int, List[str]] = {}
    current: Optional[int] = None

    for ln in lines:
        m = SEMESTER_HEADER.match(ln)
        if m:
            current = int(m.group(1))
            semesters.setdefault(current, [])
            continue
        if current is not None:
            semesters[current].append(ln)

    return semesters

def extract_courses_from_semester_lines(lines: List[str]) -> Tuple[List[str], List[str]]:
    course_codes: List[str] = []
    other: List[str] = []
    for ln in lines:
        found = COURSE_REGEX.findall(ln.upper())
        if found:
            for c in found:
                c_norm = re.sub(r"\s+", " ", c.strip())
                if c_norm not in course_codes:
                    course_codes.append(c_norm)
        else:
            if any(k in ln.upper() for k in ["GEN ED", "GHW", "ELECTIVE", "FYS", "ENGR", "FIRST-YEAR"]):
                other.append(ln)
    return course_codes, other

def get_foundation_plan_for_major(
    major_code: str,
    *,
    semesters: Tuple[int, int] = (1, 2),
) -> Dict[str, List[str]]:
    links = fetch_flowchart_pdf_links()
    major_code = major_code.strip().upper()

    if major_code not in links:
        for k in links:
            if k.startswith(major_code):
                major_code = k
                break

    if major_code not in links:
        raise KeyError(f"No flowchart PDF found for major '{major_code}'.")

    pdf_url = links[major_code]
    pdf_bytes = download_pdf(pdf_url)
    text = _extract_pdf_text(pdf_bytes)
    sem_blocks = parse_flowchart_semesters(text)

    out: Dict[str, List[str]] = {}
    out["major"] = [major_code]
    out["pdf_url"] = [pdf_url]

    for sem in semesters:
        lines = sem_blocks.get(sem, [])
        courses, other = extract_courses_from_semester_lines(lines)
        out[f"semester_{sem}_courses"] = courses
        out[f"semester_{sem}_other"] = other

    return out

def format_foundation_plan(plan: Dict[str, List[str]]) -> str:
    major = (plan.get("major") or [""])[0]
    pdf_url = (plan.get("pdf_url") or [""])[0]

    def fmt_sem(n: int) -> str:
        courses = plan.get(f"semester_{n}_courses", [])
        other = plan.get(f"semester_{n}_other", [])
        lines = [f"{n}st Semester" if n == 1 else f"{n}nd Semester" if n == 2 else f"{n}th Semester"]
        if courses:
            lines.append("Courses:")
            for c in courses:
                lines.append(f"  - {c}")
        if other:
            lines.append("Other requirements shown on flowchart:")
            for o in other[:8]:
                lines.append(f"  - {o}")
        if not courses and not other:
            lines.append("  (Could not parse this semester block from the PDF text.)")
        return "\n".join(lines)

    return (
        f"Foundation plan from PSU Engineering flowchart for {major}\n"
        f"Source PDF: {pdf_url}\n\n"
        f"{fmt_sem(1)}\n\n"
        f"{fmt_sem(2)}\n"
    )