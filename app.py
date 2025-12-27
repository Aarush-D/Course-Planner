# app.py
import re
import os
import io
from typing import Dict
from flask import Flask, request, render_template_string, redirect, url_for, session, send_file

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

DEFAULT_SETTINGS = {
    "show_rag": True,
    "show_semantic": True,
    "show_eligible": True,
    "show_graph": True,
    "show_llm_flowchart": True,
}

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
  <script src="https://unpkg.com/vis-network@9.1.2/dist/vis-network.min.js"></script>
  <title>PSU Course Planner</title>
  <style>
    body { font-family: sans-serif; max-width: 1200px; margin: 2rem auto; }
    textarea { width: 100%; height: 80px; }
    .section { margin-top: 2rem; }
    h2 { border-bottom: 1px solid #ccc; padding-bottom: 0.3rem; }
    .level-header { margin-top: 1rem; font-weight: bold; }
    .course { margin-left: 1rem; margin-bottom: 0.4rem; }
    .sub { margin-left: 0; font-size: 0.95rem; color: #444; white-space: pre-wrap; }
    label { font-weight: 600; }
    .filters { margin-top: 0.5rem; margin-bottom: 1rem; }
    .filters span { margin-right: 1rem; }
    input[type="number"] { width: 80px; }
    input[type="text"] { width: 420px; }
    .warn { background: #fff3cd; padding: 0.75rem; border: 1px solid #ffeeba; border-radius: 8px; }
    .topbar { display:flex; justify-content: space-between; align-items:center; }
    .topbar a { margin-left: 1rem; }
    .pill { display:inline-block; padding: 2px 8px; border: 1px solid #ccc; border-radius: 999px; font-size: 12px; color: #444; margin-left: 8px; }
    .box { border: 1px solid #ddd; border-radius: 10px; padding: 12px; background: #fafafa; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }
    #graph { height: 560px; border:1px solid #ddd; border-radius: 10px; background: #fff; }
    .mermaid-wrap { border:1px solid #ddd; border-radius: 10px; background: #fff; padding: 10px; }
    .mermaid { overflow:auto; }
    @media (max-width: 1000px) {
      .grid { grid-template-columns: 1fr; }
      #graph { height: 480px; }
    }
  </style>
</head>
<body>
  <div class="topbar">
    <h1>PSU Course Planner</h1>
    <div>
      <a href="/settings">Settings</a>
      <a href="/clear_chat">Clear chat</a>
      <a href="/export_pdf">Export PDF</a>
    </div>
  </div>

  <form method="post">
    <label for="dept">Department:</label>
    <select name="dept" id="dept">
      <option value="CMPSC" {% if dept == "CMPSC" %}selected{% endif %}>CMPSC</option>
      <option value="CMPEN" {% if dept == "CMPEN" %}selected{% endif %}>CMPEN</option>
      <option value="MATH"  {% if dept == "MATH"  %}selected{% endif %}>MATH</option>
      <option value="STAT"  {% if dept == "STAT"  %}selected{% endif %}>STAT</option>
    </select>

    <div class="filters">
      <span>Show levels:</span>
      <label><input type="checkbox" name="lvl100" value="1" {% if lvl100 %}checked{% endif %}> 100</label>
      <label><input type="checkbox" name="lvl200" value="1" {% if lvl200 %}checked{% endif %}> 200</label>
      <label><input type="checkbox" name="lvl300" value="1" {% if lvl300 %}checked{% endif %}> 300</label>
      <label><input type="checkbox" name="lvl400" value="1" {% if lvl400 %}checked{% endif %}> 400+</label>

      <span style="margin-left: 2rem;">
        Max results per section:
        <input type="number" name="max_results" min="1"
               value="{{ max_results if max_results is not none else '' }}">
      </span>
    </div>

    {% if show_semantic %}
    <div class="filters">
      <span>Semantic search:</span>
      <input type="text" name="semantic_query" value="{{ semantic_query }}">
      {% if semantic_query %}<span class="pill">cosine similarity</span>{% endif %}
    </div>
    {% endif %}

    {% if show_rag %}
    <div class="filters">
      <span>Ask with RAG (completed + eligible):</span>
      <input type="text" name="rag_question" value="{{ rag_question }}">
      {% if rag_question %}<span class="pill">memory: on</span>{% endif %}
    </div>
    {% endif %}

    <div class="filters">
      <span>Search for a course (code or name):</span>
      <input type="text" name="search_query" value="{{ search_query }}">
      <span style="font-size: 0.9rem; color: #666;">
        e.g. "CMPSC 131", "313", "Assembly", "AI"
      </span>
    </div>

    <div class="filters">
      <span>Why can't I take (code)?</span>
      <input type="text" name="why_not_query" value="{{ why_not_query }}">
      <span style="font-size: 0.9rem; color: #666;">
        e.g. "CMPSC 473"
      </span>
    </div>

    <label>Completed courses (comma-separated):</label>
    <textarea name="completed">{{ completed_text }}</textarea><br>
    <button type="submit">Show</button>
  </form>

  {% if warning %}
    <div class="section warn">{{ warning }}</div>
  {% endif %}

  {% if completed %}
  <div class="section">
    <h2>Completed Courses</h2>
    <div class="box">{{ ", ".join(completed) }}</div>
  </div>
  {% endif %}

  {% if show_graph and (graph_nodes or graph_edges or (show_llm_flowchart and llm_mermaid)) %}
  <div class="section">
    <h2>Flowcharts</h2>
    <div class="sub" style="margin-bottom:10px;">
      Left: actual prereq/concurrent graph. Right: LLM-generated "recommended path" (Mermaid) + explanation.
    </div>

    <div class="grid">
      <div>
        <div id="graph"></div>
      </div>

      <div class="mermaid-wrap">
        {% if show_llm_flowchart and llm_explanation %}
          <div class="box sub" style="margin-bottom:10px;">{{ llm_explanation }}</div>
        {% endif %}

        {% if show_llm_flowchart and llm_mermaid %}
          <div class="mermaid">{{ llm_mermaid|e }}</div>
        {% else %}
          <div class="sub">No LLM flowchart yet. Ask a recommendation question to generate one.</div>
        {% endif %}
      </div>
    </div>

    <<script>
  const container = document.getElementById('graph');
  const nodes = new vis.DataSet({{ graph_nodes|tojson }});
  const edges = new vis.DataSet({{ graph_edges|tojson }});

  // 1) Color nodes BEFORE creating the network (prevents re-layout jolts)
  nodes.forEach(n => {
    if (n.status === "completed") nodes.update({ id: n.id, color: { background: "#d4edda", border:"#7bc47f" }});
    else if (n.status === "eligible") nodes.update({ id: n.id, color: { background: "#d1ecf1", border:"#6cb2c4" }});
    else nodes.update({ id: n.id, color: { background: "#f8d7da", border:"#d77b84" }});
  });

  const options = {
    layout: { improvedLayout: true },
    physics: {
      enabled: true,
      stabilization: {
        enabled: true,
        iterations: 500,
        updateInterval: 25
      },
      barnesHut: {
        gravitationalConstant: -750,
        centralGravity: 0.2,
        springLength: 240,
        springConstant: 0.04,
        damping: 0.25,
        avoidOverlap: 1.0
      }
    },
    nodes: {
      shape: "box",
      margin: 10,
      widthConstraint: { maximum: 260 }
    },
    edges: { smooth: { type: "dynamic" } },
    interaction: {
      hover: true,
      dragNodes: true,
      zoomView: true,
      dragView: true
    }
  };

  const network = new vis.Network(container, { nodes, edges }, options);

  // 2) When the layout finishes stabilizing, freeze it so it stops "jumping"
  network.once("stabilizationIterationsDone", function () {
    network.setOptions({ physics: false });
    // Optional: lock current positions so even small recalcs won't move nodes
    // network.storePositions();
  });
</script>
  </div>
  {% endif %}

  {% if why_not_answer %}
  <div class="section">
    <h2>Why not?</h2>
    <div class="box sub">{{ why_not_answer }}</div>
  </div>
  {% endif %}

  {% if search_results %}
  <div class="section">
    <h2>Search results for "{{ search_query }}" ({{ dept }})</h2>
    {% for course in search_results %}
      <div class="course">
        <strong>{{ course.code }}</strong>
        {% if course.credits is not none %}
          ({{ format_credits(course.credits) }})
        {% endif %}
        — {{ course.name }}
      </div>
      {% if course.description %}
        <div class="sub">{{ course.description }}</div>
      {% endif %}
      {% if course.prereq_groups %}
        <div class="sub">Prereqs: {{ format_groups(course.prereq_groups) }}</div>
      {% endif %}
      {% if course.concurrent_groups %}
        <div class="sub">Concurrent: {{ format_groups(course.concurrent_groups) }}</div>
      {% endif %}
      <br>
    {% endfor %}
  </div>
  {% endif %}

  {% if show_rag and rag_response %}
  <div class="section">
    <h2>Recommendations</h2>
    <div class="box sub">{{ rag_response }}</div>
  </div>
  {% endif %}

  {% if show_eligible and (no_concurrent or with_concurrent) %}
  <div class="section">
    <h2>Eligible {{ dept }} Courses (NO Concurrent Requirement)</h2>
    {% if not no_concurrent %}
      <p>None</p>
    {% else %}
      {% for lvl, courses in no_concurrent.items() %}
        <div class="level-header">{{ "Other-level" if lvl == 0 else (lvl|string + "-level") }}</div>
        {% for course in courses %}
          <div class="course">
            - {{ course.code }}{% if course.credits is not none %} ({{ format_credits(course.credits) }}){% endif %} — {{ course.name }}
          </div>
        {% endfor %}
      {% endfor %}
    {% endif %}
  </div>

  <div class="section">
    <h2>Eligible {{ dept }} Courses (WITH Enforced Concurrent at Enrollment)</h2>
    {% if not with_concurrent %}
      <p>None</p>
    {% else %}
      {% for lvl, courses in with_concurrent.items() %}
        <div class="level-header">{{ "Other-level" if lvl == 0 else (lvl|string + "-level") }}</div>
        {% for course in courses %}
          <div class="course">
            - {{ course.code }}{% if course.credits is not none %} ({{ format_credits(course.credits) }}){% endif %} — {{ course.name }}
          </div>
        {% endfor %}
      {% endfor %}
    {% endif %}
  </div>
  {% endif %}

  {% if show_semantic and semantic_results %}
  <div class="section">
    <h2>Semantic results ({{ dept }})</h2>
    <div class="sub" style="margin-bottom:10px;">
      Score is cosine similarity: 1.0 very similar, 0 unrelated.
    </div>
    {% for r in semantic_results %}
      <div class="course">
        <strong>{{ r["code"] }}</strong> — {{ r["name"] }}
        <span style="color:#666;">(score {{ "%.3f"|format(r["score"]) }})</span>
      </div>
    {% endfor %}
  </div>
  {% endif %}

  <!-- Mermaid init ONCE -->
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
    mermaid.initialize({
      startOnLoad: true,
      securityLevel: "loose"
    });
  </script>
</body>
</html>
"""

SETTINGS_TEMPLATE = """
<!doctype html>
<html>
<head><title>Settings</title></head>
<body style="font-family:sans-serif; max-width:900px; margin:2rem auto;">
  <h1>Settings</h1>
  <form method="post">
    <label><input type="checkbox" name="show_rag" value="1" {% if show_rag %}checked{% endif %}> Show RAG</label><br>
    <label><input type="checkbox" name="show_semantic" value="1" {% if show_semantic %}checked{% endif %}> Show Semantic Search</label><br>
    <label><input type="checkbox" name="show_eligible" value="1" {% if show_eligible %}checked{% endif %}> Show Eligible Courses</label><br>
    <label><input type="checkbox" name="show_graph" value="1" {% if show_graph %}checked{% endif %}> Show Prereq Graph</label><br>
    <label><input type="checkbox" name="show_llm_flowchart" value="1" {% if show_llm_flowchart %}checked{% endif %}> Show LLM Flowchart</label><br><br>
    <button type="submit">Save</button>
  </form>
  <p><a href="{{ url_for('index') }}">Back to Planner</a></p>
</body>
</html>
"""


def sanitize_mermaid(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    # remove accidental fences
    s = re.sub(r"^```(?:mermaid)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    if not re.search(r"^(flowchart|graph)\s+", s, flags=re.IGNORECASE | re.MULTILINE):
        s = "flowchart TD\n" + s
    return s.strip()


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


def is_100_level(code: str) -> bool:
    m = re.search(r"\b(\d{3})[A-Z]?\b", code)
    return bool(m) and 100 <= int(m.group(1)) < 200


def filter_by_levels(courses, lvl100: bool, lvl200: bool, lvl300: bool, lvl400: bool):
    allowed = set()
    if lvl100: allowed.add(100)
    if lvl200: allowed.add(200)
    if lvl300: allowed.add(300)
    if lvl400: allowed.add(400)
    if not allowed:
        return []
    out = []
    for c in courses:
        lvl = course_level(c.code)
        if lvl is None:
            continue
        if lvl >= 400 and 400 in allowed:
            out.append(c)
        elif lvl in allowed:
            out.append(c)
    return out


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


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        current = get_settings()
        current.update({
            "show_rag": bool(request.form.get("show_rag")),
            "show_semantic": bool(request.form.get("show_semantic")),
            "show_eligible": bool(request.form.get("show_eligible")),
            "show_graph": bool(request.form.get("show_graph")),
            "show_llm_flowchart": bool(request.form.get("show_llm_flowchart")),
        })
        session["settings"] = current
        return redirect(url_for("index"))

    s = get_settings()
    return render_template_string(
        SETTINGS_TEMPLATE,
        show_rag=s["show_rag"],
        show_semantic=s["show_semantic"],
        show_eligible=s["show_eligible"],
        show_graph=s["show_graph"],
        show_llm_flowchart=s["show_llm_flowchart"],
    )


@app.route("/clear_chat")
def clear_chat():
    dept = session.get("last_dept", "CMPSC")
    if "chat_history_by_dept" in session:
        session["chat_history_by_dept"].pop(dept, None)
    session.pop("last_llm_flowchart", None)
    return redirect(url_for("index"))


@app.route("/export_pdf")
def export_pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    state = session.get("last_view_state", {})
    dept = state.get("dept", "CMPSC")
    completed = state.get("completed", [])
    rag_answer = state.get("rag_response", "")
    semantic_results = state.get("semantic_results", [])
    eligible_no_conc = state.get("eligible_no_conc", [])
    eligible_with_conc = state.get("eligible_with_conc", [])

    llm_fc = session.get("last_llm_flowchart", {})
    llm_expl = (llm_fc.get("explanation") or "")
    llm_mermaid_raw = (llm_fc.get("mermaid") or "")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    def draw_lines(lines, start_y, left=50, line_height=14):
        y = start_y
        for line in lines:
            if y < 50:
                c.showPage()
                y = height - 50
            c.drawString(left, y, str(line)[:120])
            y -= line_height
        return y

    y = height - 50
    y = draw_lines(["PSU Course Planner Export"], y, line_height=18)
    y -= 10
    y = draw_lines([f"Department: {dept}"], y)
    y = draw_lines([f"Completed: {', '.join(completed) if completed else 'None'}"], y)
    y -= 10

    if rag_answer:
        y = draw_lines(["Recommendations:"], y, line_height=16)
        y = draw_lines(str(rag_answer).splitlines(), y)

    if llm_expl:
        y -= 10
        y = draw_lines(["LLM Flowchart Explanation:"], y, line_height=16)
        y = draw_lines(llm_expl.splitlines(), y)

    if llm_mermaid_raw:
        y -= 10
        y = draw_lines(["LLM Mermaid (text):"], y, line_height=16)
        y = draw_lines(llm_mermaid_raw.splitlines(), y)

    y -= 10
    if eligible_no_conc or eligible_with_conc:
        y = draw_lines(["Eligible Courses (No Concurrent):"], y, line_height=16)
        y = draw_lines(eligible_no_conc if eligible_no_conc else ["None"], y)
        y -= 8
        y = draw_lines(["Eligible Courses (With Concurrent):"], y, line_height=16)
        y = draw_lines(eligible_with_conc if eligible_with_conc else ["None"], y)

    y -= 10
    if semantic_results:
        y = draw_lines(["Semantic Results:"], y, line_height=16)
        lines = [f'{r["code"]} — {r["name"]} (score {r["score"]:.3f})' for r in semantic_results]
        y = draw_lines(lines, y)

    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="psu_course_planner_export.pdf",
        mimetype="application/pdf",
    )


@app.route("/", methods=["GET", "POST"])
def index():
    s = get_settings()

    dept = "CMPSC"
    completed_text = ""
    completed: set[str] = set()

    lvl100 = lvl200 = lvl300 = lvl400 = True
    max_results = None

    search_query = ""
    search_results: list[Course] = []

    semantic_query = ""
    semantic_results = []

    rag_question = ""
    rag_response = ""

    why_not_query = ""
    why_not_answer = ""

    warning = ""

    graph_nodes = []
    graph_edges = []

    llm_explanation = ""
    llm_mermaid = ""

    if request.method == "POST":
        dept = request.form.get("dept", "CMPSC").strip().upper()
        session["last_dept"] = dept

        completed_text = request.form.get("completed", "").strip()
        completed = {c.strip().upper().replace("  ", " ") for c in completed_text.split(",") if c.strip()}

        lvl100 = bool(request.form.get("lvl100"))
        lvl200 = bool(request.form.get("lvl200"))
        lvl300 = bool(request.form.get("lvl300"))
        lvl400 = bool(request.form.get("lvl400"))

        max_raw = request.form.get("max_results", "").strip()
        if max_raw:
            try:
                max_results = int(max_raw)
                if max_results <= 0:
                    max_results = None
            except ValueError:
                max_results = None

        search_query = request.form.get("search_query", "").strip()
        semantic_query = request.form.get("semantic_query", "").strip() if s["show_semantic"] else ""
        rag_question = request.form.get("rag_question", "").strip() if s["show_rag"] else ""
        why_not_query = request.form.get("why_not_query", "").strip()

    catalog: Dict[str, Course] = get_dept_catalog(dept)

    # Basics
    all_basics = basic_courses(catalog)
    basics_100 = [c for c in all_basics if is_100_level(c.code)]
    basic_top_by_level = group_by_level(basics_100) if not completed else {}

    # Eligible
    no_concurrent_levels = {}
    with_concurrent_levels = {}
    avail: list[Course] = []

    if completed:
        avail = available_courses(catalog, completed)

        raw_no_concurrent = [c for c in avail if not c.concurrent_groups]
        with_concurrent_courses = [c for c in avail if c.concurrent_groups]

        no_concurrent = filter_by_levels(raw_no_concurrent, lvl100, lvl200, lvl300, lvl400)
        with_concurrent_courses = filter_by_levels(with_concurrent_courses, lvl100, lvl200, lvl300, lvl400)

        if max_results is not None:
            no_concurrent = no_concurrent[:max_results]
            with_concurrent_courses = with_concurrent_courses[:max_results]

        no_concurrent_levels = group_by_level(no_concurrent)
        with_concurrent_levels = group_by_level(with_concurrent_courses)

    # Build vis graph
    if s["show_graph"]:
        try:
            graph_nodes, graph_edges, _ = build_progression_graph(catalog, completed, max_depth=2)
        except Exception:
            graph_nodes, graph_edges = [], []

    # Search
    if search_query:
        search_results = find_course(catalog, search_query)

    # Why-not
    if why_not_query:
        why_not_answer = explain_why_not(catalog, why_not_query, completed)

    # Ensure local index for semantic/rag
    index_path = f"{dept.lower()}_index.json"
    if (semantic_query or rag_question) and not os.path.exists(index_path):
        warning = f"Local semantic index missing ({index_path}). Building now..."
        build_local_embeddings_index(catalog, dept)

    # Semantic
    level_filters = set()
    if lvl100: level_filters.add(100)
    if lvl200: level_filters.add(200)
    if lvl300: level_filters.add(300)
    if lvl400: level_filters.add(400)

    if semantic_query and s["show_semantic"]:
        semantic_results = semantic_search_courses(
            dept=dept,
            query=semantic_query,
            top_k=12,
            level_filters=level_filters if level_filters else None,
        )

    # RAG + LLM flowchart
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
                eligible=avail,
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
                expl, mermaid = generate_llm_flowchart_mermaid(
                    dept=dept,
                    completed=completed,
                    eligible=avail,
                    question=rag_question,
                )
                llm_explanation = expl
                llm_mermaid = mermaid
                session["last_llm_flowchart"] = {"explanation": expl, "mermaid": mermaid}
            except Exception as e:
                llm_explanation = f"LLM flowchart generation failed: {type(e).__name__}: {e}"
                llm_mermaid = ""
                session["last_llm_flowchart"] = {"explanation": llm_explanation, "mermaid": ""}

    else:
        llm_fc = session.get("last_llm_flowchart", {})
        llm_explanation = llm_fc.get("explanation", "")
        llm_mermaid = llm_fc.get("mermaid", "")

    llm_mermaid = sanitize_mermaid(llm_mermaid)

    # Snapshot for PDF export
    eligible_no_conc_lines = []
    for lvl, courses in no_concurrent_levels.items():
        eligible_no_conc_lines.append(f"{lvl}-level:" if lvl else "Other-level:")
        for c in courses:
            eligible_no_conc_lines.append(f"  - {c.code} {format_credits(c.credits)} — {c.name}".strip())

    eligible_with_conc_lines = []
    for lvl, courses in with_concurrent_levels.items():
        eligible_with_conc_lines.append(f"{lvl}-level:" if lvl else "Other-level:")
        for c in courses:
            eligible_with_conc_lines.append(f"  - {c.code} {format_credits(c.credits)} — {c.name}".strip())

    session["last_view_state"] = {
        "dept": dept,
        "completed": sorted(completed),
        "rag_response": rag_response,
        "semantic_results": semantic_results,
        "eligible_no_conc": eligible_no_conc_lines,
        "eligible_with_conc": eligible_with_conc_lines,
    }

    return render_template_string(
        HTML_TEMPLATE,
        dept=dept,
        completed_text=completed_text,
        completed=sorted(completed),
        basic_top=basic_top_by_level,
        no_concurrent=no_concurrent_levels,
        with_concurrent=with_concurrent_levels,
        format_groups=format_groups,
        format_credits=format_credits,
        lvl100=lvl100,
        lvl200=lvl200,
        lvl300=lvl300,
        lvl400=lvl400,
        max_results=max_results,
        search_query=search_query,
        search_results=search_results,
        semantic_query=semantic_query,
        semantic_results=semantic_results,
        rag_question=rag_question,
        rag_response=rag_response,
        why_not_query=why_not_query,
        why_not_answer=why_not_answer,
        warning=warning,
        show_rag=s["show_rag"],
        show_semantic=s["show_semantic"],
        show_eligible=s["show_eligible"],
        show_graph=s["show_graph"],
        show_llm_flowchart=s["show_llm_flowchart"],
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        llm_explanation=llm_explanation,
        llm_mermaid=llm_mermaid,
    )


if __name__ == "__main__":
    app.run(debug=True)