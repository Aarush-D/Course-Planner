from flask import Flask, request, render_template_string
from typing import Dict, List
from Courseplanner import (  # change to your filename
    Course,
    get_cmpsc_catalog,
    available_courses,
    format_groups,
    format_credits,
    group_by_level,
)

app = Flask(__name__)

HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
    <title>PSU CMPSC Course Planner</title>
    <style>
        body { font-family: sans-serif; max-width: 900px; margin: 2rem auto; }
        textarea { width: 100%; height: 80px; }
        .section { margin-top: 2rem; }
        h2 { border-bottom: 1px solid #ccc; padding-bottom: 0.3rem; }
        .level-header { margin-top: 1rem; font-weight: bold; }
        .course { margin-left: 1rem; margin-bottom: 0.4rem; }
        .sub { margin-left: 2.5rem; font-size: 0.9rem; color: #444; }
    </style>
</head>
<body>
    <h1>PSU CMPSC Course Planner</h1>
    <p>Enter completed courses (comma-separated), e.g. <code>MATH 140, CMPSC 131, CMPSC 132</code>.</p>

    <form method="post">
        <textarea name="completed">{{ completed_text }}</textarea><br>
        <button type="submit">Show Eligible Courses</button>
    </form>

    {% if completed %}
    <div class="section">
        <h2>Completed Courses</h2>
        <ul>
        {% for c in completed %}
            <li>{{ c }}</li>
        {% endfor %}
        </ul>
    </div>
    {% endif %}

    {% if no_concurrent or with_concurrent %}
    <div class="section">
        <h2>Eligible Courses (NO Concurrent Requirement)</h2>
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
        <h2>Eligible Courses (WITH Enforced Concurrent at Enrollment)</h2>
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
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    catalog: Dict[str, Course] = get_cmpsc_catalog()

    completed_text = ""
    completed: set[str] = set()
    no_concurrent_levels = {}
    with_concurrent_levels = {}

    if request.method == "POST":
        completed_text = request.form.get("completed", "").strip()
        completed = {
            c.strip().upper().replace("  ", " ")
            for c in completed_text.split(",")
            if c.strip()
        }

        avail = available_courses(catalog, completed)
        no_concurrent = [c for c in avail if not c.concurrent_groups]
        with_concurrent = [c for c in avail if c.concurrent_groups]

        no_concurrent_levels = group_by_level(no_concurrent)
        with_concurrent_levels = group_by_level(with_concurrent)

    return render_template_string(
        HTML_TEMPLATE,
        completed_text=completed_text,
        completed=sorted(completed),
        no_concurrent=no_concurrent_levels,
        with_concurrent=with_concurrent_levels,
        format_groups=format_groups,
        format_credits=format_credits,
    )

if __name__ == "__main__":
    app.run(debug=True)
