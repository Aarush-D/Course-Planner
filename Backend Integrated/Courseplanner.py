# Courseplanner.py
import re
import json
import os
import math
import requests
from dataclasses import dataclass
from typing import List, Set, Dict, Optional, Tuple
from bs4 import BeautifulSoup

COURSE_REGEX = re.compile(r"[A-Z]{2,5}\s*\d{2,3}[A-Z]?")

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
        m = re.match(rf"^({dept})\s+(\d{{2,3}}[A-Z]?)\s*:\s*(.+)$", title_text)
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
                is_pr = "enforced prerequisite" in label
                is_co = "enforced concurrent at enrollment" in label
                if not (is_pr or is_co):
                    continue
                target = prereq_groups if is_pr else concurrent_groups

                parent_p = strong.parent
                if parent_p:
                    g: Set[str] = set()
                    for a in parent_p.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            g.add(txt)
                    if g:
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

    return [catalog[code] for code in sorted(planned)]

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
        eligible_lines.append(
            f"{c.code} — {c.name} ({format_credits(c.credits)})\n"
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