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
    code: str
    name: str
    credits: float | None
    prereq_groups: List[Set[str]]          # AND-of-ORs (Enforced Prerequisite)
    concurrent_groups: List[Set[str]]      # AND-of-ORs (Enforced Concurrent at Enrollment)
    description: str | None = None         # bulletin description text


# ---------------------------
# URL helpers / scraping
# ---------------------------

def psu_dept_url(dept_code: str) -> str:
    dept_slug = dept_code.lower()
    return f"https://bulletins.psu.edu/university-course-descriptions/undergraduate/{dept_slug}/"


def scrape_psu_dept_catalog(dept: str) -> Dict[str, Course]:
    """
    Scrape PSU bulletin for a given department code, e.g. 'CMPSC', 'CMPEN', 'MATH', 'STAT'.
    """
    dept = dept.upper()
    url = psu_dept_url(dept)

    resp = requests.get(url)
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

        # ---- Credits ----
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

        # fallback: sometimes credits appear in title text
        if credits is None:
            m_cred = CREDIT_PATTERN.search(name_with_credits)
            if m_cred:
                try:
                    credits = float(m_cred.group(1))
                except ValueError:
                    credits = None

        # ---- Name (strip trailing "Credits" text) ----
        name = re.sub(r"\d.*Credits.*$", "", name_with_credits).strip()
        name = re.sub(r"\d[-.]?$", "", name).rstrip()

        # ---- Description ----
        desc = None
        desc_block = block.select_one(".courseblockdesc")
        if desc_block:
            desc = desc_block.get_text(" ", strip=True)

        # ---- Enforced Prereq / Enforced Concurrent at Enrollment ----
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

                # 1) Same paragraph as label
                parent_p = strong.parent
                if parent_p:
                    group: Set[str] = set()
                    for a in parent_p.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            group.add(txt)
                    if group:
                        target_list.append(group)

                # 2) Next <ul> if present (guard: must belong to prereq_section)
                ul = strong.find_next("ul")
                if ul and prereq_section in ul.parents:
                    group2: Set[str] = set()
                    for a in ul.find_all("a"):
                        txt = a.get_text(strip=True).replace("\xa0", " ").upper()
                        if COURSE_REGEX.fullmatch(txt):
                            group2.add(txt)
                    if group2:
                        target_list.append(group2)

        catalog[_normalize_code(code)] = Course(
            code=_normalize_code(code),
            name=name,
            credits=credits,
            prereq_groups=prereq_groups,
            concurrent_groups=concurrent_groups,
            description=desc,
        )

    return catalog


# ---------------------------
# Normalization / grouping
# ---------------------------

