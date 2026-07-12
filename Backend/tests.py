"""Smoke tests for the course planner backend.

Run from the Backend directory:

    USE_OLLAMA=0 python tests.py

Uses only the standard library (unittest) plus the app's own modules.
Requires the cached catalogs in Backend/catalogs/ (created on first app run).
"""
from __future__ import annotations

import os
import re
import unittest

os.environ.setdefault("USE_OLLAMA", "0")  # tests must not depend on Ollama

import planner_engine as engine
from app import app, parse_completion_changes, _extract_major_from_prompt


def _plan_and_catalog():
    plan = engine.load_degree_plan("CMPSC")
    catalog = engine.load_merged_catalog(plan["departments"])
    return plan, catalog


class TestMajorParsing(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(_extract_major_from_prompt("I am a Computer Science major"), "CMPSC")
        self.assertEqual(_extract_major_from_prompt("my major is CMPSC"), "CMPSC")
        self.assertEqual(_extract_major_from_prompt("I study Computer Engineering"), "CMPEN")
        self.assertEqual(_extract_major_from_prompt("I am a Statistics major"), "STAT")
        self.assertEqual(_extract_major_from_prompt("I am studying Mathematics"), "MATH")
        self.assertIsNone(_extract_major_from_prompt("What should I take next?"))


class TestCourseParsing(unittest.TestCase):
    def setUp(self):
        _, self.catalog = _plan_and_catalog()

    def test_code_formats(self):
        for text in ["CMPSC 131", "CMPSC131", "CMPSC-131", "cmpsc 131"]:
            matched, _ = engine.match_courses_in_text(f"I took {text}", self.catalog)
            self.assertEqual([m["code"] for m in matched], ["CMPSC 131"], text)

    def test_aliases(self):
        matched, _ = engine.match_courses_in_text("I took calc 1 and calc 2", self.catalog)
        codes = {m["code"] for m in matched}
        self.assertEqual(codes, {"MATH 140", "MATH 141"})

    def test_unknown_course_reported(self):
        matched, unmatched = engine.match_courses_in_text("I took CMPSC 999", self.catalog)
        self.assertEqual(matched, [])
        self.assertIn("CMPSC 999", unmatched)

    def test_leading_zeros_normalized(self):
        self.assertEqual(engine.norm_code("ENGL 015"), "ENGL 15")
        matched, _ = engine.match_courses_in_text("I completed ENGL 015", self.catalog)
        self.assertEqual([m["code"] for m in matched], ["ENGL 15"])


class TestStateMerging(unittest.TestCase):
    def setUp(self):
        _, self.catalog = _plan_and_catalog()

    def test_add_and_remove(self):
        added, removed, _ = parse_completion_changes(
            "I took CMPSC 131. I dropped MATH 140.", self.catalog
        )
        self.assertEqual([m["code"] for m in added], ["CMPSC 131"])
        self.assertEqual([m["code"] for m in removed], ["MATH 140"])

    def test_removal_wins_in_clause(self):
        added, removed, _ = parse_completion_changes(
            "I did not take CMPSC 132", self.catalog
        )
        self.assertEqual(added, [])
        self.assertEqual([m["code"] for m in removed], ["CMPSC 132"])

    def test_question_adds_nothing(self):
        added, removed, _ = parse_completion_changes(
            "Can I take CMPSC 465 next semester?", self.catalog
        )
        self.assertEqual(added, [])
        self.assertEqual(removed, [])


class TestEligibility(unittest.TestCase):
    def setUp(self):
        self.plan, self.catalog = _plan_and_catalog()

    def test_prereq_blocking(self):
        c132 = self.catalog["CMPSC 132"]
        self.assertFalse(engine.prereqs_satisfied(c132, set()))
        self.assertTrue(engine.prereqs_satisfied(c132, {"CMPSC 131"}))
        self.assertTrue(engine.prereqs_satisfied(c132, {"CMPSC 121"}))  # OR group

    def test_freshman_gets_semester_one(self):
        rec = engine.recommend_semester(self.plan, self.catalog, set())
        codes = {p["code"] for p in rec["courses"] if p["code"]}
        self.assertIn("CMPSC 131", codes)
        self.assertIn("MATH 140", codes)
        self.assertNotIn("CMPSC 132", codes)  # prereq CMPSC 131 not completed yet
        self.assertLessEqual(rec["total_credits"], 17.5)

    def test_full_plan_reaches_graduation(self):
        import datetime
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=datetime.date(2026, 7, 12),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertLessEqual(len(fp["terms"]), 9)
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(fp["terms"][0]["label"], "Fall 2026")


class TestYearPlanning(unittest.TestCase):
    def setUp(self):
        import datetime
        self.plan, self.catalog = _plan_and_catalog()
        self.today = datetime.date(2026, 7, 12)

    def test_three_year_goal_needs_summers(self):
        no_summer = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=3, allow_summer=False, today=self.today,
        )
        self.assertFalse(no_summer["goal"]["met"])
        self.assertTrue(any("summer" in w.lower() for w in no_summer["warnings"]))

        with_summer = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=3, allow_summer=True, today=self.today,
        )
        summer_terms = [t for t in with_summer["terms"] if t["is_summer"]]
        self.assertTrue(summer_terms)
        for t in summer_terms:
            self.assertLessEqual(t["total_credits"], engine.SUMMER_MAX_CREDITS)
        # Summers must make the goal at least as achievable.
        self.assertLessEqual(
            sum(1 for t in with_summer["terms"] if not t["within_goal"]),
            sum(1 for t in no_summer["terms"] if not t["within_goal"]),
        )

    def test_summer_unavailable_course_moves_out_of_summer(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=3, allow_summer=True, today=self.today,
        )
        # Find any course scheduled in a summer term, flag it unavailable, re-plan.
        summer_courses = [
            p["code"] for t in fp["terms"] if t["is_summer"]
            for p in t["courses"] if p["code"]
        ]
        if not summer_courses:
            self.skipTest("no summer-scheduled course to flag")
        flagged = summer_courses[0]
        fp2 = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=3, allow_summer=True,
            summer_unavailable={flagged}, today=self.today,
        )
        for t in fp2["terms"]:
            if t["is_summer"]:
                self.assertNotIn(flagged, [p["code"] for p in t["courses"]])
        # Still scheduled somewhere (a regular term).
        all_codes = [p["code"] for t in fp2["terms"] for p in t["courses"] if p["code"]]
        self.assertIn(flagged, all_codes)


