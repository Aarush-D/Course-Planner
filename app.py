import re
from flask import Flask, request, render_template_string
from typing import Dict
from Courseplanner import (
    Course,
    get_dept_catalog,      # CMPSC, CMPEN, MATH, STAT, etc.
    available_courses,
    format_groups,
    format_credits,
    group_by_level,
    basic_courses,
    course_level,
    find_course,           # NEW
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
    </style>
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
            <span>Search for a course (code or name):</span>
            <input type="text" name="search_query" value="{{ search_query }}">
            <span style="font-size: 0.9rem; color: #666;">
                e.g. "CMPSC 131", "313", "Assembly", "AI"
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

    {# ----- TOP intro section: only BEFORE user has completed input ----- #}
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

    {# ----- BOTTOM basic section: AFTER user input, all no-req courses (all levels, filtered) ----- #}
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

    # filters
    lvl100 = lvl200 = lvl300 = lvl400 = True
    max_results = None

    search_query = ""
    search_results: list[Course] = []

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

    catalog: Dict[str, Course] = get_dept_catalog(dept)

    # If there's a search query, populate search_results (independent of completed courses)
    if search_query:
        search_results = find_course(catalog, search_query)

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

    if has_completed_input:
        avail = available_courses(catalog, completed)

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
    )


if __name__ == "__main__":
    app.run(debug=True)
