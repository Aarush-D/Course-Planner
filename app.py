import re
import json
from flask import Flask, request, render_template_string
from typing import Dict
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
    build_prereq_graph,
    build_future_graph,
    semantic_search_courses,
    rag_answer as rag_answer_fn,  # <-- rename to avoid name collisions
)

app = Flask(__name__)

HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
    <title>PSU Course Planner</title>
    <style>
        body { font-family: sans-serif; max-width: 900px; margin: 2rem auto; }
        textarea { width: 100%; height: 80px; }
        .section { margin-top: 2rem; }
        h2 { border-bottom: 1px solid #ccc; padding-bottom: 0.3rem; }
        .level-header { margin-top: 1rem; font-weight: bold; }
        .course { margin-left: 1rem; margin-bottom: 0.4rem; }
        .sub { margin-left: 2.5rem; font-size: 0.9rem; color: #444; }
        label { font-weight: 600; }
        .filters { margin-top: 0.5rem; margin-bottom: 1rem; }
        .filters span { margin-right: 1rem; }
        input[type="number"] { width: 80px; }
        input[type="text"] { width: 280px; }
        #graph-prereq, #graph-future {
            margin-top: 1rem;
            height: 400px;
            border: 1px solid #ccc;
        }
        .graph-tabs {
            margin-top: 1rem;
        }
        .graph-tabs button {
            margin-right: 0.5rem;
            padding: 0.4rem 0.8rem;
            cursor: pointer;
        }
        .graph-tabs button.active {
            background-color: #1976d2;
            color: white;
            border: none;
        }
    </style>
    <script src="https://unpkg.com/vis-network@9.1.2/dist/vis-network.min.js"></script>
</head>
<body>
    <h1>PSU Course Planner</h1>
    <p>
        Choose a department and enter completed courses (comma-separated), e.g.
        <code>MATH 140, CMPSC 131, CMPSC 132</code>.
    </p>

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
                <input type="number" name="max_results"
                       min="1"
                       value="{{ max_results if max_results is not none else '' }}">
            </span>
        </div>

        <div class="filters">
            <span>Semantic search:</span>
            <input type="text" name="semantic_query" value="{{ semantic_query }}">
        </div>

        <div class="filters">
            <span>Ask with RAG:</span>
            <input type="text" name="rag_question" value="{{ rag_question }}">
        </div>

        <div class="filters">
            <span>Search for a course (code or name):</span>
            <input type="text" name="search_query" value="{{ search_query }}">
            <span style="font-size: 0.9rem; color: #666;">
                e.g. "CMPSC 131", "313", "Assembly", "AI"
            </span>
        </div>

        <div class="filters">
            <span>Goal course (optional):</span>
            <input type="text" name="goal_course" value="{{ goal_course }}">
            <span style="font-size: 0.9rem; color: #666;">
                e.g. "CMPSC 473"
            </span>
        </div>

        <textarea name="completed">{{ completed_text }}</textarea><br>
        <button type="submit">Show Eligible Courses</button>
    </form>

    {% if completed %}
    <div class="section">
        <h2>Completed Courses (you entered)</h2>
        <ul>
        {% for c in completed %}
            <li>{{ c }}</li>
        {% endfor %}
        </ul>
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
                <div class="sub">
                    {{ course.description }}
                </div>
            {% endif %}
            {% if course.prereq_groups %}
                <div class="sub">
                    Prereqs: {{ format_groups(course.prereq_groups) }}
                </div>
            {% endif %}
            {% if course.concurrent_groups %}
                <div class="sub">
                    Concurrent: {{ format_groups(course.concurrent_groups) }}
                </div>
            {% endif %}
            <br>
        {% endfor %}
    </div>
    {% endif %}

    {% if (graph_course_code or future_graph_has_nodes) %}
    <div class="section">
        <h2>Course Graph ({{ dept }})</h2>
        <div class="graph-tabs">
            <button type="button" id="btn-prereq" onclick="showGraph('prereq')">
                Past / prerequisites
            </button>
            <button type="button" id="btn-future" onclick="showGraph('future')">
                Future / unlocked
            </button>
        </div>
        <div id="graph-prereq"></div>
        <div id="graph-future" style="display:none;"></div>
    </div>
    {% endif %}

    {% if basic_top %}
    <div class="section">
        <h2>Intro {{ dept }} Courses (100-level, no prerequisites / no concurrent requirements)</h2>
        {% for lvl, courses in basic_top.items() %}
            <div class="level-header">
                {{ "Other-level" if lvl == 0 else (lvl|string + "-level") }}
            </div>
            {% for course in courses %}
                <div class="course">
                    - {{ course.code }}
                    {% if course.credits is not none %}
                        ({{ format_credits(course.credits) }})
                    {% endif %}
                    — {{ course.name }}
                </div>
            {% endfor %}
        {% endfor %}
    </div>
    {% endif %}

    {% if no_concurrent or with_concurrent %}
    <div class="section">
        <h2>Eligible {{ dept }} Courses (NO Concurrent Requirement)</h2>
        {% if not no_concurrent %}
            <p>None</p>
        {% else %}
            {% for lvl, courses in no_concurrent.items() %}
                <div class="level-header">
                    {{ "Other-level" if lvl == 0 else (lvl|string + "-level") }}
                </div>
                {% for course in courses %}
                    <div class="course">
                        - {{ course.code }}
                        {% if course.credits is not none %}
                            ({{ format_credits(course.credits) }})
                        {% endif %}
                        — {{ course.name }}
                    </div>
                    {% if course.prereq_groups %}
                        <div class="sub">
                            Prereqs: {{ format_groups(course.prereq_groups) }}
                        </div>
                    {% endif %}
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
                <div class="level-header">
                    {{ "Other-level" if lvl == 0 else (lvl|string + "-level") }}
                </div>
                {% for course in courses %}
                    <div class="course">
                        - {{ course.code }}
                        {% if course.credits is not none %}
                            ({{ format_credits(course.credits) }})
                        {% endif %}
                        — {{ course.name }}
                    </div>
                    {% if course.prereq_groups %}
                        <div class="sub">
                            Prereqs: {{ format_groups(course.prereq_groups) }}
                        </div>
                    {% endif %}
                    {% if course.concurrent_groups %}
                        <div class="sub">
                            Concurrent: {{ format_groups(course.concurrent_groups) }}
                        </div>
                    {% endif %}
                {% endfor %}
            {% endfor %}
        {% endif %}
    </div>
    {% endif %}

    {% if basic_bottom %}
    <div class="section">
        <h2>{{ dept }} Courses with no prerequisites / no concurrent requirements (filtered by level)</h2>
        {% for lvl, courses in basic_bottom.items() %}
            <div class="level-header">
                {{ "Other-level" if lvl == 0 else (lvl|string + "-level") }}
            </div>
            {% for course in courses %}
                <div class="course">
                    - {{ course.code }}
                    {% if course.credits is not none %}
                        ({{ format_credits(course.credits) }})
                    {% endif %}
                    — {{ course.name }}
                </div>
            {% endfor %}
        {% endfor %}
    </div>
    {% endif %}

    {% if goal_course_result %}
    <div class="section">
        <h2>Goal Course Analysis: {{ goal_course_result.code }} — {{ goal_course_result.name }}</h2>

        {% if goal_course_result.pre_missing or goal_course_result.conc_missing %}
            <p>Here’s what you still need:</p>
        {% else %}
            <p>You already satisfy all enforced prerequisites and concurrent requirements for this goal course.</p>
        {% endif %}

        {% if goal_course_result.pre_missing %}
            <h3>Unmet Prerequisites</h3>
            <ul>
            {% for group in goal_course_result.pre_missing %}
                <li>
                    {% if group|length > 1 %}
                        ({{ " or ".join(group) }})
                    {% else %}
                        {{ group[0] }}
                    {% endif %}
                </li>
            {% endfor %}
            </ul>
        {% endif %}

        {% if goal_course_result.conc_missing %}
            <h3>Unmet Concurrent Requirements</h3>
            <ul>
            {% for group in goal_course_result.conc_missing %}
                <li>
                    {% if group|length > 1 %}
                        ({{ " or ".join(group) }})
                    {% else %}
                        {{ group[0] }}
                    {% endif %}
                </li>
            {% endfor %}
            </ul>
        {% endif %}

        {% if goal_course_result.pre_satisfied or goal_course_result.conc_satisfied %}
            <h3>Already Satisfied</h3>
            <ul>
              {% for group in goal_course_result.pre_satisfied %}
                <li>Prereq group satisfied by: {{ ", ".join(group) }}</li>
              {% endfor %}
              {% for group in goal_course_result.conc_satisfied %}
                <li>Concurrent group satisfied by: {{ ", ".join(group) }}</li>
              {% endfor %}
            </ul>
        {% endif %}
    </div>
    {% endif %}

    {% if (graph_course_code or future_graph_has_nodes) %}
    <script>
    (function() {
        var prereqNodes = {{ graph_nodes_json|safe }};
        var prereqEdges = {{ graph_edges_json|safe }};
        var futureNodes = {{ future_nodes_json|safe }};
        var futureEdges = {{ future_edges_json|safe }};

        var prereqContainer = document.getElementById('graph-prereq');
        var futureContainer = document.getElementById('graph-future');

        function computeLevelLayout(nodes, maxRows) {
            var levelOrder = [100, 200, 300, 400, 0]; // 0 = Other/unknown

            var buckets = {};
            nodes.forEach(n => {
                var lvl = (n.level === undefined || n.level === null) ? 0 : n.level;
                if (lvl >= 400) lvl = 400;
                if (![100,200,300,400].includes(lvl)) lvl = 0;
                buckets[lvl] = buckets[lvl] || [];
                buckets[lvl].push(n);
            });

            levelOrder.forEach(lvl => {
                if (buckets[lvl]) {
                    buckets[lvl].sort((a,b) => (a.label||"").localeCompare(b.label||""));
                }
            });

            var X_SPACING = 240;
            var Y_SPACING = 70;
            var WRAP_SPACING = 220;

            levelOrder.forEach((lvl, colIndex) => {
                var arr = buckets[lvl] || [];
                arr.forEach((node, i) => {
                    var wrap = Math.floor(i / maxRows);
                    var row  = i % maxRows;
                    node.x = (colIndex * X_SPACING) + (wrap * WRAP_SPACING);
                    node.y = row * Y_SPACING;
                    node.fixed = { x: true, y: true };
                });
            });

            return nodes;
        }

        function makeNetwork(container, nodes, edges, mode) {
            if (!container || !nodes || !nodes.length) return null;

            nodes = computeLevelLayout(nodes, 10);

            var data = {
                nodes: new vis.DataSet(nodes),
                edges: new vis.DataSet(edges),
            };

            var options = {
                physics: false,
                interaction: { hover: true },
                edges: {
                    arrows: { to: { enabled: true } },
                    font: { align: 'middle', size: 10 }
                },
                nodes: {
                    shape: 'box',
                    margin: 10,
                    font: { size: 12 }
                }
            };

            var network = new vis.Network(container, data, options);

            function buildAdjacency(edgesArr) {
                var incoming = new Map();
                var outgoing = new Map();
                edgesArr.forEach(e => {
                    if (!incoming.has(e.to)) incoming.set(e.to, []);
                    if (!outgoing.has(e.from)) outgoing.set(e.from, []);
                    incoming.get(e.to).push(e.from);
                    outgoing.get(e.from).push(e.to);
                });
                return { incoming, outgoing };
            }

            var adj = buildAdjacency(edges);

            function ancestors(startId) {
                var seen = new Set();
                var stack = [startId];
                while (stack.length) {
                    var cur = stack.pop();
                    var ins = adj.incoming.get(cur) || [];
                    ins.forEach(p => {
                        if (!seen.has(p)) {
                            seen.add(p);
                            stack.push(p);
                        }
                    });
                }
                return seen;
            }

            function descendants(startId) {
                var seen = new Set();
                var stack = [startId];
                while (stack.length) {
                    var cur = stack.pop();
                    var outs = adj.outgoing.get(cur) || [];
                    outs.forEach(n => {
                        if (!seen.has(n)) {
                            seen.add(n);
                            stack.push(n);
                        }
                    });
                }
                return seen;
            }

            function highlight(nodeId) {
                var keep = new Set([nodeId]);

                if (mode === 'future') {
                    descendants(nodeId).forEach(x => keep.add(x));
                } else {
                    ancestors(nodeId).forEach(x => keep.add(x));
                }

                var allNodes = data.nodes.get();
                allNodes.forEach(n => {
                    var isKeep = keep.has(n.id);
                    var base = n.color;

                    n.color = {
                        background: base,
                        border: "#555",
                        opacity: isKeep ? 1.0 : 0.35
                    };
                    n.font = { color: isKeep ? "#000" : "#777" };
                });
                data.nodes.update(allNodes);

                var allEdges = data.edges.get();
                allEdges.forEach(e => {
                    var isKeepEdge = keep.has(e.from) && keep.has(e.to);
                    e.width = isKeepEdge ? 2.5 : 1;
                    e.color = { opacity: isKeepEdge ? 1.0 : 0.2 };
                });
                data.edges.update(allEdges);
            }

            function resetHighlight() {
                var allNodes = data.nodes.get();
                allNodes.forEach(n => {
                    var restored = (typeof n.color === "string") ? n.color : (n.color && n.color.background) || "#e0e0e0";
                    n.color = restored;
                    n.font = { color: "#000" };
                });
                data.nodes.update(allNodes);

                var allEdges = data.edges.get();
                allEdges.forEach(e => {
                    e.width = 1;
                    e.color = { opacity: 1.0 };
                });
                data.edges.update(allEdges);
            }

            network.on("click", function(params) {
                if (params.nodes && params.nodes.length) {
                    highlight(params.nodes[0]);
                } else {
                    resetHighlight();
                }
            });

            return network;
        }

        var prereqNetwork = makeNetwork(prereqContainer, prereqNodes, prereqEdges, 'prereq');
        var futureNetwork = makeNetwork(futureContainer, futureNodes, futureEdges, 'future');

        window.showGraph = function(which) {
            var btnPrereq = document.getElementById('btn-prereq');
            var btnFuture = document.getElementById('btn-future');

            if (which === 'future') {
                if (futureContainer) futureContainer.style.display = 'block';
                if (prereqContainer) prereqContainer.style.display = 'none';
                if (btnFuture) btnFuture.classList.add('active');
                if (btnPrereq) btnPrereq.classList.remove('active');
            } else {
                if (prereqContainer) prereqContainer.style.display = 'block';
                if (futureContainer) futureContainer.style.display = 'none';
                if (btnPrereq) btnPrereq.classList.add('active');
                if (btnFuture) btnFuture.classList.remove('active');
            }
        };

        {% if graph_course_code %}
            showGraph('prereq');
        {% elif future_graph_has_nodes %}
            showGraph('future');
        {% endif %}
    })();
    </script>
    {% endif %}

    {% if semantic_results %}
    <div class="section">
        <h2>Semantic results ({{ dept }})</h2>
        {% for r in semantic_results %}
            <div class="course">
                <strong>{{ r.code }}</strong> — {{ r.name }}
                <span style="color:#666;">(score {{ "%.3f"|format(r.score) }})</span>
            </div>
        {% endfor %}
    </div>
    {% endif %}

    {% if rag_response %}
    <div class="section">
        <h2>RAG Answer</h2>
        <div class="sub">{{ rag_response }}</div>
    </div>
    {% endif %}
</body>
</html>
"""

def is_100_level(code: str) -> bool:
    m = re.search(r"\b(\d{3})[A-Z]?\b", code)
    if not m:
        return False
    num = int(m.group(1))
    return 100 <= num < 200


def filter_by_levels(courses, lvl100: bool, lvl200: bool, lvl300: bool, lvl400: bool):
    allowed_levels = set()
    if lvl100:
        allowed_levels.add(100)
    if lvl200:
        allowed_levels.add(200)
    if lvl300:
        allowed_levels.add(300)
    if lvl400:
        allowed_levels.add(400)

    if not allowed_levels:
        return []

    filtered = []
    for c in courses:
        lvl = course_level(c.code)
        if lvl is None:
            continue
        if lvl >= 400 and 400 in allowed_levels:
            filtered.append(c)
        elif lvl in allowed_levels:
            filtered.append(c)
    return filtered


@app.route("/", methods=["GET", "POST"])
def index():
    dept = "CMPSC"
    completed_text = ""
    completed: set[str] = set()
    no_concurrent_levels = {}
    with_concurrent_levels = {}

    lvl100 = lvl200 = lvl300 = lvl400 = True
    max_results = None

    search_query = ""
    search_results: list[Course] = []
    goal_course = ""
    goal_course_result = None

    graph_course_code = None
    graph_nodes_json = "[]"
    graph_edges_json = "[]"
    future_nodes_json = "[]"
    future_edges_json = "[]"
    future_graph_has_nodes = False

    # IMPORTANT: always define these (fixes UnboundLocalError)
    semantic_query = ""
    semantic_results = []
    rag_question = ""
    rag_response = ""

    if request.method == "POST":
        dept = request.form.get("dept", "CMPSC").strip().upper()

        completed_text = request.form.get("completed", "").strip()
        completed = {
            c.strip().upper().replace("  ", " ")
            for c in completed_text.split(",")
            if c.strip()
        }

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
        goal_course = request.form.get("goal_course", "").strip()

        semantic_query = request.form.get("semantic_query", "").strip()
        rag_question = request.form.get("rag_question", "").strip()

    catalog: Dict[str, Course] = get_dept_catalog(dept)

    # Basic courses (no prereqs or concurrent)
    all_basics = basic_courses(catalog)
    basics_100 = [c for c in all_basics if is_100_level(c.code)]

    has_completed_input = bool(completed)

    if not has_completed_input:
        basic_top_by_level = group_by_level(basics_100)
        basic_bottom_by_level = {}
        basic_codes_for_exclusion = set()
    else:
        basic_top_by_level = {}
        basics_filtered = filter_by_levels(all_basics, lvl100, lvl200, lvl300, lvl400)
        if max_results is not None:
            basics_filtered = basics_filtered[:max_results]
        basic_bottom_by_level = group_by_level(basics_filtered)
        basic_codes_for_exclusion = {c.code for c in all_basics}

    # Availability
    avail: list[Course] = []
    avail_codes: set[str] = set()

    if has_completed_input:
        avail = available_courses(catalog, completed)
        avail_codes = {c.code for c in avail}

        raw_no_concurrent = [c for c in avail if not c.concurrent_groups]
        with_concurrent_courses = [c for c in avail if c.concurrent_groups]

        raw_no_concurrent = [c for c in raw_no_concurrent if c.code not in basic_codes_for_exclusion]

        no_concurrent = filter_by_levels(raw_no_concurrent, lvl100, lvl200, lvl300, lvl400)
        with_concurrent_courses = filter_by_levels(with_concurrent_courses, lvl100, lvl200, lvl300, lvl400)

        if max_results is not None:
            no_concurrent = no_concurrent[:max_results]
            with_concurrent_courses = with_concurrent_courses[:max_results]

        no_concurrent_levels = group_by_level(no_concurrent)
        with_concurrent_levels = group_by_level(with_concurrent_courses)
    else:
        no_concurrent_levels = {}
        with_concurrent_levels = {}
        basic_bottom_by_level = {}

    # Search / prereq graph
    if search_query:
        search_results = find_course(catalog, search_query)
        if len(search_results) == 1:
            graph_course_code = search_results[0].code
            nodes, edges = build_prereq_graph(
                catalog,
                graph_course_code,
                completed=completed,
                available=avail_codes,
                max_depth=2,
            )
            graph_nodes_json = json.dumps(nodes)
            graph_edges_json = json.dumps(edges)

    # Future graph from completed + available
    if completed or avail_codes:
        f_nodes, f_edges = build_future_graph(
            catalog,
            completed=completed,
            available=avail_codes,
            max_depth=2,
        )
        if f_nodes:
            future_nodes_json = json.dumps(f_nodes)
            future_edges_json = json.dumps(f_edges)
            future_graph_has_nodes = True

    # Goal course analysis
    if goal_course:
        matches = find_course(catalog, goal_course)
        if matches:
            chosen = None
            goal_norm = goal_course.strip().upper().replace("  ", " ")
            for m in matches:
                if m.code.upper() == goal_norm:
                    chosen = m
                    break
            if chosen is None:
                chosen = matches[0]

            pre_satisfied = []
            pre_missing = []
            for group in chosen.prereq_groups:
                if group & completed:
                    pre_satisfied.append(sorted(group))
                else:
                    pre_missing.append(sorted(group))

            conc_satisfied = []
            conc_missing = []
            completed_or_avail = completed | avail_codes
            for group in chosen.concurrent_groups:
                if group & completed_or_avail:
                    conc_satisfied.append(sorted(group))
                else:
                    conc_missing.append(sorted(group))

            goal_course_result = {
                "code": chosen.code,
                "name": chosen.name,
                "pre_satisfied": pre_satisfied,
                "pre_missing": pre_missing,
                "conc_satisfied": conc_satisfied,
                "conc_missing": conc_missing,
            }

    # Semantic + RAG
    if semantic_query:
        level_filters = set()
        if lvl100: level_filters.add(100)
        if lvl200: level_filters.add(200)
        if lvl300: level_filters.add(300)
        if lvl400: level_filters.add(400)

        semantic_results = semantic_search_courses(
            dept=dept,
            query=semantic_query,
            top_k=12,
            level_filters=level_filters if level_filters else None,
        )

    if rag_question:
        level_filters = set()
        if lvl100: level_filters.add(100)
        if lvl200: level_filters.add(200)
        if lvl300: level_filters.add(300)
        if lvl400: level_filters.add(400)

        rag_response = rag_answer_fn(
            dept=dept,
            question=rag_question,
            top_k=8,
            level_filters=level_filters if level_filters else None,
        )

    return render_template_string(
        HTML_TEMPLATE,
        dept=dept,
        completed_text=completed_text,
        completed=sorted(completed),
        basic_top=basic_top_by_level,
        basic_bottom=basic_bottom_by_level,
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
        goal_course=goal_course,
        goal_course_result=goal_course_result,
        graph_course_code=graph_course_code,
        graph_nodes_json=graph_nodes_json,
        graph_edges_json=graph_edges_json,
        future_nodes_json=future_nodes_json,
        future_edges_json=future_edges_json,
        future_graph_has_nodes=future_graph_has_nodes,
        semantic_query=semantic_query,
        semantic_results=semantic_results,
        rag_question=rag_question,
        rag_response=rag_response,
    )

if __name__ == "__main__":
    app.run(debug=True)