class TestWeightedRanking(unittest.TestCase):
    def setUp(self):
        self.plan, self.catalog = _plan_and_catalog()

    def test_flowchart_beats_catalog_only(self):
        ranked = engine.score_recommendations(self.plan, self.catalog, {"CMPSC 131", "MATH 140"})
        self.assertTrue(ranked)
        flow = [r for r in ranked if r["source"] == "Official Advising Flowchart"]
        cat = [r for r in ranked if r["source"] == "Course Catalog"]
        if flow and cat:
            self.assertGreater(max(r["score"] for r in flow), max(r["score"] for r in cat))
        self.assertEqual(ranked, sorted(ranked, key=lambda r: -r["score"]))

    def test_completed_and_ineligible_excluded(self):
        completed = {"CMPSC 131", "MATH 140"}
        ranked = engine.score_recommendations(self.plan, self.catalog, completed)
        codes = {r["code"] for r in ranked}
        self.assertFalse(codes & completed)
        self.assertNotIn("CMPSC 465", codes)  # prereqs not met

    def test_special_topics_excluded_by_default(self):
        ranked = engine.score_recommendations(self.plan, self.catalog, set(), top_n=100)
        for r in ranked:
            name = (r["name"] or "").lower()
            self.assertNotIn("internship", name)
            self.assertNotIn("special topics", name)


