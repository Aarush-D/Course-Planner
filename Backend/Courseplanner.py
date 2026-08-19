# Courseplanner.py
import re
import json
import os
import math
import requests
from dataclasses import dataclass, field
from typing import List, Set, Dict, Optional, Tuple
from bs4 import BeautifulSoup

# \d{1,3}: PSU has legacy single-digit codes (e.g. SOC 1, PLSC 3) alongside
# the usual 2-3 digit ones.
COURSE_REGEX = re.compile(r"[A-Z]{2,5}\s*\d{1,3}[A-Z]?")

CREDIT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)(?:-\d+(?:\.\d+)?)?\s*Credits",
    re.IGNORECASE
)

@dataclass
class Course:
    code: str
    name: str
    credits: float | None
    prereq_groups: List[Set[str]]
    concurrent_groups: List[Set[str]]
    description: str | None = None
    # Codes this course may NOT be taken/counted alongside ("may not schedule
    # for credit toward the degree if X has already been completed"). A flat
    # OR set, not grouped like prereq_groups — PSU's real "may not also take"
    # language is never an AND-of-groups pattern. Empty for every catalog
    # scraped before this field existed; inert until real data is added.
    excludes: Set[str] = field(default_factory=set)

# -------------------------
# Flowchart priority
# -------------------------
# Every course that appears on the official CMPSC 8-semester flowchart.
# These are surfaced first in eligible/recommendation lists so the LLM
# and the frontend always see the on-plan courses at the top.
FLOWCHART_COURSES: Set[str] = {
    # Semester 1
    "CMPSC 131", "MATH 140", "ENGL 015", "ENGL 030", "ESL 015",
    "CMPSC 150N",
    # Semester 2
    "CMPSC 132", "MATH 141", "PHYS 211", "GEN ED", "CMPSC 111",
    "ENGR 100",
    # Semester 3
    "CMPSC 221", "MATH 230", "MATH 220", "PHYS 212", "CAS 100A",
    "CAS 100B",
    # Semester 4
    "CMPSC 222", "CMPSC 360", "CMPEN 270",
    # Semester 5
    "CMPEN 315", "CMPSC 320", "CMPSC 465", "STAT 318",
    # Semester 6
    "CMPSC 316", "CMPSC 461", "STAT 319", "ENGL 202C",
    # Semester 7
    "CMPSC 483W",
    # Semester 8  (elective slots kept as labels, not enforced)
}

def is_flowchart_course(code: str) -> bool:
    """Return True if the normalized code appears in the official flowchart."""
    return _normalize_code(code) in {_normalize_code(c) for c in FLOWCHART_COURSES}

def sort_by_flowchart_priority(courses: list) -> list:
    """
    Sort a list of Course objects (or code strings) so that official
    flowchart courses come first, preserving original order within each group.
    Works with both Course dataclass instances and plain strings.
    """
    def _key(item):
        code = item.code if hasattr(item, "code") else str(item)
        return (0 if is_flowchart_course(code) else 1, code)
    return sorted(courses, key=_key)

# -------------------------
# Ollama helpers
# -------------------------
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

def ollama_embed(text: str, model: str = "nomic-embed-text") -> list[float]:
    r = requests.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["embedding"]

def ollama_chat_messages(messages: list[dict], model: str = "llama3") -> str:
    r = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={"model": model, "messages": messages, "stream": False},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]

def ollama_chat(prompt: str, model: str = "llama3") -> str:
    messages = [
        {"role": "system", "content": "You are a helpful PSU course planning assistant."},
        {"role": "user", "content": prompt},
    ]
    return ollama_chat_messages(messages, model=model)

# -------------------------
# Scraping helpers
# -------------------------
def psu_dept_url(dept_code: str) -> str:
    return f"https://bulletins.psu.edu/university-course-descriptions/undergraduate/{dept_code.lower()}/"

def _normalize_code(s: str) -> str:
    s = s.strip().upper().replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s

