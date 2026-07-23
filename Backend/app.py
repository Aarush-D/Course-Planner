from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

from Courseplanner import build_progression_graph
import planner_engine as engine
import transfer_credit as tc

# Optional RAG retrieval (advising notes fed to the LLM explanation only).
try:
    from rag_retrieve import load_index, top_k_chunks, format_context
except Exception:  # pragma: no cover - missing optional module
    load_index = top_k_chunks = format_context = None

# ----------------------------
# Config (environment-driven)
# ----------------------------

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3:latest")
OLLAMA_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT_S", "25"))
USE_OLLAMA = os.getenv("USE_OLLAMA", "1") not in ("0", "false", "no")
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0") in ("1", "true", "yes")
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:4200"
    ).split(",")
    if o.strip()
]
RAG_INDEX_PATH = os.getenv(
    "RAG_INDEX_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "rag_data", "rag_index.json"),
)

_MAJOR_ALIASES = {
    "COMPUTER SCIENCE": "CMPSC",
    "CS": "CMPSC",
    "CMPSC": "CMPSC",
    "COMPUTER ENGINEERING": "CMPEN",
    "CMPEN": "CMPEN",
    "MATHEMATICS": "MATH",
    "MATH": "MATH",
    "STATISTICS": "STAT",
    "STAT": "STAT",
    "PREMEDICINE": "PREMED",
    "PRE-MEDICINE": "PREMED",
    "PRE MEDICINE": "PREMED",
    "PREMED": "PREMED",
    "PRE-MED": "PREMED",
    "PRE MED": "PREMED",
    "NURSING": "NURS",
    "NURS": "NURS",
    "ENGLISH": "ENGL",
    "BUSINESS": "BUSINESS",
    "CYBERSECURITY": "CYBER",
    "CYBERSECURITY ANALYTICS": "CYBER",
    "CYBER": "CYBER",
    "INFORMATION SCIENCES AND TECHNOLOGY": "CYBER",
    "IST": "CYBER",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # requests are small JSON payloads
CORS(app, origins=CORS_ORIGINS)

_RAG_INDEX = None


@app.errorhandler(Exception)
def handle_error(e):  # always return JSON, never a stack-trace page
    code = getattr(e, "code", 500)
    message = str(e) if FLASK_DEBUG else "Internal server error."
    if code != 500:
        message = getattr(e, "description", message)
    return jsonify({"error": message}), code


@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@app.get("/api/degree-plans")
def api_degree_plans():
    return jsonify({"plans": engine.list_degree_plans()})


@app.post("/api/transfer-credit")
def api_transfer_credit():
    """PA community colleges near the student, ranked by how many of their
    requested PSU courses transfer there (then by distance). The equivalency
    data itself isn't scraped yet (LionPATH's Transfer Credit Tool has no
    public API — see docs/EXPANSION_PLAN.md §5), so courses_covered_count is
    currently always 0 and every response includes a note saying so; the
    distance ranking itself is real and already useful on its own.
    """
    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    zip_code = str(payload.get("zip_code") or "").strip()
    if not zip_code:
        return jsonify({"error": "'zip_code' is required."}), 400

    courses_in = payload.get("courses") or []
    if not isinstance(courses_in, list):
        return jsonify({"error": "'courses' must be a list of course codes."}), 400
    courses = [engine.norm_code(str(c)) for c in courses_in if str(c).strip()]

    if not tc.zip_to_coords(zip_code):
        return jsonify({
            "error": f"'{zip_code}' isn't in the supported area yet — Pennsylvania zip "
                     "codes only for now. Nationwide coverage is planned.",
        }), 400

    cache = tc.load_equivalency_cache()
    ranked = tc.rank_colleges_for_courses(zip_code, courses, cache=cache) if courses \
        else tc.nearest_colleges(zip_code)

    return jsonify({
        "zipCode": zip_code,
        "courses": courses,
        "colleges": ranked,
        "equivalencyDataAvailable": bool(cache),
        "note": None if cache else (
            "Transfer-equivalency data hasn't been collected yet — showing distance only. "
            "Once available, colleges will be ranked by how many of your courses transfer there."
        ),
    })


# ----------------------------
# Ollama (optional, explanation only)
# ----------------------------

def ollama_chat(prompt: str, model: str = OLLAMA_MODEL, timeout_s: int = OLLAMA_TIMEOUT_S) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        return ""
    base = OLLAMA_HOST.rstrip("/")
    body = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 350},
    }
    system = (
        "You are a friendly Penn State academic advisor. "
        "You are given verified facts computed by a planning engine. "
        "Answer the student's question using ONLY those facts. "
        "Never invent courses or change the recommended list. Be concise."
    )
    # Preferred: /api/chat
    try:
        data = requests.post(
            f"{base}/api/chat",
            json={
                **body,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=timeout_s,
        ).json()
        text = (data.get("message") or {}).get("content", "") or ""
        if text:
            return text
    except Exception:
        pass
    # Fallback: /api/generate
    data = requests.post(
        f"{base}/api/generate",
        json={**body, "prompt": f"{system}\n\n{prompt}"},
        timeout=timeout_s,
    ).json()
    return data.get("response", "") or ""


# ----------------------------
# RAG helpers
# ----------------------------

def get_rag_index():
    global _RAG_INDEX
    if _RAG_INDEX is None and load_index and os.path.exists(RAG_INDEX_PATH):
        try:
            _RAG_INDEX = load_index(RAG_INDEX_PATH)
        except Exception:
            _RAG_INDEX = None
    return _RAG_INDEX


def retrieve_rag_context(prompt: str, dept: str, k: int = 4) -> str:
    if not prompt or not top_k_chunks or not format_context:
        return ""
    idx = get_rag_index()
    if idx is None:
        return ""
    try:
        hits = top_k_chunks(idx, query=prompt, k=k, dept=dept)
        return format_context(hits)
    except Exception:
        return ""


# ----------------------------
# Prompt parsing
# ----------------------------

def _extract_major_from_prompt(prompt: str) -> Optional[str]:
    """Find the major alias that appears earliest in the message.

    Course codes ("MATH 140", "STAT 200"...) can collide with short major
    aliases ("MATH", "STAT"); picking dict-iteration order instead of text
    position let a course mention anywhere in the message override an
    explicit "I am a premed student" earlier in the same sentence. Leftmost
    match wins instead, matching how a reader would parse the sentence.
    """
    raw = (prompt or "").upper()
    best: Optional[Tuple[int, str]] = None
    for alias, dept in _MAJOR_ALIASES.items():
        m = re.search(rf"\b{re.escape(alias)}\b", raw)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), dept)
    return best[1] if best else None