class TestMermaid(unittest.TestCase):
    def setUp(self):
        self.plan, self.catalog = _plan_and_catalog()

    def test_valid_mermaid_shape(self):
        rec = engine.recommend_semester(self.plan, self.catalog, {"CMPSC 131", "MATH 140"})
        mm = engine.build_mermaid(self.plan, self.catalog, {"CMPSC 131", "MATH 140"}, rec["courses"])
        self.assertTrue(mm["mermaid"].startswith("flowchart"))
        self.assertNotRegex(mm["mermaid"], r"-->\s*$")      # no incomplete edges
        self.assertNotIn("classDef", mm["mermaid"])
        for line in mm["mermaid"].splitlines():
            line = line.strip()
            if "[" in line and not line.startswith("subgraph"):
                self.assertRegex(line, r'^[A-Za-z0-9_]+\["[^"\[\]]+"\]$')

    def test_empty_state_fallback(self):
        mm = engine.build_mermaid(self.plan, self.catalog, set(), [])
        self.assertTrue(mm["mermaid"].startswith("flowchart"))
        self.assertIn("Start here", mm["mermaid"])


class TestApiShape(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"status": "ok"})

    def test_plan_response_shape(self):
        r = self.client.post("/api/plan", json={
            "prompt": "I am a CMPSC major and I completed CMPSC 131 and MATH 140.",
            "completed": [],
        })
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        for key in ["state", "dept", "completed", "eligible", "graph", "rag_response",
                    "llm_flowchart", "coursePlan"]:
            self.assertIn(key, d)
        self.assertEqual(d["state"]["dept"], "CMPSC")
        self.assertEqual(sorted(d["state"]["completed"]), ["CMPSC 131", "MATH 140"])
        cp = d["coursePlan"]
        for key in ["recommendations", "tips", "nextSemester", "fullPlan", "progress", "matched"]:
            self.assertIn(key, cp)
        for rec in cp["recommendations"]:
            self.assertIn("name", rec)
            self.assertIn("reason", rec)
            self.assertIn("score", rec)
            self.assertIn("source", rec)

    def test_summer_unavailable_from_chat(self):
        r = self.client.post("/api/plan", json={
            "prompt": "CMPSC 360 is not available over the summer.",
            "completed": ["CMPSC 131", "CMPSC 132", "MATH 140", "MATH 141"],
            "start_year": 2025, "grad_years": 4, "allow_summer": True,
        })
        d = r.get_json()
        self.assertIn("CMPSC 360", d["state"]["summerUnavailable"])
        for t in d["coursePlan"]["fullPlan"]["terms"]:
            if t["isSummer"]:
                self.assertNotIn("CMPSC 360", [c["id"] for c in t["courses"]])

    def test_state_roundtrip_with_removal(self):
        r = self.client.post("/api/plan", json={
            "prompt": "I dropped MATH 140.",
            "completed": ["CMPSC 131", "MATH 140"],
        })
        self.assertEqual(r.get_json()["state"]["completed"], ["CMPSC 131"])

    def test_invalid_payload(self):
        r = self.client.post("/api/plan", json={"completed": "CMPSC 131"})
        self.assertEqual(r.status_code, 400)

    def test_acceptance_prompt(self):
        """Spec section 19 acceptance test (deterministic parts)."""
        r = self.client.post("/api/plan", json={
            "prompt": (
                "I am a CMPSC major. I have completed CMPSC 131, CMPSC 132, "
                "MATH 140, and MATH 141. I want courses that follow the official "
                "advising path, unlock upper-level classes, and help with software "
                "engineering internships. What should I take next?"
            ),
            "completed": [],
        })
        d = r.get_json()
        self.assertEqual(d["state"]["dept"], "CMPSC")
        self.assertEqual(
            sorted(d["state"]["completed"]),
            ["CMPSC 131", "CMPSC 132", "MATH 140", "MATH 141"],
        )
        cp = d["coursePlan"]
        self.assertTrue(cp["recommendations"])
        top = cp["recommendations"][0]
        self.assertEqual(top["source"], "Official Advising Flowchart")
        self.assertTrue(cp["llm_flowchart"]["mermaid"].startswith("flowchart"))
        completed_set = set(d["state"]["completed"])
        for rec in cp["recommendations"]:
            self.assertNotIn(rec["name"], completed_set)


if __name__ == "__main__":
    unittest.main(verbosity=2)
