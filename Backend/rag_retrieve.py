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
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text:latest")

# Bound on how many distinct queries we'll keep embeddings for. A chat
# session tends to re-send the same (or a near-identical) prompt several
# times in a row -- e.g. every settings-only re-plan (toggling "Allow
# Summer", switching a dropdown) replays the last prompt against the
# planner, which also re-triggers RAG retrieval with that exact string.
# Caching the embedding avoids a redundant network round-trip to Ollama
# for those repeats.
_EMBED_CACHE_SIZE = 256


@lru_cache(maxsize=_EMBED_CACHE_SIZE)
def _embed_query_cached(query: str, timeout_s: int) -> Tuple[float, ...]:
    r = requests.post(
        f"{OLLAMA_HOST.rstrip('/')}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": query},
        timeout=timeout_s,
    )
    r.raise_for_status()
    return tuple(r.json()["embedding"])


def load_index(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _embed_query(query: str, timeout_s: int = 30) -> List[float]:
    """Public wrapper kept for callers/tests that import this name directly;
    the actual network call is cached (see `_embed_query_cached`)."""
    return list(_embed_query_cached(query, timeout_s))


def _cosine(a: List[float], b: List[float]) -> float:
    """Kept for backward compatibility / callers that score a single pair.
    `top_k_chunks` no longer uses this in a per-record Python loop -- see
    the vectorized numpy scoring below."""
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
    """Cosine-similarity retrieval with optional department filter.

    Scoring is vectorized with numpy (one matrix-vector product over every
    candidate record) instead of a per-record pure-Python loop -- this was
    previously O(records) Python-level `sum()`/`sqrt()` calls on every
    single chat request. Behavior (dept filtering, score>0 cutoff, top-k
    ordering) is unchanged.
    """
    q_vec = _embed_query(query)
    if not q_vec:
        return []
    q_arr = np.asarray(q_vec, dtype=np.float64)
    q_norm = float(np.linalg.norm(q_arr))
    if q_norm == 0.0:
        return []

    candidates: List[Dict[str, Any]] = []
    vectors: List[List[float]] = []
    for rec in index.get("records", []):
        if dept:
            rec_dept = (rec.get("meta") or {}).get("dept")
            # Keep dept-less docs (general advising rules) plus matching dept docs.
            if rec_dept and rec_dept.upper() != dept.upper():
                continue
        vec = rec.get("vector") or []
        # Only score records whose embedding dimension actually matches the
        # query's -- mirrors the old `_cosine` behavior of scoring a
        # mismatched/empty vector as 0.0 (i.e. excluded, since only
        # positive scores are ever returned below).
        if len(vec) != q_arr.shape[0]:
            continue
        candidates.append(rec)
        vectors.append(vec)

    if not candidates:
        return []

    matrix = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1)
    denom = norms * q_norm
    sims = np.divide(
        matrix @ q_arr,
        denom,
        out=np.zeros(len(candidates), dtype=np.float64),
        where=denom > 0,
    )

    order = np.argsort(-sims)
    results: List[Dict[str, Any]] = []
    for i in order:
        score = float(sims[i])
        if score <= 0:
            break  # sorted descending -- everything after this is <= 0 too
        if len(results) >= k:
            break
        rec = candidates[i]
        results.append({"score": round(score, 4), "text": rec["text"], "meta": rec.get("meta", {})})
    return results


def format_context(hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return ""
    parts = []
    for h in hits:
        title = (h.get("meta") or {}).get("title") or "advising notes"
        parts.append(f"[{title}]\n{h['text']}")
    return "\n\n---\n\n".join(parts)