_TAKEN_TRIGGERS = [
    "i took", "i've taken", "i have taken", "i completed", "completed",
    "passed", "finished", "already took", "already taken", "i have credit",
    "transfer credit", "ap credit", "got credit",
]

_REMOVAL_TRIGGERS = [
    "did not take", "didn't take", "have not taken", "haven't taken",
    "have not completed", "haven't completed", "did not complete",
    "didn't complete", "not completed", "remove", "dropped", "i drop",
    "never took",
]


def _split_clauses(prompt: str) -> List[str]:
    return [c.strip() for c in re.split(r"[.;!?\n]|,?\s+but\s+", prompt or "") if c.strip()]


_START_VERB_RE = re.compile(r"\b(started|start|began|begin|enrolled|enroll)\b", re.IGNORECASE)
_COLLEGE_WORD_RE = re.compile(r"\b(college|school|university|psu|penn\s*state)\b", re.IGNORECASE)
_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _extract_start_year_from_prompt(prompt: str) -> Optional[int]:
    """Detect a stated start year like "I started school in 2022" or
    "I began at Penn State in 2023" — lets a mid-chat correction override
    the dropdown's start year even if the student never touched it.

    Requires a start-verb, a college-word, and a year all in the SAME
    clause so casual course-taking language ("I started CMPSC 131 in
    Fall 2022") doesn't get misread as a college start date.
    """
    for clause in _split_clauses(prompt):
        if not (_START_VERB_RE.search(clause) and _COLLEGE_WORD_RE.search(clause)):
            continue
        m = _FOUR_DIGIT_YEAR_RE.search(clause)
        if m:
            year = int(m.group(1))
            if 2015 <= year <= 2035:
                return year
    return None


