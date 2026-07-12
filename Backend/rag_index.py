"""Build the local RAG embedding index.

Reads chunks from rag_loader.build_chunks (catalog JSON + advising TXT files),
embeds them with nomic-embed-text via the local Ollama HTTP API, and writes
rag_data/rag_index.json. Re-run whenever rag_data source files change:

    python rag_index.py
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

import requests

from rag_loader import build_chunks

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAG_ROOT = os.path.join(BASE_DIR, "rag_data")
INDEX_PATH = os.path.join(RAG_ROOT, "rag_index.json")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text:latest")


def embed_text(text: str, timeout_s: int = 60) -> List[float]:
    r = requests.post(
        f"{OLLAMA_HOST.rstrip('/')}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def build_index(rag_root: str = RAG_ROOT, index_path: str = INDEX_PATH) -> Dict[str, Any]:
    chunks = build_chunks(rag_root)
    if not chunks:
        raise SystemExit(f"No chunks found under {rag_root} — nothing to index.")

    records = []
    for i, ch in enumerate(chunks):
        vec = embed_text(ch["text"])
        records.append({
            "chunk_id": ch["chunk_id"],
            "doc_id": ch["doc_id"],
            "text": ch["text"],
            "meta": ch.get("meta", {}),
            "vector": vec,
        })
        if (i + 1) % 25 == 0:
            print(f"  embedded {i + 1}/{len(chunks)} chunks...")

    index = {"embedding_model": EMBED_MODEL, "records": records}
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f)
    print(f"Wrote {len(records)} chunks -> {index_path}")
    return index


if __name__ == "__main__":
    try:
        build_index()
    except requests.ConnectionError:
        sys.exit(
            "Could not reach Ollama. Start it first (`ollama serve`) and make sure "
            f"the embedding model is pulled (`ollama pull {EMBED_MODEL}`)."
        )
