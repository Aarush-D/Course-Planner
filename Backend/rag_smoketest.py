# Backend/rag_smoketest.py
import os
from rag_loader import build_chunks

if __name__ == "__main__":
    rag_root = os.path.join(os.path.dirname(__file__), "rag_data")
    chunks = build_chunks(rag_root)
    print("Chunks:", len(chunks))
    print("Example chunk:", chunks[0]["chunk_id"])
    print(chunks[0]["text"][:300])