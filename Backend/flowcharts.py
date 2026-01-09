from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

import requests
from PyPDF2 import PdfReader
from io import BytesIO

DEFAULT_TIMEOUT = 60

# Where you will store PDFs locally:
# Backend/assets/flow-charts/CMPSC-2026.pdf etc
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FLOWCHART_DIR = os.path.join(BASE_DIR, "assets", "flow-charts")

# Map major -> local filename (recommended)
FLOWCHART_FILES: Dict[str, str] = {
    "CMPSC": "CMPSC-2026.pdf",
    # Add more later:
    # "CMPEN": "CMPEN-2026.pdf",
    # "MATH": "MATH-2026.pdf",
}


def _read_local_pdf(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def download_pdf(url_or_path: str) -> bytes:
    """
    Accepts either:
      - absolute URL (http/https) -> download via requests
      - local filename/path -> read from Backend/assets/flow-charts
    """
    s = (url_or_path or "").strip()

    # URL case
    if s.startswith("http://") or s.startswith("https://"):
        r = requests.get(s, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.content

    # Local file case
    # If user passed just "CMPSC-2026.pdf", read from FLOWCHART_DIR
    if not os.path.isabs(s):
        candidate = os.path.join(FLOWCHART_DIR, s)
    else:
        candidate = s

    if not os.path.exists(candidate):
        raise FileNotFoundError(
            f"Flowchart PDF not found at: {candidate}\n"
            f"Create: {FLOWCHART_DIR} and put the PDF there."
        )

    return _read_local_pdf(candidate)


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    chunks = []
    for page in reader.pages:
        t = page.extract_text() or ""
        chunks.append(t)
    return "\n".join(chunks)


def parse_flowchart_semesters(text: str) -> Dict[str, List[str]]:
    """
    VERY simple parser: finds course-like codes.
    You can improve this later (real semester blocks).
    """
    course_re = re.compile(r"\b[A-Z]{2,5}\s*\d{2,3}[A-Z]?\b")
    courses = sorted(set(course_re.findall(text)))
    return {"ALL": courses}


def get_foundation_plan_for_major(major_code: str, semesters: Tuple[int, int] = (1, 2)):
    major_code = (major_code or "").upper()
    if major_code not in FLOWCHART_FILES:
        raise KeyError(f"No flowchart PDF mapped for major '{major_code}'.")

    filename = FLOWCHART_FILES[major_code]
    pdf_bytes = download_pdf(filename)
    text = _extract_pdf_text(pdf_bytes)

    parsed = parse_flowchart_semesters(text)

    # For now return “ALL courses found” as foundation list
    return parsed


def format_foundation_plan(plan: Dict[str, List[str]]) -> str:
    items = plan.get("ALL", [])
    if not items:
        return "I couldn’t extract any course codes from the flowchart PDF."

    # keep it short
    preview = items[:20]
    more = "" if len(items) <= 20 else f"\n(+{len(items) - 20} more)"
    return "Foundation courses pulled from flowchart PDF:\n- " + "\n- ".join(preview) + more