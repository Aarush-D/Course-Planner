# app.py (API only)
import os
import re
from typing import Dict
from flask import Flask, request, session
from flask_cors import CORS

from flowcharts import get_foundation_plan_for_major, format_foundation_plan

from Courseplanner import (
    Course,
    get_dept_catalog,
    available_courses,
    format_groups,
    format_credits,
    group_by_level,
    basic_courses,
    course_level,
    find_course,
    semantic_search_courses,
    rag_answer as rag_answer_fn,
    build_local_embeddings_index,
    explain_why_not,
    build_progression_graph,
    generate_llm_flowchart_mermaid,
)

app = Flask(__name__)
app.secret_key = "dev-change-this"

# Optional: with Angular proxy you don't NEED CORS,
# but leaving this enabled makes direct testing easier.
CORS(app, supports_credentials=True)

DEFAULT_SETTINGS = {
    "show_rag": True,
    "show_semantic": True,
    "show_eligible": True,
    "show_graph": True,
    "show_llm_flowchart": True,
}

def get_settings():
    s = session.get("settings")
    if not isinstance(s, dict):
        s = {}
    merged = DEFAULT_SETTINGS.copy()
    merged.update(s)
    session["settings"] = merged
    return merged

def get_chat_history_for_dept(dept: str) -> list[dict]:
    if "chat_history_by_dept" not in session:
        session["chat_history_by_dept"] = {}
    return session["chat_history_by_dept"].get(dept, [])

def set_chat_history_for_dept(dept: str, history: list[dict], max_turns: int = 6):
    if "chat_history_by_dept" not in session:
        session["chat_history_by_dept"] = {}
    session["chat_history_by_dept"][dept] = history[-(max_turns * 2):]

def _normalize_code(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().upper())

def _looks_like_foundation_question(q: str) -> bool:
    q = (q or "").strip().lower()
    triggers = [
        "what courses should i take",
        "what should i take",
        "first semester",
        "second semester",
        "starting out",
        "i have no courses",
        "0 courses",
        "freshman",
        "foundation",
        "beginner",
    ]
    return any(t in q for t in triggers)

@app.get("/api/health")
def api_health():
    return {"status": "ok"}

@app.get("/api/catalog")
def api_catalog():
    dept = request.args.get("dept", "CMPSC").strip().upper()
    catalog = get_dept_catalog(dept)
    # Lightweight list view
    return {
        "dept": dept,
        "count": len(catalog),
        "courses": [
            {
                "code": c.code,
                "name": c.name,
                "credits": c.credits,
                "prereq_groups": [sorted(list(g)) for g in c.prereq_groups],
                "concurrent_groups": [sorted(list(g)) for g in c.concurrent_groups],
                "description": c.description,
            }
            for c in sorted(catalog.values(), key=lambda x: x.code)
        ],
    }

@app.post("/api/plan")
def api_plan():
    """
    This will become your SINGLE endpoint Angular uses for:
    - eligible courses
    - prereq graph
    - search
    - why-not
    - semantic
    - rag
    - llm mermaid
    """
    data = request.get_json(force=True) or {}

    dept = (data.get("dept") or "CMPSC").strip().upper()
    completed_raw = data.get("completed") or []
    completed = {_normalize_code(x) for x in completed_raw if str(x).strip()}

    semantic_query = (data.get("semantic_query") or "").strip()
    rag_question = (data.get("rag_question") or "").strip()
    search_query = (data.get("search_query") or "").strip()
    why_not_query = (data.get("why_not_query") or "").strip()

    # level filters
    level_filters = set(data.get("level_filters") or [100, 200, 300, 400])

    s = get_settings()

    catalog: Dict[str, Course] = get_dept_catalog(dept)

    # Eligible
    eligible = available_courses(catalog, completed) if completed else []
    eligible_no_conc = [c for c in eligible if not c.concurrent_groups]
    eligible_with_conc = [c for c in eligible if c.concurrent_groups]

    # Graph
    graph_nodes, graph_edges = [], []
    if s["show_graph"]:
        try:
            graph_nodes, graph_edges, _ = build_progression_graph(catalog, completed, max_depth=2)
        except Exception:
            graph_nodes, graph_edges = [], []

    # Search
    search_results = []
    if search_query:
        hits = find_course(catalog, search_query)
        search_results = [
            {
                "code": c.code,
                "name": c.name,
                "credits": c.credits,
                "description": c.description,
                "prereq_groups": [sorted(list(g)) for g in c.prereq_groups],
                "concurrent_groups": [sorted(list(g)) for g in c.concurrent_groups],
            }
            for c in hits
        ]

    # Why-not
    why_not_answer = ""
    if why_not_query:
        why_not_answer = explain_why_not(catalog, why_not_query, completed)

    # Ensure local semantic index if needed
    index_path = f"{dept.lower()}_index.json"
    if (semantic_query or rag_question) and not os.path.exists(index_path):
        build_local_embeddings_index(catalog, dept)

    # Semantic
    semantic_results = []
    if semantic_query and s["show_semantic"]:
        semantic_results = semantic_search_courses(
            dept=dept,
            query=semantic_query,
            top_k=12,
            level_filters=level_filters if level_filters else None,
        )

    # RAG + LLM Mermaid
    rag_response = ""
    llm_explanation = ""
    llm_mermaid = ""

    if rag_question and s["show_rag"]:
        if not completed and _looks_like_foundation_question(rag_question):
            try:
                plan = get_foundation_plan_for_major(dept, semesters=(1, 2))
                rag_response = format_foundation_plan(plan)
            except Exception as e:
                rag_response = f"Flowchart foundation plan failed: {type(e).__name__}: {e}"
        else:
            history = get_chat_history_for_dept(dept)
            rag_response = rag_answer_fn(
                dept=dept,
                question=rag_question,
                completed=completed,
                eligible=eligible,
                chat_history=history,
                chat_model="llama3",
            )
            history = history + [
                {"role": "user", "content": rag_question},
                {"role": "assistant", "content": rag_response},
            ]
            set_chat_history_for_dept(dept, history, max_turns=6)

        if s["show_llm_flowchart"]:
            try:
                expl, mer = generate_llm_flowchart_mermaid(
                    dept=dept,
                    completed=completed,
                    eligible=eligible,
                    question=rag_question,
                )
                llm_explanation = expl
                llm_mermaid = mer
            except Exception as e:
                llm_explanation = f"LLM flowchart generation failed: {type(e).__name__}: {e}"
                llm_mermaid = ""

    return {
        "dept": dept,
        "completed": sorted(list(completed)),
        "eligible": [
            {
                "code": c.code,
                "name": c.name,
                "credits": c.credits,
                "prereq_groups": [sorted(list(g)) for g in c.prereq_groups],
                "concurrent_groups": [sorted(list(g)) for g in c.concurrent_groups],
                "description": c.description,
            }
            for c in eligible
            if ((course_level(c.code) or 0) in level_filters) or ((course_level(c.code) or 0) >= 400 and 400 in level_filters)
        ],
        "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "search_results": search_results,
        "why_not_answer": why_not_answer,
        "semantic_results": semantic_results,
        "rag_response": rag_response,
        "llm_flowchart": {
            "explanation": llm_explanation,
            "mermaid": llm_mermaid,
        },
    }

@app.route("/api/plan", methods=["POST"])
def api_plan_test():   # <-- renamed
    data = request.get_json(silent=True) or {}
    return {
        "debug": "Flask received the request",
        "prompt": data.get("prompt"),
        "completed": data.get("completed", [])
    }

if __name__ == "__main__":
    app.run(debug=True)