def _normalize_code(s: str) -> str:
    """
    Normalize strings like "cmpsc131", "CMPSC 131", "cmpsc 131H"
    to "CMPSC 131H" (uppercase, single spaces).
    """
    s = s.strip().upper().replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def course_level(code: str) -> int | None:
    """
    'CMPSC 132' -> 100
    'CMPSC 221' -> 200
    """
    m = re.search(r"\b(\d{3})[A-Z]?\b", code)
    if not m:
        return None
    num = int(m.group(1))
    return (num // 100) * 100


def node_level(code: str) -> int:
    """
    Returns 100/200/300/400 (400 means 400+) or 0 for unknown.
    """
    lvl = course_level(code)
    if lvl is None:
        return 0
    return 400 if lvl >= 400 else lvl


def group_by_level(courses: list[Course]) -> dict[int, list[Course]]:
    levels: dict[int, list[Course]] = {}
    for c in courses:
        lvl = course_level(c.code)
        if lvl is None:
            lvl = 0
        levels.setdefault(lvl, []).append(c)
    for lvl in levels:
        levels[lvl].sort(key=lambda x: x.code)
    return levels


# ---------------------------
# Eligibility logic
# ---------------------------

def can_take_this_term(course: Course, completed: set[str], planned: set[str]) -> bool:
    """
    Available THIS term if:
      - Enforced Prerequisite groups satisfied by COMPLETED
      - Enforced Concurrent satisfied by COMPLETED ∪ PLANNED
    """
    for group in course.prereq_groups:
        if not (group & completed):
            return False

    completed_or_planned = completed | planned
    for group in course.concurrent_groups:
        if not (group & completed_or_planned):
            return False

    return True


def available_courses(catalog: Dict[str, Course], completed: set[str]) -> list[Course]:
    """
    Compute all dept courses you can take THIS term, allowing concurrent enrollment.
    """
    completed = {_normalize_code(c) for c in completed}
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


def basic_courses(catalog: Dict[str, Course]) -> list[Course]:
    basics = [c for c in catalog.values() if not c.prereq_groups and not c.concurrent_groups]
    basics.sort(key=lambda x: x.code)
    return basics


# ---------------------------
# Formatting helpers
# ---------------------------

def format_groups(groups: List[Set[str]]) -> str:
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
    if credits is None:
        return ""
    if float(credits).is_integer():
        return f"{int(credits)} cr"
    return f"{credits} cr"


# ---------------------------
# Search helpers
# ---------------------------

def find_course(catalog: Dict[str, Course], query: str) -> list[Course]:
    """
    Search by exact code, bare number (e.g. "131"), or substring in name/code.
    """
    query = query.strip()
    if not query:
        return []

    q_norm = _normalize_code(query)

    # exact code
    if q_norm in catalog:
        return [catalog[q_norm]]

    # bare 3-digit number
    m_num = re.fullmatch(r"(\d{3})", query.strip())
    if m_num:
        num = m_num.group(1)
        hits = [c for c in catalog.values() if re.search(rf"\b{num}[A-Z]?\b", c.code)]
        return sorted(hits, key=lambda x: x.code)

    # substring search
    q_lower = query.lower()
    hits = [c for c in catalog.values() if (q_lower in c.name.lower() or q_lower in c.code.lower())]
    return sorted(hits, key=lambda x: x.code)


# ---------------------------
# JSON cache helpers
# ---------------------------

def catalog_to_json_dict(catalog: Dict[str, Course]) -> dict:
    out: dict = {}
    for code, course in catalog.items():
        out[code] = {
            "code": course.code,
            "name": course.name,
            "credits": course.credits,
            "prereq_groups": [sorted(list(group)) for group in course.prereq_groups],
            "concurrent_groups": [sorted(list(group)) for group in course.concurrent_groups],
            "description": course.description,
        }
    return out


def catalog_from_json_dict(data: dict) -> Dict[str, Course]:
    catalog: Dict[str, Course] = {}
    for code, obj in data.items():
        prereq_groups = [set(group) for group in obj.get("prereq_groups", [])]
        concurrent_groups = [set(group) for group in obj.get("concurrent_groups", [])]
        catalog[code] = Course(
            code=obj["code"],
            name=obj["name"],
            credits=obj.get("credits"),
            prereq_groups=prereq_groups,
            concurrent_groups=concurrent_groups,
            description=obj.get("description"),
        )
    return catalog


def save_catalog_to_json(path: str, catalog: Dict[str, Course]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog_to_json_dict(catalog), f, indent=2, ensure_ascii=False)


def load_catalog_from_json(path: str) -> Dict[str, Course]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return catalog_from_json_dict(data)


def get_dept_catalog(dept: str) -> Dict[str, Course]:
    dept = dept.upper()
    cache_path = f"{dept.lower()}_catalog.json"

    if os.path.exists(cache_path):
        return load_catalog_from_json(cache_path)

    catalog = scrape_psu_dept_catalog(dept)
    save_catalog_to_json(cache_path, catalog)
    return catalog


def get_cmpsc_catalog() -> Dict[str, Course]:
    return get_dept_catalog("CMPSC")


# ---------------------------
# Graph builders (PREREQ + FUTURE)
# ---------------------------

def build_prereq_graph(
    catalog: Dict[str, Course],
    root_code: str,
    completed: set[str] | None = None,
    available: set[str] | None = None,
    max_depth: int = 2,
) -> tuple[list[dict], list[dict]]:
    """
    Backward / prerequisite graph around root_code.

    nodes: [{id, label, title, color, level}]
    edges: [{id, from, to, label}]  # label: 'prereq' or 'concurrent'
    """
    def norm(s: str) -> str:
        return _normalize_code(s)

    root = norm(root_code)
    if root not in catalog:
        return [], []

    completed = {norm(c) for c in (completed or set())}
    available = {norm(c) for c in (available or set())}

    visited: set[str] = set()
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_id = 0

    def add_node(code: str):
        code_n = norm(code)
        if code_n in nodes:
            return

        if code_n in catalog:
            c = catalog[code_n]
            label = c.code
            title = c.name
        else:
            label = code_n
            title = code_n

        if code_n == root:
            status = "target"
            color = "#ffd54f"
        elif code_n in completed:
            status = "completed"
            color = "#81c784"
        elif code_n in available:
            status = "available"
            color = "#64b5f6"
        else:
            status = "locked"
            color = "#e0e0e0"

        nodes[code_n] = {
            "id": code_n,
            "label": label,
            "title": f"{title} ({status})",
            "color": color,
            "level": node_level(code_n),   # <-- key for your vertical layout
        }

    def dfs(code: str, depth: int):
        nonlocal edge_id
        code_n = norm(code)
        if depth > max_depth:
            return
        if code_n in visited:
            return
        visited.add(code_n)

        add_node(code_n)
        course = catalog.get(code_n)
        if not course:
            return

        # prereqs: pre -> course
        for group in course.prereq_groups:
            for pre in group:
                pre_n = norm(pre)
                add_node(pre_n)
                edges.append({"id": f"e{edge_id}", "from": pre_n, "to": code_n, "label": "prereq"})
                edge_id += 1
                dfs(pre_n, depth + 1)

        # concurrent: conc -> course
        for group in course.concurrent_groups:
            for pre in group:
                pre_n = norm(pre)
                add_node(pre_n)
                edges.append({"id": f"e{edge_id}", "from": pre_n, "to": code_n, "label": "concurrent"})
                edge_id += 1
                dfs(pre_n, depth + 1)

    dfs(root, 0)
    return list(nodes.values()), edges


def build_future_graph(
    catalog: Dict[str, Course],
    completed: set[str],
    available: set[str],
    max_depth: int = 2,
) -> tuple[list[dict], list[dict]]:
    """
    Forward / future graph starting from completed + available.

    nodes: [{id, label, title, color, level}]
    edges: [{id, from, to, label}]  # label: 'prereq' or 'concurrent'
    """
    def norm(s: str) -> str:
        return _normalize_code(s)

    completed = {norm(c) for c in completed}
    available = {norm(c) for c in available}

    adjacency: dict[str, list[tuple[str, str]]] = {}

    for course in catalog.values():
        course_code = norm(course.code)

        for group in course.prereq_groups:
            for pre in group:
                pre_n = norm(pre)
                adjacency.setdefault(pre_n, []).append((course_code, "prereq"))

        for group in course.concurrent_groups:
            for pre in group:
                pre_n = norm(pre)
                adjacency.setdefault(pre_n, []).append((course_code, "concurrent"))

    visited: set[str] = set()
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_id = 0

    def add_node(code: str):
        code_n = norm(code)
        if code_n in nodes:
            return

        if code_n in catalog:
            c = catalog[code_n]
            label = c.code
            title = c.name
        else:
            label = code_n
            title = code_n

        if code_n in completed:
            status = "completed"
            color = "#81c784"
        elif code_n in available:
            status = "available"
            color = "#64b5f6"
        else:
            status = "future"
            color = "#e0e0e0"

        nodes[code_n] = {
            "id": code_n,
            "label": label,
            "title": f"{title} ({status})",
            "color": color,
            "level": node_level(code_n),   # <-- key for your vertical layout
        }

    from collections import deque
    frontier = deque()

    start_codes = completed | available
    for sc in start_codes:
        frontier.append((sc, 0))

    while frontier:
        code, depth = frontier.popleft()
        code_n = norm(code)

        if code_n in visited:
            continue
        visited.add(code_n)

        add_node(code_n)

        if depth >= max_depth:
            continue

        for (nbr, lbl) in adjacency.get(code_n, []):
            nbr_n = norm(nbr)
            add_node(nbr_n)
            edges.append({"id": f"e{edge_id}", "from": code_n, "to": nbr_n, "label": lbl})
            edge_id += 1
            frontier.append((nbr_n, depth + 1))

    return list(nodes.values()), edges


# =========================
# RAG + Semantic Search
# =========================

from typing import Any, Optional
import os
import math

def _course_to_doc_text(c: Course) -> str:
    """
    Turn a course into a single searchable text blob for embeddings.
    """
    parts = [
        f"Course: {c.code}",
        f"Title: {c.name}",
    ]
    if c.credits is not None:
        parts.append(f"Credits: {c.credits}")
    if c.description:
        parts.append(f"Description: {c.description}")
    if c.prereq_groups:
        parts.append(f"Enforced Prerequisites: {format_groups(c.prereq_groups)}")
    if c.concurrent_groups:
        parts.append(f"Enforced Concurrent at Enrollment: {format_groups(c.concurrent_groups)}")

    return "\n".join(parts)


def _chunk_list(xs: list[Any], n: int) -> list[list[Any]]:
    return [xs[i:i+n] for i in range(0, len(xs), n)]


def ensure_pinecone_index(
    index_name: str,
    dimension: int,
    metric: str = "cosine",
):
    """
    Create Pinecone index if it doesn't exist.
    """
    from pinecone import Pinecone, ServerlessSpec

    api_key = os.environ.get("PINECONE_API_KEY")
    region = os.environ.get("PINECONE_REGION", "us-east-1")

    if not api_key:
        raise RuntimeError("Missing PINECONE_API_KEY env var")

    pc = Pinecone(api_key=api_key)

    existing = [idx["name"] for idx in pc.list_indexes()]
    if index_name not in existing:
        pc.create_index(
            name=index_name,
            dimension=dimension,
            metric=metric,
            spec=ServerlessSpec(cloud="aws", region=region),
        )

    return pc.Index(index_name)


def build_course_embeddings_index(
    catalog: Dict[str, Course],
    dept: str,
    *,
    index_name: Optional[str] = None,
    embedding_model: str = "text-embedding-3-small",
    batch_size: int = 96,
) -> None:
    """
    Build/refresh vector index for a department's catalog.
    """
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY env var")

    if index_name is None:
        index_name = os.environ.get("PINECONE_INDEX", "psu-course-planner")

    client = OpenAI(api_key=api_key)

    # 1) Build documents
    docs: list[dict] = []
    for code, c in catalog.items():
        text = _course_to_doc_text(c)
        lvl = course_level(c.code) or 0
        docs.append({
            "id": f"{dept}:{code}",
            "code": c.code,
            "dept": dept,
            "level": 400 if (lvl and lvl >= 400) else lvl,
            "text": text,
            "name": c.name,
        })

    # 2) Embed all texts (batched)
    vectors = []
    for chunk in _chunk_list(docs, batch_size):
        inputs = [d["text"] for d in chunk]
        emb = client.embeddings.create(model=embedding_model, input=inputs)
        for d, item in zip(chunk, emb.data):
            vectors.append((
                d["id"],
                item.embedding,
                {
                    "dept": d["dept"],
                    "code": d["code"],
                    "name": d["name"],
                    "level": d["level"],
                    "text": d["text"],  # store full text as metadata for easy RAG
                }
            ))

    # 3) Ensure Pinecone index exists
    # Dimension comes from the first vector
    dim = len(vectors[0][1]) if vectors else 0
    if dim == 0:
        return

    index = ensure_pinecone_index(index_name=index_name, dimension=dim, metric="cosine")

    # 4) Upsert to Pinecone (batched)
    for chunk in _chunk_list(vectors, 100):
        index.upsert(vectors=chunk)


def semantic_search_courses(
    dept: str,
    query: str,
    *,
    top_k: int = 10,
    level_filters: Optional[set[int]] = None,  # ex: {100,200,300,400}
    embedding_model: str = "text-embedding-3-small",
    index_name: Optional[str] = None,
) -> list[dict]:
    """
    Returns Pinecone matches with metadata.
    """
    from openai import OpenAI
    from pinecone import Pinecone

    if index_name is None:
        index_name = os.environ.get("PINECONE_INDEX", "psu-course-planner")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY env var")

    pine_key = os.environ.get("PINECONE_API_KEY")
    if not pine_key:
        raise RuntimeError("Missing PINECONE_API_KEY env var")

    # Embed the query
    client = OpenAI(api_key=api_key)
    q_emb = client.embeddings.create(model=embedding_model, input=[query]).data[0].embedding

    pc = Pinecone(api_key=pine_key)
    index = pc.Index(index_name)

    # Pinecone metadata filtering
    flt: dict = {"dept": {"$eq": dept}}
    if level_filters:
        # Pinecone supports $in style for many configs; if yours doesn’t, remove this filter and filter in Python.
        flt["level"] = {"$in": sorted(level_filters)}

    res = index.query(
        vector=q_emb,
        top_k=top_k,
        include_metadata=True,
        filter=flt,
    )

    matches = []
    for m in res.matches or []:
        md = m.metadata or {}
        matches.append({
            "score": float(m.score),
            "code": md.get("code"),
            "name": md.get("name"),
            "level": md.get("level"),
            "text": md.get("text"),
        })
    return matches


def rag_answer(
    dept: str,
    question: str,
    *,
    top_k: int = 8,
    level_filters: Optional[set[int]] = None,
    index_name: Optional[str] = None,
) -> str:
    """
    Simple RAG: retrieve relevant courses, then ask an LLM to answer based on them.
    """
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY env var")

    retrieved = semantic_search_courses(
        dept=dept,
        query=question,
        top_k=top_k,
        level_filters=level_filters,
        index_name=index_name,
    )

    context_blocks = []
    for r in retrieved:
        context_blocks.append(
            f"{r['code']} — {r['name']}\n{r['text']}"
        )
    context = "\n\n---\n\n".join(context_blocks) if context_blocks else "(no matches)"

    client = OpenAI(api_key=api_key)

    # Keep the answer grounded in retrieved docs
    resp = client.responses.create(
        model="gpt-5-mini",
        input=[
            {
                "role": "system",
                "content": (
                    "You are a PSU course planner assistant. "
                    "Answer ONLY using the provided course catalog excerpts. "
                    "If the excerpts don’t contain the answer, say what’s missing."
                ),
            },
            {
                "role": "user",
                "content": f"Department: {dept}\n\nQuestion: {question}\n\nCourse excerpts:\n{context}",
            },
        ],
    )

    return resp.output_text




# ---------------------------
# CLI runner (optional)
# ---------------------------

if __name__ == "__main__":
    catalog = get_cmpsc_catalog()
    print(f"Loaded {len(catalog)} CMPSC courses.\n")

    basics = basic_courses(catalog)
    print("=== Basic CMPSC Courses (no prerequisites / no concurrent requirements) ===")
    if not basics:
        print("None\n")
    else:
        grouped_basics = group_by_level(basics)
        for lvl in sorted(grouped_basics.keys()):
            label = "Other-level" if lvl == 0 else f"{lvl}-level"
            print(f"\n  -- {label} --")
            for course in grouped_basics[lvl]:
                cred_str = format_credits(course.credits)
                if cred_str:
                    print(f"- {course.code} ({cred_str}) — {course.name}")
                else:
                    print(f"- {course.code} — {course.name}")
        print()

    raw = input("Completed courses (comma-separated): ").strip()
    completed = {_normalize_code(c) for c in raw.split(",") if c.strip()}

    avail = available_courses(catalog, completed)
    no_concurrent = [c for c in avail if not c.concurrent_groups]
    with_concurrent = [c for c in avail if c.concurrent_groups]

    print("\n=== Eligible (NO Concurrent Requirement) ===")
    for c in no_concurrent:
        cred_str = format_credits(c.credits)
        line = f"- {c.code} ({cred_str}) — {c.name}" if cred_str else f"- {c.code} — {c.name}"
        print(line)
        if c.prereq_groups:
            print(f"    Prereqs: {format_groups(c.prereq_groups)}")
        print()

    print("\n=== Eligible (WITH Enforced Concurrent at Enrollment) ===")
    for c in with_concurrent:
        cred_str = format_credits(c.credits)
        line = f"- {c.code} ({cred_str}) — {c.name}" if cred_str else f"- {c.code} — {c.name}"
        print(line)
        if c.prereq_groups:
            print(f"    Prereqs: {format_groups(c.prereq_groups)}")
        if c.concurrent_groups:
            print(f"    Concurrent: {format_groups(c.concurrent_groups)}")
        print()