# Mid-paragraph labels that follow an initial <strong> clause without their
# own <strong> wrapper — must stop that clause's scope, not get swept into
# it. "Recommended ..." (e.g. "Recommended Corequisite:") is advisory, not
# enforced, so dropping it is correct. "Enforced Concurrent:" (bare, vs. the
# "at Enrollment" form already caught by the primary label check) IS a real
# requirement, but capturing it correctly would need a second pass; safer to
# drop it here too — under-enforcing a concurrent pairing just means it's
# not double-checked, whereas leaving it in scope wrongly makes it a strict
# prior-term prerequisite and can make a same-term pairing "unschedulable".
_BOUNDARY_RE = re.compile(
    r"(?i)\b(concurrent\s+courses|prerequisite\s+or\s+concurrent|recommended\s+\w+)\s*:"
    r"|\benforced\s+concurrent\b\s*:?"  # colon is optional here — seen as
                                         # both "Enforced Concurrent:" and
                                         # "...and enforced concurrent X"
)
_CONNECTOR_TOKEN_RE = re.compile(r"\band\b|\bor\b|[(),]", re.IGNORECASE)


def _label_scope_nodes(strong_tag) -> list:
    """Sibling nodes governed by one <strong> label: everything after it up to
    (not including) the next <strong>, or a plain-text boundary phrase like
    'Concurrent Courses:' that PSU sometimes tacks onto the same paragraph
    without its own <strong> wrapper."""
    scope = []
    for sib in strong_tag.next_siblings:
        if getattr(sib, "name", None) == "strong":
            break
        if isinstance(sib, str):
            m = _BOUNDARY_RE.search(sib)
            if m:
                before = sib[: m.start()]
                if before.strip():
                    scope.append(before)
                break
        scope.append(sib)
    return scope


def _and_or_groups_from_scope(scope: list) -> List[Set[str]]:
    """Split a label's scope into AND-required groups (OR-alternatives
    within each). Handles the common real PSU pattern 'A and (B or C) and
    (D or E)' — e.g. CMPSC 489's actual prerequisite, 'MATH 141 and
    (MATH 220 or MATH 430 or MATH 436) and (STAT 318 or STAT 319 or
    STAT 414 or STAT 415 or STAT 418 or EE 465)', is three AND-required
    groups, the last two each with several OR-alternatives — NOT one big
    OR-group over all ten courses. A parenthesized clause is an OR-group;
    a top-level 'and' (including the implicit one at a ')') starts a new
    AND-group. A genuinely ambiguous nested AND *inside* a parenthesized
    clause (e.g. 'X or (Y and Z)') still falls back to one merged
    OR-group — permissive, never wrongly blocks a valid path — since that
    structure can't be split safely without knowing PSU's real intent."""
    # One ordered stream of ("course", code) / ("tok", token) — DOM sibling
    # order already interleaves <a> course links and connector text
    # correctly, so a single pass over `scope` preserves real document order.
    stream: List[Tuple[str, str]] = []
    for node in scope:
        if getattr(node, "name", None) == "a":
            txt = node.get_text(strip=True).replace("\xa0", " ").upper()
            if COURSE_REGEX.fullmatch(txt):
                stream.append(("course", txt))
            continue
        if not isinstance(node, str) or not node.strip():
            continue
        for tok in _CONNECTOR_TOKEN_RE.findall(node):
            stream.append(("tok", tok.lower()))

    # Bail to one merged OR-group only for the genuinely ambiguous case:
    # an "and" appearing INSIDE a parenthesized clause.
    depth = 0
    for kind, val in stream:
        if kind != "tok":
            continue
        if val == "(":
            depth += 1
        elif val == ")":
            depth = max(0, depth - 1)
        elif val == "and" and depth > 0:
            all_courses = {v for k, v in stream if k == "course"}
            return [all_courses] if all_courses else []

    groups: List[Set[str]] = []
    current: Set[str] = set()
    depth = 0
    for kind, val in stream:
        if kind == "course":
            current.add(val)
            continue
        if val == "(":
            depth += 1
        elif val == ")":
            depth = max(0, depth - 1)
            if depth == 0 and current:
                groups.append(current)
                current = set()
        elif val == "and" and depth == 0:
            if current:
                groups.append(current)
                current = set()
        # "or" and "," never split a group — they mark alternatives within it.
    if current:
        groups.append(current)
    return groups


