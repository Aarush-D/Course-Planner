from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

from Courseplanner import (
    build_progression_graph,
    find_course,
    get_dept_catalog,
    semantic_search_courses,
)

# Optional flowchart-based "foundation plan" (PDF parsing)
# NOTE: This module is optional; the API still works without it.
try:
    from flowcharts import get_foundation_plan_for_major, format_foundation_plan
except Exception:
    get_foundation_plan_for_major = None
    format_foundation_plan = None


# ----------------------------
# Ollama helpers (HTTP)
# ----------------------------

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")


class OllamaError(RuntimeError):
    pass


def _post_json(url: str, payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
    try:
        r = requests.post(url, json=payload, timeout=timeout_s)
    except requests.RequestException as e:
        raise OllamaError(str(e)) from e

    if r.status_code == 404:
        # Caller may want to fall back to a different endpoint.
        raise OllamaError(f"404 Not Found for url: {url}")

    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise OllamaError(f"{r.status_code} {r.text}") from e

    try:
        return r.json()
    except Exception as e:
        raise OllamaError(f"Non-JSON response from Ollama: {r.text[:200]}") from e


def ollama_chat(prompt: str, model: str = OLLAMA_MODEL, timeout_s: int = 30) -> str:
    """Use Ollama /api/chat if available, otherwise fall back to /api/generate."""
    prompt = (prompt or "").strip()
    if not prompt:
        return ""

    chat_url = f"{OLLAMA_HOST.rstrip('/')}/api/chat"
    gen_url = f"{OLLAMA_HOST.rstrip('/')}/api/generate"

    # 1) Preferred: /api/chat
    try:
        data = _post_json(
            chat_url,
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful Penn State academic advisor."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            timeout_s=timeout_s,
        )
        return (data.get("message") or {}).get("content", "") or ""
    except OllamaError as e:
        # If chat endpoint doesn't exist, fall back.
        if "404" not in str(e):
            raise

    # 2) Fallback: /api/generate
    data = _post_json(
        gen_url,
        {"model": model, "prompt": prompt, "stream": False},
        timeout_s=timeout_s,
    )
    return data.get("response", "") or ""


# ----------------------------
# Serialization helpers
# ----------------------------

def _course_to_frontend_course(course: Any) -> Dict[str, Any]:
    """Return the Course interface expected by the frontend."""
    if hasattr(course, "code"):
        # Courseplanner.Course dataclass/object
        prereqs: List[str] = []
        for group in getattr(course, "prereq_groups", []) or []:
            # Each group is like {"CMPSC 121", "CMPEN 270"} meaning OR group.
            try:
                prereqs.extend(sorted(list(group)))
            except Exception:
                continue
        return {
            "id": str(getattr(course, "code", "")),
            "name": str(getattr(course, "name", getattr(course, "code", "Course"))),
            "description": str(getattr(course, "description", "")),
            "prerequisites": prereqs,
        }

    if isinstance(course, dict):
        # Handle graph nodes or dict results
        code = course.get("id") or course.get("code") or "UNKNOWN"
        label = course.get("label") or code
        title = label.split("\\n")[0] if isinstance(label, str) else str(code)
        desc = course.get("description") or ""
        prereqs = course.get("prerequisites") or []
        return {
            "id": str(code),
            "name": str(title),
            "description": str(desc),
            "prerequisites": [str(x) for x in prereqs],
        }

    return {"id": str(course), "name": str(course), "description": "", "prerequisites": []}


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


def _safe_json_from_llm(text: str) -> Optional[dict]:
    """Extract first JSON object from a model response."""
    if not text:
        return None
    # Try direct parse first.
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try extracting a JSON object within fences or surrounding text.
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ----------------------------
# LLM generation
# ----------------------------

def generate_recommendations(
    dept: str,
    completed: List[str],
    eligible_codes: List[str],
    question: str,
    model: str = OLLAMA_MODEL,
    timeout_s: int = 35,
) -> Tuple[List[Dict[str, str]], str]:
    """Return (recommendations_list, raw_text)."""
    prompt = f"""
You are a Penn State academic advisor.

Department: {dept}
Completed courses: {completed}
Eligible next courses: {eligible_codes[:25]}

Student question:
{question}

Return ONLY valid JSON in this exact shape:
{{
  "recommendations": [
    {{"name": "COURSE CODE", "reason": "1 short sentence"}},
    {{"name": "COURSE CODE", "reason": "1 short sentence"}},
    {{"name": "COURSE CODE", "reason": "1 short sentence"}},
    {{"name": "COURSE CODE", "reason": "1 short sentence"}},
    {{"name": "COURSE CODE", "reason": "1 short sentence"}}
  ],
  "tips": ["tip 1", "tip 2"]
}}
""".strip()

    raw = ollama_chat(prompt, model=model, timeout_s=timeout_s)
    obj = _safe_json_from_llm(raw)

    recs: List[Dict[str, str]] = []
    if isinstance(obj, dict):
        for item in obj.get("recommendations", []) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            reason = str(item.get("reason", "")).strip()
            if name:
                recs.append({"name": name, "reason": reason or ""})

        tips = obj.get("tips") or []
        if isinstance(tips, list) and tips:
            # Append tips as pseudo-items if you want; frontend shows tips separately.
            pass

    return recs, raw


def generate_mermaid_flowchart(
    dept: str,
    completed: List[str],
    eligible_codes: List[str],
    model: str = OLLAMA_MODEL,
    timeout_s: int = 35,
) -> Dict[str, str]:
    """Return {mermaid, explanation}. Always returns something usable."""

    # Deterministic fallback (always works)
    def fallback() -> Dict[str, str]:
        lines = ["flowchart TD"]
        if completed:
            lines.append("subgraph Completed")
            for c in completed[:12]:
                cid = re.sub(r"[^A-Za-z0-9_]", "_", c)
                lines.append(f"  {cid}[{c}]")
            lines.append("end")

        if eligible_codes:
            lines.append("subgraph Eligible")
            for c in eligible_codes[:12]:
                cid = re.sub(r"[^A-Za-z0-9_]", "_", c)
                lines.append(f"  {cid}[{c}]")
            lines.append("end")

        if completed and eligible_codes:
            # Light connector for visual flow
            c0 = re.sub(r"[^A-Za-z0-9_]", "_", completed[0])
            e0 = re.sub(r"[^A-Za-z0-9_]", "_", eligible_codes[0])
            lines.append(f"{c0} --> {e0}")

        return {
            "explanation": "Generated from your completed and eligible courses.",
            "mermaid": "\n".join(lines),
        }

    prompt = f"""
Create a Mermaid diagram for a student's course progression.

Rules:
- Output ONLY Mermaid code (no backticks, no commentary)
- Start with exactly: flowchart TD
- Use underscores instead of spaces in node IDs
- Keep it readable (max ~20 nodes)
- It's OK to show only a subset of prerequisite arrows

Department: {dept}
Completed: {completed}
Eligible next: {eligible_codes[:15]}

Mermaid only:
""".strip()

    try:
        mermaid = (ollama_chat(prompt, model=model, timeout_s=timeout_s) or "").strip()
        if not mermaid.lower().startswith("flowchart"):
            return fallback()
        return {
            "explanation": "LLM-generated course flowchart (Ollama).",
            "mermaid": mermaid,
        }
    except Exception:
        return fallback()


# ----------------------------
# Flask app
# ----------------------------

app = Flask(__name__)
CORS(app)


@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@app.post("/api/plan")
def api_plan():
    payload = request.get_json(force=True) or {}

    dept = (payload.get("dept") or "CMPSC").upper()
    completed = payload.get("completed") or []
    prompt = (payload.get("prompt") or "").strip()

    semantic_query = (payload.get("semantic_query") or "").strip()
    search_query = (payload.get("search_query") or "").strip()

    # --- Catalog ---
    catalog = get_dept_catalog(dept)

    # --- Progression graph / eligible list ---
    graph_nodes, graph_edges, eligible_courses = [], [], []
    try:
        graph_nodes, graph_edges, eligible_courses = build_progression_graph(
            catalog, completed, max_depth=2
        )
    except Exception as e:
        graph_nodes, graph_edges, eligible_courses = [], [], []

    graph = {"nodes": graph_nodes, "edges": graph_edges}

    eligible_codes: List[str] = []
    for c in eligible_courses:
        if hasattr(c, "code"):
            eligible_codes.append(c.code)
        elif isinstance(c, dict):
            eligible_codes.append(str(c.get("id") or c.get("code") or "UNKNOWN"))
        else:
            eligible_codes.append(str(c))

    # --- Searches (optional) ---
    semantic_results: List[Any] = []
    search_results: List[Any] = []

    if semantic_query:
        try:
            semantic_results = semantic_search_courses(dept=dept, query=semantic_query, top_k=5)
        except Exception as e:
            semantic_results = [{"error": str(e)}]

    if search_query:
        try:
            # find_course can return list; keep it small
            found = find_course(catalog, search_query)
            search_results = [
                _course_to_frontend_course(c) for c in (found[:10] if isinstance(found, list) else [found])
            ]
        except Exception as e:
            search_results = [{"error": str(e)}]

    # --- Foundation plan (PDF based) ---
    rag_response = ""
    if prompt and get_foundation_plan_for_major and format_foundation_plan:
        if (not completed) and _looks_like_foundation_question(prompt):
            try:
                plan = get_foundation_plan_for_major(dept, semesters=(1, 2))
                rag_response = format_foundation_plan(plan)
            except Exception as e:
                rag_response = f"(Flowchart plan failed) {e}"

    # --- LLM outputs ---
    recommendations: List[Dict[str, str]] = []

    # Mermaid flowchart shown in center
    llm_flowchart = generate_mermaid_flowchart(
        dept=dept,
        completed=completed,
        eligible_codes=eligible_codes,
    )

    if prompt and not rag_response:
        try:
            recs, raw = generate_recommendations(
                dept=dept,
                completed=completed,
                eligible_codes=eligible_codes,
                question=prompt,
            )
            recommendations = recs
            # Keep raw text for debugging/fallback display
            rag_response = raw
        except Exception as e:
            rag_response = f"(Ollama failed) {e}"

    # Build a list-based flowchart (used by the left-to-right list UI if desired)
    flowchart_list = [_course_to_frontend_course(c) for c in eligible_courses[:25]]

    response = {
        # Backward-compat keys
        "dept": dept,
        "completed": completed,
        "eligible": eligible_codes,
        "graph": graph,
        "rag_response": rag_response,
        "semantic_results": semantic_results,
        "search_results": search_results,
        "why_not_answer": "",
        "llm_flowchart": llm_flowchart,
        # New structured payload
        "coursePlan": {
            "flowchart": flowchart_list,
            "recommendations": recommendations,
            "llm_flowchart": llm_flowchart,
        },
    }

    return jsonify(response)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
