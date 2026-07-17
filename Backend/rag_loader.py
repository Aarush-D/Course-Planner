# Backend/rag_loader.py
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class RagDoc:
    doc_id: str
    source_path: str
    kind: str  # "json_course" | "text"
    title: str
    text: str
    meta: Dict[str, Any]


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _clean_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def _guess_dept_from_filename(path: str) -> Optional[str]:
    base = os.path.basename(path).lower()
    for d in ["cmpsc", "cmpen", "math", "stat", "phys", "engl", "esl", "cas", "engr",
              "biol", "chem", "bmb", "hpa", "psych", "soc", "nutr", "phil", "bioet"]:
        if d in base:
            return d.upper()
    return None


def load_text_docs(rag_root: str) -> List[RagDoc]:
    docs: List[RagDoc] = []

    # Your structure: rag_data/txt/*.txt
    txt_dir = os.path.join(rag_root, "txt")
    candidates = []
    candidates += glob.glob(os.path.join(txt_dir, "*.txt"))
    candidates += glob.glob(os.path.join(rag_root, "*.txt"))  # allow optional root txt

    for path in sorted(set(candidates)):
        raw = _read_text(path)
        text = _clean_text(raw)
        if not text:
            continue
        dept = _guess_dept_from_filename(path)

        docs.append(
            RagDoc(
                doc_id=f"text:{os.path.basename(path)}",
                source_path=path,
                kind="text",
                title=os.path.basename(path),
                text=text,
                meta={"dept": dept} if dept else {},
            )
        )

    return docs


def _json_to_course_docs(path: str) -> List[RagDoc]:
    """
    Supports your catalog JSON files where each item looks like:
    { "code": "...", "name": "...", "description": "...", "prereq_groups": [...] }
    """
    raw = _read_text(path)
    obj = json.loads(raw)

    if not isinstance(obj, dict):
        raise ValueError(f"Catalog JSON must be an object (dict): {path}")

    dept = _guess_dept_from_filename(path) or str(obj.get("dept") or "").upper() or None
    courses = obj.get("courses")
    if courses is None:
        # Many catalogs are just { "CMPSC 121": {...}, ... } or list-like inside dict.
        # Try to infer:
        if all(isinstance(v, dict) for v in obj.values()):
            # interpret top-level keys as course codes
            courses = []
            for k, v in obj.items():
                if not isinstance(v, dict):
                    continue
                v = dict(v)
                v.setdefault("code", k)
                courses.append(v)
        else:
            # If it's already a dict with "courses": [...]
            raise ValueError(f"Unsupported catalog format for {path}")

    if not isinstance(courses, list):
        raise ValueError(f"Expected courses list in {path}")

    docs: List[RagDoc] = []
    for c in courses:
        if not isinstance(c, dict):
            continue
        code = str(c.get("code") or c.get("id") or "").strip()
        name = str(c.get("name") or "").strip()
        desc = str(c.get("description") or "").strip()
        prereq_groups = c.get("prereq_groups") or []
        conc_groups = c.get("concurrent_groups") or []

        prereq_txt = ""
        if prereq_groups:
            prereq_txt += f"Prerequisites groups: {prereq_groups}\n"
        if conc_groups:
            prereq_txt += f"Concurrent groups: {conc_groups}\n"

        text = _clean_text(
            f"Course: {code}\n"
            f"Title: {name}\n"
            f"Description: {desc}\n"
            f"{prereq_txt}".strip()
        )
        if not text or not code:
            continue

        docs.append(
            RagDoc(
                doc_id=f"course:{dept or 'UNK'}:{code}",
                source_path=path,
                kind="json_course",
                title=f"{code} {name}".strip(),
                text=text,
                meta={
                    "dept": dept,
                    "code": code,
                    "name": name,
                },
            )
        )

    return docs


def load_catalog_docs(rag_root: str) -> List[RagDoc]:
    docs: List[RagDoc] = []
    for path in sorted(glob.glob(os.path.join(rag_root, "*.json"))):
        base = os.path.basename(path).lower()

        # Skip any index/embedding/db/metadata JSON files
        if base.endswith("_index.json") or "index" in base:
            continue

        # Only load likely catalogs
        if not base.endswith("_catalog.json"):
            continue

        docs.extend(_json_to_course_docs(path))
    return docs


def load_all_docs(rag_root: str) -> List[RagDoc]:
    docs = []
    docs += load_text_docs(rag_root)
    docs += load_catalog_docs(rag_root)
    return docs


def chunk_text(
    text: str,
    chunk_size: int = 900,
    overlap: int = 120,
) -> List[str]:
    """
    Simple, robust chunker:
    - Splits by paragraphs
    - Packs paragraphs into ~chunk_size chars
    - Adds overlap to help retrieval
    """
    text = _clean_text(text)
    if not text:
        return []

    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []

    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= chunk_size:
            buf = (buf + "\n\n" + p).strip() if buf else p
        else:
            if buf:
                chunks.append(buf)
            buf = p

    if buf:
        chunks.append(buf)

    if overlap > 0 and len(chunks) > 1:
        # Add overlap by prefixing last overlap chars of prev chunk
        overlapped: List[str] = []
        prev_tail = ""
        for ch in chunks:
            merged = (prev_tail + "\n" + ch).strip() if prev_tail else ch
            overlapped.append(merged)
            prev_tail = ch[-overlap:]
        chunks = overlapped

    return chunks


def build_chunks(rag_root: str) -> List[Dict[str, Any]]:
    """
    Output schema used by Step 3 indexer:
    [
      {
        "chunk_id": "...",
        "doc_id": "...",
        "text": "...",
        "meta": {...}
      }
    ]
    """
    docs = load_all_docs(rag_root)
    out: List[Dict[str, Any]] = []

    for d in docs:
        parts = chunk_text(d.text)
        for i, part in enumerate(parts):
            out.append(
                {
                    "chunk_id": f"{d.doc_id}::chunk{i}",
                    "doc_id": d.doc_id,
                    "text": part,
                    "meta": {
                        **(d.meta or {}),
                        "title": d.title,
                        "kind": d.kind,
                        "source_path": d.source_path,
                        "chunk_index": i,
                    },
                }
            )

    return out