def scrape_psu_dept_catalog(dept: str) -> Dict[str, Course]:
    dept = dept.upper()
    url = psu_dept_url(dept)

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    catalog: Dict[str, Course] = {}
    blocks = soup.select("div.courseblock")

    for block in blocks:
        title_tag = block.select_one(".courseblocktitle")
        if not title_tag:
            continue

        title_text = title_tag.get_text(" ", strip=True)
        m = re.match(rf"^({dept})\s+(\d{{1,3}}[A-Z]?)\s*:\s*(.+)$", title_text)
        if not m:
            continue

        dept_code, num, name_with_credits = m.groups()
        code = f"{dept_code} {num}"

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

        name = re.sub(r"\d.*Credits.*$", "", name_with_credits).strip()
        name = re.sub(r"\d[-.]?$", "", name).rstrip()

        desc = None
        desc_block = block.select_one(".courseblockdesc")
        if desc_block:
            desc = desc_block.get_text(" ", strip=True)

        prereq_groups: List[Set[str]] = []
        concurrent_groups: List[Set[str]] = []

        prereq_section = block.select_one(".courseblockextra")
        if prereq_section:
            for strong in prereq_section.find_all("strong"):
                label = strong.get_text(" ", strip=True).lower()
                # "Enforced Prerequisite OR Concurrent at Enrollment" (common for
                # lab/lecture pairs) must count as concurrent, not prerequisite —
                # "enforced prerequisite" is a substring of that phrase, so check
                # for "or concurrent" first.
                is_or_concurrent = "or concurrent" in label
                is_co = is_or_concurrent or "enforced concurrent at enrollment" in label
                is_pr = "enforced prerequisite" in label and not is_or_concurrent
                if not (is_pr or is_co):
                    continue
                target = prereq_groups if is_pr else concurrent_groups

                for g in _and_or_groups_from_scope(_label_scope_nodes(strong)):
                    target.append(g)

                ul = strong.find_next("ul")
                if ul and prereq_section in ul.parents:
                    g2: Set[str] = set()
                    for a in ul.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            g2.add(txt)
                    if g2:
                        target.append(g2)

        norm = _normalize_code(code)
        catalog[norm] = Course(
            code=norm,
            name=name,
            credits=credits,
            prereq_groups=prereq_groups,
            concurrent_groups=concurrent_groups,
            description=desc,
        )

    return catalog

# -------------------------
# JSON cache for catalog
# -------------------------
def catalog_to_json_dict(catalog: Dict[str, Course]) -> dict:
    out = {}
    for code, c in catalog.items():
        out[code] = {
            "code": c.code,
            "name": c.name,
            "credits": c.credits,
            "prereq_groups": [sorted(list(g)) for g in c.prereq_groups],
            "concurrent_groups": [sorted(list(g)) for g in c.concurrent_groups],
            "description": c.description,
            "excludes": sorted(c.excludes),
        }
    return out

def catalog_from_json_dict(data: dict) -> Dict[str, Course]:
    catalog: Dict[str, Course] = {}
    for code, obj in data.items():
        catalog[code] = Course(
            code=obj["code"],
            name=obj["name"],
            credits=obj.get("credits"),
            prereq_groups=[set(g) for g in obj.get("prereq_groups", [])],
            concurrent_groups=[set(g) for g in obj.get("concurrent_groups", [])],
            description=obj.get("description"),
            excludes=set(obj.get("excludes", [])),
        )
    return catalog

def save_catalog_to_json(path: str, catalog: Dict[str, Course]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog_to_json_dict(catalog), f, indent=2, ensure_ascii=False)

def load_catalog_from_json(path: str) -> Dict[str, Course]:
    with open(path, "r", encoding="utf-8") as f:
        return catalog_from_json_dict(json.load(f))

def get_dept_catalog(dept: str) -> Dict[str, Course]:
    dept = dept.upper()
    cache_path = f"{dept.lower()}_catalog.json"
    if os.path.exists(cache_path):
        return load_catalog_from_json(cache_path)
    catalog = scrape_psu_dept_catalog(dept)
    save_catalog_to_json(cache_path, catalog)
    return catalog

