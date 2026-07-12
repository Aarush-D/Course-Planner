"""Retrieve relevant advising context from the local RAG index.

Interface used by app.py:
    idx = load_index(path)
    hits = top_k_chunks(idx, query="...", k=6, dept="CMPSC")
    context = format_context(hits)
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text:latest")


def load_index(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _embed_query(query: str, timeout_s: int = 30) -> List[float]:
    r = requests.post(
        f"{OLLAMA_HOST.rstrip('/')}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": query},
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def top_k_chunks(
    index: Dict[str, Any],
    query: str,
    k: int = 6,
    dept: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Cosine-similarity retrieval with optional department filter."""
    q_vec = _embed_query(query)

    scored = []
    for rec in index.get("records", []):
        if dept:
            rec_dept = (rec.get("meta") or {}).get("dept")
            # Keep dept-less docs (general advising rules) plus matching dept docs.
            if rec_dept and rec_dept.upper() != dept.upper():
                continue
        scored.append((_cosine(q_vec, rec.get("vector") or []), rec))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        {"score": round(s, 4), "text": r["text"], "meta": r.get("meta", {})}
        for s, r in scored[:k]
        if s > 0
    ]


def format_context(hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return ""
    parts = []
    for h in hits:
        title = (h.get("meta") or {}).get("title") or "advising notes"
        parts.append(f"[{title}]\n{h['text']}")
    return "\n\n---\n\n".join(parts)
