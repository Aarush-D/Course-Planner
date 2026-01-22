from __future__ import annotations
from flask import Flask, jsonify, request
from flask_cors import CORS
import ollama
import threading

from Courseplanner import (
    get_dept_catalog,
    build_progression_graph,
    semantic_search_courses,
    find_course,
)

try:
    from flowcharts import get_foundation_plan_for_major, format_foundation_plan
except Exception:
    get_foundation_plan_for_major = None
    format_foundation_plan = None


app = Flask(__name__)
CORS(app)


@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok"})


# ---------------- OLLAMA SAFE WRAPPER ----------------

def ask_ollama(prompt: str, model: str = "llama3") -> str:
    # Keep responses fast + deterministic
    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": "You are a helpful Penn State academic advisor."},
            {"role": "user", "content": prompt},
        ],
        options={
            "temperature": 0.2,
            "num_predict": 350,   # keeps Mermaid short
        },
    )
    return resp["message"]["content"]


# ---------------- MAIN ENDPOINT ----------------

@app.post("/api/plan")
def api_plan():
    print("✅ /api/plan hit")

    payload = request.get_json(force=True) or {}

    dept = (payload.get("dept") or "CMPSC").upper()
    completed = payload.get("completed") or []
    prompt = (payload.get("prompt") or "").strip()

    semantic_query = (payload.get("semantic_query") or "").strip()
    search_query = (payload.get("search_query") or "").strip()
    why_not_query = (payload.get("why_not_query") or "").strip()

    catalog = get_dept_catalog(dept)

    # ----- Graph -----
    try:
        nodes, edges, eligible_courses = build_progression_graph(
            catalog, completed, max_depth=2
        )
    except Exception as e:
        print("❌ Graph failed:", e)
        nodes, edges, eligible_courses = [], [], []

    graph = {"nodes": nodes, "edges": edges}

    eligible_codes = [
        c.code if hasattr(c, "code") else str(c) for c in eligible_courses
    ]

    llm_flowchart = {"mermaid": "flowchart TD\n", "explanation": "LLM-generated course flowchart"}

    if prompt:
        try:
            flow_prompt = f"""
    Output ONLY Mermaid code. No backticks. No explanation.

    Start with: flowchart TD

    Use node ids with underscores (example: CMPSC_131).
    Use arrows like: CMPSC_131 --> CMPSC_132

    Completed courses:
    {completed}

    Eligible next courses:
    {eligible_codes[:10]}

    Make a simple, readable graph that connects completed -> eligible when reasonable.
    If you are unsure of prereqs, still show completed -> eligible.
    """
            mermaid = ask_ollama(flow_prompt)

            # Safety cleanup (remove ``` if model adds it)
            mermaid = mermaid.replace("```mermaid", "").replace("```", "").strip()

            llm_flowchart = {
                "mermaid": mermaid,
                "explanation": "LLM-generated course flowchart",
            }
        except Exception as e:
            llm_flowchart = {
                "mermaid": "",
                "explanation": f"(Flowchart generation failed) {e}",
            }

    # ----- Searches -----
    semantic_results = []
    search_results = []

    if semantic_query:
        semantic_results = semantic_search_courses(dept, semantic_query, top_k=5)

    if search_query:
        search_results = find_course(catalog, search_query)

    # ----- Foundation plan -----
    rag_response = ""
    if (
        prompt
        and not completed
        and get_foundation_plan_for_major
        and format_foundation_plan
        and "first" in prompt.lower()
    ):
        try:
            plan = get_foundation_plan_for_major(dept, semesters=(1, 2))
            rag_response = format_foundation_plan(plan)
        except Exception as e:
            rag_response = f"(Flowchart plan failed: {e})"

    # ----- Ollama recommendations -----
    if prompt and not rag_response:
        rec_prompt = f"""
You are a Penn State academic advisor.

Department: {dept}
Completed courses: {completed}
Eligible next courses: {eligible_codes[:15]}

Student question:
{prompt}

Return:
1) 5 recommended courses with reasons
2) 2 stretch courses
3) 2 planning tips
"""
        rag_response = ask_ollama(rec_prompt)

    # ----- Mermaid flowchart (LLM) -----
    flow_prompt = f"""
flowchart TD
Completed --> Eligible

Completed courses: {completed}
Eligible courses: {eligible_codes[:10]}
"""

    llm_flowchart = {
        "explanation": "LLM-generated course flowchart",
        "mermaid": ask_ollama(flow_prompt),
    }

    response = {
        "dept": dept,
        "completed": completed,
        "eligible": eligible_codes,
        "graph": graph,
        "rag_response": rag_response,
        "semantic_results": semantic_results,
        "search_results": search_results,
        "why_not_answer": why_not_query,
        "llm_flowchart": llm_flowchart,
    }

    print("✅ /api/plan returning")
    return jsonify(response)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)