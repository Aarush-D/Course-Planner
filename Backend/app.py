from flask import Flask, request, jsonify
from flask_cors import CORS

from Courseplanner import (
    get_dept_catalog,
    available_courses,
    find_course,
    explain_why_not,
    semantic_search_courses,
    build_local_embeddings_index,
    rag_answer as rag_answer_fn,
    build_progression_graph,
    generate_llm_flowchart_mermaid,
)

from flowcharts import get_foundation_plan_for_major, format_foundation_plan

import os
import re

app = Flask(__name__)
CORS(app)  # easiest for local dev; later you can tighten this

def _looks_like_foundation_question(q: str) -> bool:
    q = (q or "").strip().lower()
    triggers = [
        "what courses should i take",
        "what should i take",
        "first semester",
        "second semester",
        "starting out",
        "freshman",
        "foundation",
        "beginner",
        "no courses",
        "0 courses",
    ]
    return any(t in q for t in triggers)

@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok"})

@app.post("/api/plan")
def api_plan():
    data = request.get_json(force=True) or {}

    dept = (data.get("dept") or "CMPSC").strip().upper()
    question = (data.get("prompt") or "").strip()
    completed = data.get("completed") or []
    completed = {str(c).strip().upper() for c in completed if str(c).strip()}

    # load catalog
    catalog = get_dept_catalog(dept)

    # eligible
    eligible = available_courses(catalog, completed) if completed else []
    eligible_codes = [c.code for c in eligible]

    # build semantic index on-demand if needed
    index_path = f"{dept.lower()}_index.json"
    if question and not os.path.exists(index_path):
        build_local_embeddings_index(catalog, dept)

    # search / why-not optional
    search_query = (data.get("search_query") or "").strip()
    why_not_query = (data.get("why_not_query") or "").strip()

    search_results = []
    if search_query:
        search_results = [
            {
                "code": c.code,
                "name": c.name,
                "credits": c.credits,
                "description": c.description,
                "prereq_groups": [sorted(list(g)) for g in c.prereq_groups],
                "concurrent_groups": [sorted(list(g)) for g in c.concurrent_groups],
            }
            for c in find_course(catalog, search_query)
        ]

    why_not_answer = ""
    if why_not_query:
        why_not_answer = explain_why_not(catalog, why_not_query, completed)

    # left-side prereq graph (vis-network)
    graph_nodes, graph_edges = [], []
    try:
        graph_nodes, graph_edges, _ = build_progression_graph(catalog, completed, max_depth=2)
    except Exception:
        graph_nodes, graph_edges = [], []

    # semantic search (optional, if UI uses it)
    semantic_query = (data.get("semantic_query") or "").strip()
    semantic_results = []
    if semantic_query:
        semantic_results = semantic_search_courses(
            dept=dept,
            query=semantic_query,
            top_k=12,
            level_filters=None
        )

    # RAG answer + right-side mermaid
    rag_response = ""
    mermaid = ""
    explanation = ""

    if question:
        if not completed and _looks_like_foundation_question(question):
            # foundation plan from PSU flowchart PDFs (your flowcharts.py)
            plan = get_foundation_plan_for_major(dept, semesters=(1, 2))
            rag_response = format_foundation_plan(plan)

            # Optional: also generate a “nice” mermaid from that plan (if you want)
            # explanation, mermaid = ("...", "flowchart TD ...")
        else:
            rag_response = rag_answer_fn(
                dept=dept,
                question=question,
                completed=completed,
                eligible=eligible,
                chat_history=None,   # you can add history later
                chat_model="llama3",
            )

        # generate LLM mermaid recommendation flowchart (Ollama)
        try:
            explanation, mermaid = generate_llm_flowchart_mermaid(
                dept=dept,
                completed=completed,
                eligible=eligible,
                question=question,
                chat_model="llama3"
            )
        except Exception as e:
            explanation = f"LLM flowchart generation failed: {type(e).__name__}: {e}"
            mermaid = ""

    # return everything to Angular
    return jsonify({
        "dept": dept,
        "completed": sorted(list(completed)),
        "eligible": eligible_codes,
        "rag_response": rag_response,
        "semantic_results": semantic_results,
        "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "llm_flowchart": {"explanation": explanation, "mermaid": mermaid},
        "search_results": search_results,
        "why_not_answer": why_not_answer,
    })

if __name__ == "__main__":
    app.run(port=5000, debug=True)