# -------------------------
# Utilities
# -------------------------
def course_level(code: str) -> int | None:
    m = re.search(r"\b(\d{3})[A-Z]?\b", code)
    if not m:
        return None
    num = int(m.group(1))
    return (num // 100) * 100

def group_by_level(courses: list[Course]) -> dict[int, list[Course]]:
    levels: dict[int, list[Course]] = {}
    for c in courses:
        lvl = course_level(c.code) or 0
        levels.setdefault(lvl, []).append(c)
    for lvl in levels:
        levels[lvl].sort(key=lambda x: x.code)
    return levels

# -------------------------
# Eligibility
# -------------------------
def can_take_this_term(course: Course, completed: set[str], planned: set[str]) -> bool:
    for group in course.prereq_groups:
        if not (group & completed):
            return False

    completed_or_planned = completed | planned
    for group in course.concurrent_groups:
        if not (group & completed_or_planned):
            return False

    return True

def available_courses(catalog: Dict[str, Course], completed: set[str]) -> list[Course]:
    completed = {_normalize_code(c) for c in completed}
    planned: set[str] = set()

    while True:
        added = False
        for c in catalog.values():
            if c.code in completed or c.code in planned:
                continue
            if can_take_this_term(c, completed, planned):
                planned.add(c.code)
                added = True
        if not added:
            break

    raw = [catalog[code] for code in sorted(planned)]
    return sort_by_flowchart_priority(raw)

def basic_courses(catalog: Dict[str, Course]) -> list[Course]:
    basics = [c for c in catalog.values() if not c.prereq_groups and not c.concurrent_groups]
    basics.sort(key=lambda x: x.code)
    return basics

# -------------------------
# Formatting
# -------------------------
def format_groups(groups: List[Set[str]]) -> str:
    if not groups:
        return "None"
    parts: list[str] = []
    for g in groups:
        if len(g) == 1:
            parts.append(next(iter(g)))
        else:
            parts.append("(" + " or ".join(sorted(g)) + ")")
    return " AND ".join(parts)

def format_credits(credits: float | None) -> str:
    if credits is None:
        return ""
    if float(credits).is_integer():
        return f"{int(credits)} cr"
    return f"{credits} cr"

# -------------------------
# Search
# -------------------------
def find_course(catalog: Dict[str, Course], query: str) -> list[Course]:
    query = query.strip()
    if not query:
        return []

    q_norm = _normalize_code(query)
    if q_norm in catalog:
        return [catalog[q_norm]]

    m_num = re.fullmatch(r"(\d{3})", query.strip())
    if m_num:
        num = m_num.group(1)
        hits = [c for c in catalog.values() if re.search(rf"\b{num}[A-Z]?\b", c.code)]
        return sorted(hits, key=lambda x: x.code)

    ql = query.lower()
    hits = [c for c in catalog.values() if (ql in c.name.lower() or ql in c.code.lower())]
    return sorted(hits, key=lambda x: x.code)

# -------------------------
# Why-not
# -------------------------
def explain_why_not(catalog: Dict[str, Course], course_code: str, completed: set[str]) -> str:
    course_code = _normalize_code(course_code)
    completed = {_normalize_code(c) for c in completed}

    if course_code not in catalog:
        return f"I couldn't find {course_code} in this department catalog."

    c = catalog[course_code]

    missing_pre = []
    for group in c.prereq_groups:
        if not (group & completed):
            missing_pre.append(sorted(group))

    missing_conc = []
    for group in c.concurrent_groups:
        if not (group & (completed | {course_code})):
            missing_conc.append(sorted(group))

    if not missing_pre and not missing_conc:
        return f"You already satisfy enforced prereqs/concurrent requirements for {c.code}."

    lines = [f"Why you can't take {c.code} — {c.name} yet:"]
    if missing_pre:
        lines.append("Missing enforced prerequisites (need at least one from each group):")
        for g in missing_pre:
            lines.append(f"  - ({' or '.join(g)})" if len(g) > 1 else f"  - {g[0]}")
    if missing_conc:
        lines.append("Missing enforced concurrent requirement(s) (need at least one from each group):")
        for g in missing_conc:
            lines.append(f"  - ({' or '.join(g)})" if len(g) > 1 else f"  - {g[0]}")
    return "\n".join(lines)

# -------------------------
# vis-network prereq graph
# -------------------------
def build_progression_graph(
    catalog: Dict[str, Course],
    completed: set[str],
    *,
    max_depth: int = 2,
    max_nodes: int = 220,
) -> tuple[list[dict], list[dict], list[Course]]:
    completed = {_normalize_code(c) for c in completed if c.strip()}
    eligible = available_courses(catalog, completed)
    eligible_codes = {_normalize_code(c.code) for c in eligible}

    seed = set(completed) | set(eligible_codes)
    seen = set()
    frontier = set(seed)

    def deps_of(code: str) -> set[str]:
        code = _normalize_code(code)
        c = catalog.get(code)
        if not c:
            return set()
        deps = set()
        for g in c.prereq_groups:
            deps |= {_normalize_code(x) for x in g}
        for g in c.concurrent_groups:
            deps |= {_normalize_code(x) for x in g}
        return deps

    for _ in range(max_depth):
        next_frontier = set()
        for code in frontier:
            if code in seen:
                continue
            seen.add(code)
            for dep in deps_of(code):
                if dep and dep in catalog:
                    next_frontier.add(dep)
        frontier = next_frontier

    included = (set(seed) | seen | frontier)
    included_list = sorted(list(included))[:max_nodes]
    included = set(included_list)

    nodes: list[dict] = []
    for code in included_list:
        c = catalog.get(code)
        if not c:
            continue

        status = "locked"
        if code in completed:
            status = "completed"
        elif code in eligible_codes:
            status = "eligible"

        lvl = course_level(code) or 0
        nodes.append({
            "id": code,
            "label": f"{code}\\n{c.name}",
            "status": status,
            "level": 400 if lvl >= 400 else lvl,
        })

    edges: list[dict] = []
    for code in included_list:
        c = catalog.get(code)
        if not c:
            continue

        for group in c.prereq_groups:
            for pre in group:
                pre = _normalize_code(pre)
                if pre in included and pre in catalog:
                    edges.append({"from": pre, "to": code, "label": "prereq", "arrows": "to", "dashes": False})

        for group in c.concurrent_groups:
            for co in group:
                co = _normalize_code(co)
                if co in included and co in catalog:
                    edges.append({"from": co, "to": code, "label": "concurrent", "arrows": "to", "dashes": True})

    return nodes, edges, eligible

# -------------------------
# Local semantic index
# -------------------------
def _course_to_doc_text(c: Course) -> str:
    parts = [f"Course: {c.code}", f"Title: {c.name}"]
    if c.credits is not None:
        parts.append(f"Credits: {c.credits}")
    if c.description:
        parts.append(f"Description: {c.description}")
    if c.prereq_groups:
        parts.append(f"Enforced Prerequisites: {format_groups(c.prereq_groups)}")
    if c.concurrent_groups:
        parts.append(f"Enforced Concurrent at Enrollment: {format_groups(c.concurrent_groups)}")
    return "\n".join(parts)

def _index_path(dept: str) -> str:
    return f"{dept.lower()}_index.json"

def _l2_norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    denom = _l2_norm(a) * _l2_norm(b)
    if denom == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denom

def build_local_embeddings_index(
    catalog: Dict[str, Course],
    dept: str,
    *,
    embedding_model: str = "nomic-embed-text",
) -> str:
    dept = dept.upper()
    path = _index_path(dept)

    records: list[dict] = []
    for code, c in catalog.items():
        text = _course_to_doc_text(c)
        vec = ollama_embed(text, model=embedding_model)
        lvl = course_level(c.code) or 0
        records.append({
            "id": f"{dept}:{code}",
            "dept": dept,
            "code": c.code,
            "name": c.name,
            "level": 400 if (lvl and lvl >= 400) else lvl,
            "text": text,
            "embedding_model": embedding_model,
            "vector": vec,
        })

    with open(path, "w", encoding="utf-8") as f:
        json.dump({"dept": dept, "embedding_model": embedding_model, "records": records}, f)

    return path

def load_local_index(dept: str) -> dict:
    dept = dept.upper()
    path = _index_path(dept)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing local index {path}. Build it first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def semantic_search_courses(
    dept: str,
    query: str,
    *,
    top_k: int = 10,
    level_filters: Optional[set[int]] = None,
    embedding_model: str = "nomic-embed-text",
) -> list[dict]:
    idx = load_local_index(dept)
    q_vec = ollama_embed(query, model=embedding_model)

    matches = []
    for rec in idx["records"]:
        if level_filters:
            lvl = rec.get("level")
            if lvl == 0:
                continue
            if lvl >= 400:
                lvl = 400
            if lvl not in level_filters:
                continue

        score = _cosine_similarity(q_vec, rec["vector"])
        matches.append({
            "score": float(score),
            "code": rec.get("code"),
            "name": rec.get("name"),
            "level": rec.get("level"),
            "text": rec.get("text"),
        })

    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches[:top_k]

# -------------------------
# RAG + memory-ready
# -------------------------
def build_rag_context(
    dept: str,
    completed: set[str],
    eligible: list[Course],
    *,
    max_eligible: int = 60,
) -> str:
    completed_sorted = sorted({_normalize_code(c) for c in completed})

    eligible = eligible[:max_eligible]
    eligible_lines = []
    for c in eligible:
        tag = " [FLOWCHART PRIORITY]" if is_flowchart_course(c.code) else ""
        eligible_lines.append(
            f"{c.code} — {c.name} ({format_credits(c.credits)}){tag}\n"
            f"Prereqs: {format_groups(c.prereq_groups)}\n"
            f"Concurrent: {format_groups(c.concurrent_groups)}\n"
            f"Description: {c.description or ''}"
        )

    return (
        f"Department: {dept}\n"
        f"Completed Courses: {', '.join(completed_sorted) if completed_sorted else 'None'}\n\n"
        "Eligible Courses This Term:\n"
        + ("\n\n---\n\n".join(eligible_lines) if eligible_lines else "None")
    )

def rag_answer(
    dept: str,
    question: str,
    completed: set[str],
    eligible: list[Course],
    *,
    chat_history: Optional[list[dict]] = None,
    chat_model: str = "llama3",
) -> str:
    context = build_rag_context(dept, completed, eligible)

    prompt = (
        "You are helping a Penn State student plan courses.\n"
        "Rules:\n"
        "1) Use ONLY the provided context.\n"
        "2) ONLY recommend courses that appear under 'Eligible Courses This Term'.\n"
        "3) If none are eligible, explain what prerequisites they likely need next.\n"
        "4) If the student asks follow-ups, stay consistent with earlier answers.\n\n"
        f"{context}\n\n"
        f"Student question: {question}\n\n"
        "Output format:\n"
        "1) Top recommendations (3-6)\n"
        "2) Why (tie it to completed courses)\n"
        "3) If relevant: 1-2 next prerequisites to unlock more options\n"
    )

    messages = [{"role": "system", "content": "You are a helpful PSU course planning assistant."}]
    if chat_history:
        messages.extend(chat_history)
    messages.append({"role": "user", "content": prompt})
    return ollama_chat_messages(messages, model=chat_model)

# -------------------------
# LLM Mermaid Flowchart generator (JSON-only output)
# -------------------------
def _sanitize_mermaid(md: str) -> str:
    md = (md or "").strip()
    md = re.sub(r"^```(?:mermaid)?\s*", "", md, flags=re.IGNORECASE)
    md = re.sub(r"\s*```$", "", md)

    if not re.match(r"^\s*(flowchart|graph)\s+", md, re.IGNORECASE):
        md = "flowchart TD\n" + md

    md = md.replace("\r", "")
    return md.strip()

def generate_llm_flowchart_mermaid(
    dept: str,
    completed: set[str],
    eligible: list[Course],
    question: str,
    *,
    chat_model: str = "llama3",
    max_eligible: int = 12,
) -> Tuple[str, str]:
    completed_sorted = sorted({_normalize_code(c) for c in completed})
    eligible = eligible[:max_eligible]
    eligible_codes = [c.code for c in eligible]

    prompt = (
        "You must return STRICT JSON only. No markdown. No code fences.\n"
        'Return this JSON shape exactly: {"explanation": "...", "mermaid": "..."}\n\n'
        "Mermaid rules:\n"
        "- Use: flowchart TD\n"
        "- Node IDs must be letters+numbers only (A1, A2, B1...)\n"
        '- Node labels must be quoted in brackets: A1["CMPSC 131"]\n'
        "- Avoid special characters in labels (no :, no < >, no quotes inside labels)\n"
        "- Keep it compact (5-20 nodes max)\n"
        "- Use ONLY the provided completed + eligible lists\n"
        "- Show completed on the left, eligible on the right\n"
        "- Mark 1-3 recommended eligible courses with a class name 'rec'\n"
        "- Do NOT include Mermaid init directives (%%{init:...}%%)\n\n"
        f"Department: {dept}\n"
        f"Completed: {', '.join(completed_sorted) if completed_sorted else 'None'}\n"
        f"Eligible: {', '.join(eligible_codes) if eligible_codes else 'None'}\n"
        f"Student question: {question}\n\n"
        "Example mermaid value:\n"
        "flowchart TD\n"
        '  A1["CMPSC 131"] --> B1["CMPSC 132"]\n'
        "  class B1 rec\n"
    )

    raw = ollama_chat(prompt, model=chat_model).strip()

    try:
        obj = json.loads(raw)
        expl = (obj.get("explanation") or "").strip()
        mer = (obj.get("mermaid") or "").strip()
    except Exception:
        return f"LLM returned non-JSON. Raw:\n{raw}", 'flowchart TD\n  A1["No diagram generated"]'

    mer = _sanitize_mermaid(mer)
    return expl, mer