def parse_summer_unavailable(prompt: str, catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Courses the student says aren't offered in summer.

    Matches clauses like "CMPSC 360 is not available over the summer" /
    "no summer section for MATH 230" / "can't take STAT 318 in summer".
    """
    found: List[Dict[str, Any]] = []
    seen = set()
    for clause in _split_clauses(prompt):
        low = clause.lower()
        if "summer" not in low:
            continue
        if not re.search(r"\bnot\b|n't\b|\bno\b|\bunavailable\b|\bcannot\b", low):
            continue
        matched, _ = engine.match_courses_in_text(clause, catalog)
        for m in matched:
            if m["code"] not in seen:
                seen.add(m["code"])
                found.append(m)
    return found


def parse_completion_changes(
    prompt: str, catalog: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Per-clause parsing: returns (added, removed, unmatched mentions).

    A clause with removal wording removes its courses; a clause with
    completion wording adds its courses. Removal wins within one clause
    ("I did not take X" contains 'take' but must not add X).
    """
    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    seen_add, seen_rm = set(), set()

    for clause in _split_clauses(prompt):
        low = clause.lower()
        is_removal = any(t in low for t in _REMOVAL_TRIGGERS)
        is_taken = any(t in low for t in _TAKEN_TRIGGERS)
        if not (is_removal or is_taken):
            continue
        matched, unm = engine.match_courses_in_text(clause, catalog)
        unmatched.extend(u for u in unm if u not in unmatched)
        for m in matched:
            if is_removal:
                if m["code"] not in seen_rm:
                    seen_rm.add(m["code"])
                    removed.append(m)
            else:
                if m["code"] not in seen_add:
                    seen_add.add(m["code"])
                    added.append(m)

    return added, removed, unmatched


# ----------------------------
# Serialization
# ----------------------------

def _course_card(code: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
    course = catalog.get(engine.norm_code(code))
    prereqs: List[str] = []
    if course:
        for group in course.prereq_groups:
            prereqs.append(" or ".join(sorted(group)))
    return {
        "id": engine.norm_code(code),
        "name": course.name if course else code,
        "description": (course.description or "") if course else "",
        "prerequisites": prereqs,
    }


def _pick_card(pick: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    if pick.get("code"):
        card = _course_card(pick["code"], catalog)
    else:
        card = {"id": "", "name": pick["name"], "description": pick["reason"], "prerequisites": []}
    card["credits"] = pick.get("credits")
    card["reason"] = pick.get("reason", "")
    card["flowchartSemester"] = pick.get("flowchart_semester")
    card["type"] = pick.get("type", "course")
    card["etm"] = pick.get("etm", False)
    card["unlocks"] = pick.get("unlocks", 0)
    return card


# ----------------------------
# Deterministic advisor reply
# ----------------------------

def _build_reply_text(
    major: str,
    catalog_year: Any,
    added: List[Dict[str, Any]],
    removed: List[Dict[str, Any]],
    unmatched: List[str],
    progress: Dict[str, Any],
    next_sem: Dict[str, Any],
    ranked: List[Dict[str, Any]],
    plan_warnings: List[str],
    summer_flagged: Optional[List[Dict[str, Any]]] = None,
    goal: Optional[Dict[str, Any]] = None,
    next_term_label: str = "",
    chat_start_year: Optional[int] = None,
) -> str:
    lines: List[str] = []

    if chat_start_year:
        lines.append(
            f"Got it — switched to the {chat_start_year} requirements "
            f"(the catalog year you started college)."
        )
    if added:
        lines.append("Recorded as completed:")
        for m in added:
            lines.append(f"  • {m['code']} — {m['name']}")
    if removed:
        lines.append("Removed from completed:")
        for m in removed:
            lines.append(f"  • {m['code']} — {m['name']}")
    if summer_flagged:
        lines.append("Noted as NOT available in summer (plan adjusted):")
        for m in summer_flagged:
            lines.append(f"  • {m['code']} — {m['name']}")
    if unmatched:
        lines.append(f"Couldn't match: {', '.join(unmatched[:6])} (check the course code).")

    if goal:
        status = "on track" if goal.get("met") else "NOT currently on track"
        lines.append(
            f"Graduation goal: {goal['grad_years']} years (by {goal['deadline']}) — {status}."
        )

    lines.append(
        f"Progress on the {major} {catalog_year} plan: "
        f"{progress['done_items']}/{progress['total_items']} requirements "
        f"({progress['credits_done']}/{progress['total_credits']} credits)."
    )
    if progress.get("extra_courses"):
        lines.append(
            "Completed courses not on the plan (may count as electives): "
            + ", ".join(progress["extra_courses"][:8])
        )

    if next_sem["courses"]:
        lines.append("")
        term_name = next_term_label or "next semester"
        lines.append(f"Recommended for {term_name} ({next_sem['total_credits']} credits):")
        for p in next_sem["courses"]:
            label = p["code"] or p["name"]
            lines.append(f"  • {label} ({p['credits']:g} cr) — {p['reason']}")
    else:
        lines.append("All flowchart requirements are satisfied — you're set to graduate! 🎓")

    if ranked:
        lines.append("")
        lines.append("Top ranked eligible courses (weighted):")
        for r in ranked[:5]:
            lines.append(f"  • {r['code']} (score {r['score']}, {r['source']}) — {r['reasons'][0]}")

    if next_sem.get("blocked"):
        lines.append("")
        lines.append("Still locked (missing prerequisites):")
        for b in next_sem["blocked"][:3]:
            lines.append(f"  • {b['code']} needs: {'; '.join(b['missing'])}")

    for w in plan_warnings:
        lines.append(f"⚠ {w}")

    return "\n".join(lines)


def _llm_phrase_reply(question: str, facts: str, rag_context: str) -> Optional[str]:
    if not USE_OLLAMA or not question.strip():
        return None
    try:
        context_block = f"\nAdvising notes (background):\n{rag_context}\n" if rag_context else ""
        prompt = (
            "Verified planning facts:\n"
            f"{facts}\n"
            f"{context_block}\n"
            f"Student question: {question}\n\n"
            "Write a short, friendly advisor reply (max ~180 words) grounded ONLY in the facts above. "
            "Keep every course code exactly as written. Do not add or remove recommendations."
        )
        text = ollama_chat(prompt)
        return text.strip() or None
    except Exception:
        return None


# ----------------------------
# Main API
# ----------------------------

@app.post("/api/plan")
def api_plan():
    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    prompt = str(payload.get("prompt") or "").strip()[:4000]
    payload_major = str(payload.get("major") or payload.get("dept") or "").strip().upper()
    catalog_year = payload.get("catalog_year")
    completed_in = payload.get("completed") or []
    if not isinstance(completed_in, list):
        return jsonify({"error": "'completed' must be a list of course codes."}), 400
    max_credits = payload.get("max_credits")
    if max_credits is not None and not isinstance(max_credits, (int, float)):
        return jsonify({"error": "'max_credits' must be a number."}), 400

    # Year planning inputs
    try:
        start_year = int(payload.get("start_year") or 0) or None
        grad_years = int(payload.get("grad_years") or 4)
    except (TypeError, ValueError):
        return jsonify({"error": "'start_year' and 'grad_years' must be numbers."}), 400
    grad_years = min(max(grad_years, 1), 8)
    allow_summer = bool(payload.get("allow_summer"))
    summer_unavailable_in = payload.get("summer_unavailable") or []
    if not isinstance(summer_unavailable_in, list):
        return jsonify({"error": "'summer_unavailable' must be a list."}), 400

    # The chat message is the source of truth for the major when it names one.
    major = _extract_major_from_prompt(prompt) or payload_major or "CMPSC"

    # A stated start year ("oh, I started school in 2022") wins outright —
    # even over an already-synced dropdown value — since the student is
    # actively correcting it.
    chat_start_year = _extract_start_year_from_prompt(prompt)
    if chat_start_year:
        start_year = chat_start_year
        catalog_year = chat_start_year

    # Requirements follow the catalog year the student STARTED college.
    plan = engine.load_degree_plan(major, catalog_year or start_year)
    if plan is None:
        available = engine.list_degree_plans()
        fallback = available[0] if available else None
        if fallback:
            plan = engine.load_degree_plan(fallback["major"], fallback["catalog_year"])
        if plan is None:
            return jsonify({"error": f"No degree plan available for {major}."}), 404

    catalog = engine.load_merged_catalog(plan.get("departments", [major]))

    # --- interpret the chat message (add AND remove, summer availability) ---
    added, removed, unmatched = parse_completion_changes(prompt, catalog)
    summer_flagged = parse_summer_unavailable(prompt, catalog)

    completed = {engine.norm_code(c) for c in completed_in if str(c).strip()}
    completed |= {m["code"] for m in added}
    completed -= {m["code"] for m in removed}
    completed_sorted = sorted(completed)

    summer_unavailable = {engine.norm_code(c) for c in summer_unavailable_in if str(c).strip()}
    summer_unavailable |= {m["code"] for m in summer_flagged}
    summer_unavailable_sorted = sorted(summer_unavailable)

    # --- deterministic planning ---
    full_plan = engine.build_full_plan(
        plan, catalog, completed,
        start_year=start_year,
        grad_years=grad_years,
        allow_summer=allow_summer,
        summer_unavailable=summer_unavailable,
    )
    # The next term to plan is the first simulated term (summer-aware).
    first_term = full_plan["terms"][0] if full_plan["terms"] else None
    next_sem = engine.recommend_semester(
        plan, catalog, completed,
        max_credits=max_credits or (engine.SUMMER_MAX_CREDITS if first_term and first_term["is_summer"] else None),
        exclude_codes=summer_unavailable if first_term and first_term["is_summer"] else None,
    )
    if first_term:
        next_sem["courses"] = first_term["courses"]
        next_sem["total_credits"] = first_term["total_credits"]
    progress = next_sem["progress"]
    mermaid = engine.build_mermaid(plan, catalog, completed, next_sem["courses"])
    unlock_map = engine.build_unlock_map(plan, catalog, completed)
    semester_flowchart = engine.build_semester_flowchart(catalog, completed, full_plan["terms"])

    # --- weighted ranking of all eligible courses ---
    interests = engine.extract_interests(prompt)
    ranked = engine.score_recommendations(plan, catalog, completed, interests=interests)
    tips = engine.default_tips(progress, next_sem["blocked"])

    # --- prereq graph (vis-network compatibility) ---
    try:
        graph_nodes, graph_edges, _ = build_progression_graph(catalog, completed, max_depth=2)
    except Exception:
        graph_nodes, graph_edges = [], []
    graph = {"nodes": graph_nodes, "edges": graph_edges}

    # --- reply text: deterministic facts, optionally rephrased by Ollama + RAG notes ---
    facts = _build_reply_text(
        plan.get("major", major), plan.get("catalog_year", ""),
        added, removed, unmatched,
        progress, next_sem, ranked, full_plan["warnings"],
        summer_flagged=summer_flagged,
        goal=full_plan.get("goal"),
        next_term_label=first_term["label"] if first_term else "",
        chat_start_year=chat_start_year,
    )
    rag_response = facts
    if prompt:
        rag_context = retrieve_rag_context(prompt, dept=plan.get("major", major))
        phrased = _llm_phrase_reply(prompt, facts, rag_context)
        if phrased:
            rag_response = phrased

    # --- serialize ---
    recommendations = [
        {
            "name": r["code"],
            "reason": " ".join(r["reasons"]),
            "credits": r["credits"],
            "score": r["score"],
            "source": r["source"],
            "title": r["name"],
            "flowchartSemester": r["flowchart_semester"],
        }
        for r in ranked
    ]

    flowchart_cards = [_course_card(c, catalog) for c in completed_sorted]
    flowchart_cards += [_pick_card(p, catalog) for p in next_sem["courses"]]

    full_plan_out = {
        "terms": [
            {
                "index": t["index"],
                "label": t["label"],
                "isSummer": t["is_summer"],
                "withinGoal": t["within_goal"],
                "totalCredits": t["total_credits"],
                "courses": [_pick_card(p, catalog) for p in t["courses"]],
            }
            for t in full_plan["terms"]
        ],
        "warnings": full_plan["warnings"],
        "goal": {
            "startYear": full_plan["goal"]["start_year"],
            "gradYears": full_plan["goal"]["grad_years"],
            "deadline": full_plan["goal"]["deadline"],
            "allowSummer": full_plan["goal"]["allow_summer"],
            "met": full_plan["goal"]["met"],
        },
    }

    eligible_codes = [p["code"] for p in next_sem["courses"] if p["code"]]
    matched_payload = {
        "courses": added,
        "removed": removed,
        "summerUnavailable": summer_flagged,
        "unmatched": unmatched,
        "treatedAsCompleted": bool(added),
    }
    state = {
        "dept": plan.get("major", major),
        "completed": completed_sorted,
        "startYear": full_plan["goal"]["start_year"],
        "gradYears": grad_years,
        "allowSummer": allow_summer,
        "summerUnavailable": summer_unavailable_sorted,
    }

    course_plan = {
        "major": plan.get("major", major),
        "catalogYear": plan.get("catalog_year"),
        "dept": plan.get("major", major),
        "completed": completed_sorted,
        "eligible": eligible_codes,
        "graph": graph,
        "rag_response": rag_response,
        "flowchart": flowchart_cards,
        "recommendations": recommendations,
        "tips": tips,
        "llm_flowchart": mermaid,
        "unlockMap": unlock_map,
        "semesterFlowchart": semester_flowchart,
        "matched": matched_payload,
        "nextSemester": {
            "label": first_term["label"] if first_term else "",
            "isSummer": bool(first_term and first_term["is_summer"]),
            "totalCredits": next_sem["total_credits"],
            "courses": [_pick_card(p, catalog) for p in next_sem["courses"]],
            "blocked": next_sem["blocked"],
        },
        "fullPlan": full_plan_out,
        "progress": {
            "doneItems": progress["done_items"],
            "totalItems": progress["total_items"],
            "creditsDone": progress["credits_done"],
            "totalCredits": progress["total_credits"],
            "extraCourses": progress["extra_courses"],
        },
    }

    course_plan["state"] = state

    return jsonify({
        # legacy top-level keys (state is the client's source of truth)
        "state": state,
        "dept": course_plan["dept"],
        "completed": completed_sorted,
        "eligible": eligible_codes,
        "graph": graph,
        "rag_response": rag_response,
        "semantic_results": [],
        "search_results": [],
        "why_not_answer": "",
        "llm_flowchart": mermaid,
        # structured payload used by the frontend
        "coursePlan": course_plan,
    })


if __name__ == "__main__":
    # 5001 by default: macOS AirPlay Receiver squats on port 5000.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5001")), debug=FLASK_DEBUG)
