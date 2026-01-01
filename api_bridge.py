# api_bridge.py
from flask import Flask, request, jsonify
from Courseplanner import (
    get_dept_catalog,
    available_courses,
    rag_answer,
    Course
)

app = Flask(__name__)

@app.route("/api/course-plan", methods=["POST"])
def course_plan():
    data = request.get_json(force=True)

    dept = data.get("department", "CMPSC").upper()
    completed = {c.strip().upper() for c in data.get("completedCourses", []) if c.strip()}
    question = data.get("question", "")

    catalog = get_dept_catalog(dept)
    eligible = available_courses(catalog, completed)

    # Use your existing RAG logic
    answer = rag_answer(
        dept=dept,
        question=question,
        completed=completed,
        eligible=eligible,
        chat_history=None,
    )

    # Convert eligible courses into UI-safe objects
    flowchart = []
    for c in eligible[:8]:
        flowchart.append({
            "id": c.code,
            "name": c.name,
            "description": c.description or "",
            "prerequisites": sorted({p for g in c.prereq_groups for p in g}),
        })

    return jsonify({
        "recommendations": answer,
        "flowchart": flowchart
    })


if __name__ == "__main__":
    app.run(port=5000, debug=True)