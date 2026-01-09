from __future__ import annotations
from ollama_http import ollama_generate
from flask import Flask, jsonify, request
from flask_cors import CORS

from Courseplanner import (
    get_dept_catalog,
    build_progression_graph,
    course_level,
    rag_answer,
)

# Optional flowchart-based "foundation plan" (PDF parsing)
# If you don't want this part yet, you can remove these imports + the related block below.
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


def _to_sorted_list(x):
    """Convert sets/lists/etc to JSON-safe sorted list."""
    if x is None:
        return []
    if isinstance(x, set):
        return sorted(list(x))
    if isinstance(x, list):
        return x
    return [x]


def _looks_like_foundation_question(q: str) -> bool:
    q = (q or "").lower()
    triggers = [
        "foundation",
        "first year",
        "freshman",
        "starting",
        "what should i take first",
        "beginner",
        "intro sequence",
        "flowchart",
        "recommended sequence",
    ]
    return any(t in q for t in triggers)


@app.post("/api/plan")
def api_plan():
    payload = request.get_json(force=True) or {}

    # ✅ This matches your Angular payload
    dept = (payload.get("dept") or "CMPSC").upper()
    completed = payload.get("completed") or []
    prompt = (payload.get("prompt") or "").strip()

    # Optional query inputs (you can ignore these now, but future-proof)
    semantic_query = (payload.get("semantic_query") or "").strip()
    search_query = (payload.get("search_query") or "").strip()
    why_not_query = (payload.get("why_not_query") or "").strip()

    # --- Always load catalog ---
    catalog = get_dept_catalog(dept)

    # --- Build graph / eligibility ---
    graph_nodes, graph_edges = [], []
    eligible = []
    why_not_answer = ""

    try:
        # build_progression_graph returns (nodes, edges, eligible_codes)
        graph_nodes, graph_edges, eligible = build_progression_graph(
            catalog, completed, max_depth=2
        )
    except Exception as e:
        why_not_answer = f"Graph build failed: {e}"
        graph_nodes, graph_edges, eligible = [], [], []

    graph = {"nodes": graph_nodes, "edges": graph_edges}

    # --- Searches ---
    semantic_results = []
    search_results = []

    if semantic_query:
        try:
            semantic_results = compute_semantic_search(
                dept=dept, query=semantic_query, top_k=5
            )
        except Exception as e:
            semantic_results = [{"error": str(e)}]

    if search_query:
        try:
            search_results = keyword_search(catalog, search_query, top_k=10)
        except Exception as e:
            search_results = [{"error": str(e)}]

    # --- FLOWCHART foundation plan (PDF based) ---
    # If user asks a "foundation/first-year" question and you have flowcharts wired,
    # answer from the PDF flowchart plan.
    rag_response = ""
    llm_flowchart = generate_mermaid_flowchart(
    dept=dept,
    completed=completed,
    eligible=eligible,
)

    if prompt and get_foundation_plan_for_major and format_foundation_plan:
        if (not completed) and _looks_like_foundation_question(prompt):
            try:
                plan = get_foundation_plan_for_major(dept, semesters=(1, 2))
                rag_response = format_foundation_plan(plan)
            except Exception as e:
                rag_response = f"(Flowchart plan failed) {e}"

    # --- Otherwise: Ollama RAG answer ---
    # ✅ This is where recommendations/next steps text comes from.
    if prompt and not rag_response:
      try:
        rec_prompt = f"""
            You are a Penn State academic advisor.

            Department: {dept}
            Completed courses: {completed}
            Eligible next courses: {eligible[:20]}

            Student question:
            {prompt}

            Return:
            1) 5 recommended courses with 1-sentence reasoning each
            2) 2 stretch courses
            3) 2 short planning tips

            Be concise.
            """
        rag_response = ollama_generate(rec_prompt)
      except Exception as e:
        rag_response = f"(Ollama failed) {e}"

        # --- Ollama Mermaid flowchart ---
      try:
          flow_prompt = f"""
      Create a Mermaid flowchart for a student's course progression.

      Rules:
      - Output ONLY Mermaid code
      - Start with: flowchart TD
      - Use underscores instead of spaces in node IDs
      - Show prerequisite arrows
      - Completed courses first

      Completed courses: {completed}
      Eligible courses: {eligible[:15]}

      Return ONLY Mermaid code.
      """
          llm_flowchart = {
              "mermaid": ollama_generate(flow_prompt, temperature=0.1),
              "explanation": "LLM-generated course flowchart",
          }
      except Exception as e:
          llm_flowchart = {
              "mermaid": "",
              "explanation": f"Flowchart generation failed: {e}",
          }

    # Why-not explanation (optional)
    if why_not_query:
        why_not_answer = why_not_query  # placeholder for later expansion

    return jsonify(
        {
            "dept": dept,
            "completed": completed,
            "eligible": eligible,
            "graph": graph,
            "rag_response": rag_response,
            "semantic_results": semantic_results,
            "search_results": search_results,
            "why_not_answer": why_not_answer,
            "llm_flowchart": llm_flowchart,
        }
    )

def generate_mermaid_flowchart(dept, completed, eligible):
    """
    Build a Mermaid flowchart from completed courses and eligible Course objects.
    Works with both Course objects and dicts.
    """
    lines = ["flowchart TD"]

    # Completed courses (strings)
    if completed:
        lines.append("Completed[Completed Courses]")
        for c in completed:
            cid = c.replace(" ", "_")
            lines.append(f"Completed --> {cid}[{c}]")

    # Eligible courses (Course objects OR dicts)
    if eligible:
        lines.append("Eligible[Eligible Courses]")

        for course in eligible[:8]:  # keep chart readable
            # --- Handle Course object ---
            if hasattr(course, "code"):
                code = course.code
                label = f"{course.code} – {course.name}"

            # --- Handle dict fallback ---
            elif isinstance(course, dict):
                code = course.get("id", "UNKNOWN")
                label = course.get("label", code).split("\n")[0]

            else:
                continue

            cid = code.replace(" ", "_")
            lines.append(f"Eligible --> {cid}[{label}]")

    if completed and eligible:
        lines.append("Completed --> Eligible")

    return {
        "explanation": (
            "This flowchart shows your completed courses and the next "
            "eligible courses based on prerequisites."
        ),
        "mermaid": "\n".join(lines),
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)