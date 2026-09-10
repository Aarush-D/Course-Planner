"""Evaluate RAG retrieval quality against a small labeled question set.

Answers the question a poster/demo reviewer will actually ask: "how do you
know your retrieval is any good?" Nothing else in this codebase measures
that -- app.py's retrieve_rag_context() and rag_retrieve.top_k_chunks()
just return whatever scores highest, with no check anywhere that it's
actually the right chunk.

Ground truth is never hand-typed: for a course question, the "correct"
chunk is whichever indexed json_course record has that exact meta["code"]
-- pulled from the real index, so a stale/renamed course can't silently
make this eval lie. For a general-advising question it's the specific
advising_rules.txt / flowchart_guidelines.txt chunk (matched by
meta["title"]).

Mirrors the real production call exactly (see app.py's
retrieve_rag_context): k=4, dept filter set to the asking student's own
major -- top_k_chunks() then also keeps dept-less docs (general advising
rules) alongside that department's own catalog chunks, same as a real
student's request.

Run: python rag_eval.py
Needs Ollama running locally with nomic-embed-text pulled -- same
requirement as the app itself (see README's "Run locally").
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from rag_retrieve import load_index, top_k_chunks

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "rag_data", "rag_index.json")

RETRIEVAL_K = 4  # matches app.py's retrieve_rag_context default

# Each entry: a natural student-style question, which department it's
# asked "as" (sets the dept filter exactly like a real request would),
# and how to recognize the one correct chunk. Course codes/names below
# are real entries pulled from rag_index.json, spanning every department
# actually indexed -- not invented, so a renamed/dropped course would
# surface here as a real eval failure instead of a silent gap.
EVAL_SET: List[Dict[str, Any]] = [
    # -- General advising rules (dept-less docs -- should surface for ANY major) --
    {"question": "What are the rules for recommending courses to a student?", "dept": "CMPSC",
     "expected": {"title": "advising_rules.txt"}},
    {"question": "How many courses should be recommended at once, and should internships be suggested to a freshman?", "dept": "CMPSC",
     "expected": {"title": "advising_rules.txt"}},
    {"question": "What are the rules for generating a Mermaid flowchart of a student's plan?", "dept": "CMPSC",
     "expected": {"title": "flowchart_guidelines.txt"}},

    # -- Course lookups, 2 per indexed department --
    {"question": "What does BIOET 100 cover?", "dept": "BIOET", "expected": {"code": "BIOET 100"}},
    {"question": "Can you describe Medical and Health Care Ethics?", "dept": "BIOET", "expected": {"code": "BIOET 432"}},
    {"question": "What will I learn in BIOL 422, Advanced Genetics?", "dept": "BIOL", "expected": {"code": "BIOL 422"}},
    {"question": "Tell me about the Taxonomy of Seed Plants course.", "dept": "BIOL", "expected": {"code": "BIOL 414"}},
    {"question": "What is BMB 251 about?", "dept": "BMB", "expected": {"code": "BMB 251"}},
    {"question": "Describe the Elementary Biochemistry Laboratory course.", "dept": "BMB", "expected": {"code": "BMB 212"}},
    {"question": "What topics does Chemistry and Properties of Polymers cover?", "dept": "CHEM", "expected": {"code": "CHEM 480"}},
    {"question": "What is CHEM 111, Experimental Chemistry I, about?", "dept": "CHEM", "expected": {"code": "CHEM 111"}},
    {"question": "What does CMPEN 472 teach about microprocessors?", "dept": "CMPEN", "expected": {"code": "CMPEN 472"}},
    {"question": "Can you describe Introduction to Digital Systems?", "dept": "CMPEN", "expected": {"code": "CMPEN 271"}},
    {"question": "What is CMPSC 311, Introduction to Systems Programming, about?", "dept": "CMPSC", "expected": {"code": "CMPSC 311"}},
    {"question": "Tell me about Assembly Language Programming.", "dept": "CMPSC", "expected": {"code": "CMPSC 313"}},
    {"question": "What does HPA 443 cover about nursing home administration?", "dept": "HPA", "expected": {"code": "HPA 443"}},
    {"question": "Describe the Healthcare Policies and Politics course.", "dept": "HPA", "expected": {"code": "HPA 450"}},
    {"question": "What is covered in Complex Analysis for Mathematics and Engineering?", "dept": "MATH", "expected": {"code": "MATH 410"}},
    {"question": "What does MATH 141, Calculus with Analytic Geometry II, involve?", "dept": "MATH", "expected": {"code": "MATH 141"}},
    {"question": "What will I learn in Micronutrient Metabolism?", "dept": "NUTR", "expected": {"code": "NUTR 446"}},
    {"question": "Describe NUTR 495B, Advanced Field Experience in Nutrition.", "dept": "NUTR", "expected": {"code": "NUTR 495B"}},
    {"question": "What is PHIL 150N, Computing and Society, about?", "dept": "PHIL", "expected": {"code": "PHIL 150N"}},
    {"question": "Tell me about Social and Political Philosophy.", "dept": "PHIL", "expected": {"code": "PHIL 108W"}},
    {"question": "What does History and Systems of Psychology cover?", "dept": "PSYCH", "expected": {"code": "PSYCH 439"}},
    {"question": "Describe PSYCH 269, Evolutionary Psychology.", "dept": "PSYCH", "expected": {"code": "PSYCH 269"}},
    {"question": "What is Sociology of Gender about?", "dept": "SOC", "expected": {"code": "SOC 110"}},
    {"question": "What does Introductory Sociology cover?", "dept": "SOC", "expected": {"code": "SOC 1"}},
    {"question": "What is STAT 460, Intermediate Applied Statistics, about?", "dept": "STAT", "expected": {"code": "STAT 460"}},
    {"question": "Describe Introduction to Mathematical Statistics.", "dept": "STAT", "expected": {"code": "STAT 415"}},
]


def _matches(meta: Dict[str, Any], expected: Dict[str, str]) -> bool:
    if "code" in expected:
        return meta.get("code") == expected["code"]
    if "title" in expected:
        return meta.get("title") == expected["title"]
    return False


def run_eval(index: Dict[str, Any], k: int = RETRIEVAL_K) -> List[Dict[str, Any]]:
    results = []
    for item in EVAL_SET:
        hits = top_k_chunks(index, query=item["question"], k=k, dept=item.get("dept"))
        hit_rank: Optional[int] = None
        for i, h in enumerate(hits, start=1):
            if _matches(h.get("meta", {}), item["expected"]):
                hit_rank = i
                break
        results.append({**item, "hit_rank": hit_rank, "top_hits": hits})
    return results


def print_report(results: List[Dict[str, Any]], k: int) -> None:
    total = len(results)
    hits = [r for r in results if r["hit_rank"] is not None]
    misses = [r for r in results if r["hit_rank"] is None]

    print(f"\n{'=' * 60}")
    print(f"RAG RETRIEVAL EVAL — Recall@{k}")
    print(f"{'=' * 60}")
    print(f"{len(hits)}/{total} correct  ->  {100 * len(hits) / total:.0f}% Recall@{k}\n")

    if misses:
        print(f"Missed ({len(misses)}):")
        for r in misses:
            print(f"  - {r['question']!r} (expected {r['expected']})")
        print()

    # One fully worked example for the "show" half of the pitch — the
    # first hit that landed exactly at rank 1, so it's a clean, honest
    # example rather than a cherry-picked best case.
    example = next((r for r in hits if r["hit_rank"] == 1), hits[0] if hits else None)
    if example:
        print("Example (for the demo/poster):")
        print(f"  Q: {example['question']!r}")
        for i, h in enumerate(example["top_hits"], start=1):
            title = h.get("meta", {}).get("title") or h.get("meta", {}).get("code") or "?"
            marker = " <-- correct" if _matches(h.get("meta", {}), example["expected"]) else ""
            print(f"    {i}. [{h['score']:.3f}] {title}{marker}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    if not os.path.exists(INDEX_PATH):
        sys.exit(f"No index at {INDEX_PATH} -- run `python rag_index.py` first.")
    try:
        idx = load_index(INDEX_PATH)
        results = run_eval(idx)
    except Exception as e:  # noqa: BLE001 -- top-level CLI entry point
        sys.exit(
            f"Eval failed ({e}). Make sure Ollama is running "
            "(`ollama serve`) with nomic-embed-text pulled."
        )
    print_report(results, RETRIEVAL_K)
