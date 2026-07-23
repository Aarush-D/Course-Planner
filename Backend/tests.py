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
import transfer_credit as tc
from app import app, parse_completion_changes, _extract_major_from_prompt, _extract_start_year_from_prompt


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

    def test_premed_aliases(self):
        self.assertEqual(_extract_major_from_prompt("I am a premed student"), "PREMED")
        self.assertEqual(_extract_major_from_prompt("my major is Premedicine"), "PREMED")
        self.assertEqual(_extract_major_from_prompt("I'm pre-med"), "PREMED")

    def test_major_wins_over_course_code_mentioned_later(self):
        """A course code ('MATH 140') anywhere in the message must not shadow
        an earlier, explicit major statement (regression: dict-iteration
        order previously beat leftmost-in-text position)."""
        prompt = "I am a premedicine student. I've completed MATH 140 and CHEM 110."
        self.assertEqual(_extract_major_from_prompt(prompt), "PREMED")


class TestStartYearParsing(unittest.TestCase):
    def test_explicit_start_year_phrases(self):
        self.assertEqual(_extract_start_year_from_prompt("I started school in 2022"), 2022)
        self.assertEqual(_extract_start_year_from_prompt("oh I started college in 2023"), 2023)
        self.assertEqual(_extract_start_year_from_prompt("I began at Penn State in 2021"), 2021)
        self.assertEqual(_extract_start_year_from_prompt("I enrolled at PSU in 2024"), 2024)

    def test_no_false_positive_on_course_taking_language(self):
        # "started" here refers to a course, not college enrollment — must
        # not be misread as a start-year correction.
        self.assertIsNone(
            _extract_start_year_from_prompt("I started CMPSC 131 in Fall 2022")
        )

    def test_no_match_without_year(self):
        self.assertIsNone(_extract_start_year_from_prompt("I started college last year"))

    def test_no_match_when_absent(self):
        self.assertIsNone(_extract_start_year_from_prompt("What should I take next?"))


class TestHistoricalCatalogYears(unittest.TestCase):
    """Every catalog year (2022-2026) for CMPSC and PREMED must load and
    simulate a full plan to graduation with zero warnings — this is the
    'back-reference 4 years' guarantee: whichever year a student started,
    the plan they get must actually be gradable."""

    def test_all_years_load_and_graduate_cleanly(self):
        import datetime
        for major in ("CMPSC", "PREMED"):
            for year in (2022, 2023, 2024, 2025, 2026):
                with self.subTest(major=major, year=year):
                    plan = engine.load_degree_plan(major, year)
                    self.assertIsNotNone(plan, f"{major}-{year}.json failed to load")
                    self.assertEqual(plan["catalog_year"], year, f"{major} loaded the wrong year for {year}")
                    catalog = engine.load_merged_catalog(plan["departments"])
                    fp = engine.build_full_plan(
                        plan, catalog, set(),
                        start_year=year, grad_years=4,
                        today=datetime.date(year, 7, 1),
                    )
                    self.assertEqual(fp["warnings"], [], f"{major}-{year} has warnings: {fp['warnings']}")
                    self.assertTrue(fp["goal"]["met"], f"{major}-{year} did not graduate in 4 years")

    def test_chat_start_year_selects_correct_historical_plan(self):
        """End-to-end: a chat-stated start year must load THAT year's real
        degree plan now that the historical files exist."""
        r = self.client.post("/api/plan", json={
            "prompt": "oh I started school in 2022. I've completed CMPSC 131.",
            "completed": [],
            "catalog_year": 2026, "start_year": 2026, "grad_years": 4,
        })
        d = r.get_json()
        self.assertEqual(d["state"]["startYear"], 2022)
        self.assertEqual(d["coursePlan"]["catalogYear"], 2022)

    def setUp(self):
        self.client = app.test_client()


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


class TestSemesterFlowchart(unittest.TestCase):
    def setUp(self):
        import datetime
        self.plan, self.catalog = _plan_and_catalog()
        self.today = datetime.date(2026, 7, 1)

    def test_valid_mermaid_shape_and_color_classes(self):
        completed = {"CMPSC 131", "MATH 140"}
        fp = engine.build_full_plan(
            self.plan, self.catalog, completed,
            start_year=2026, grad_years=4, today=self.today,
        )
        sf = engine.build_semester_flowchart(self.catalog, completed, fp["terms"])
        mm = sf["mermaid"]
        self.assertTrue(mm.startswith("flowchart"))
        self.assertIn('subgraph SEM_DONE["Completed"]', mm)
        self.assertIn("classDef done fill", mm)
        self.assertIn("classDef next fill", mm)
        self.assertIn("classDef future fill", mm)
        # Completed courses must land in the green "done" class.
        self.assertRegex(mm, r"class [^\n]*N_CMPSC_131[^\n]* done")
        self.assertRegex(mm, r"class [^\n]*N_MATH_140[^\n]* done")
        # The very next term's courses must land in the red "next" class,
        # not grey — this is the whole point of the 3-tier color scheme.
        first_term_codes = [p["code"] for p in fp["terms"][0]["courses"] if p["code"]]
        self.assertTrue(first_term_codes)
        for code in first_term_codes:
            node_id = f"N_{code.replace(' ', '_')}"
            self.assertRegex(mm, rf"class [^\n]*{re.escape(node_id)}[^\n]* next")

    def test_edge_count_matches_linkstyle_count(self):
        completed = {"CMPSC 131", "MATH 140"}
        fp = engine.build_full_plan(
            self.plan, self.catalog, completed,
            start_year=2026, grad_years=4, today=self.today,
        )
        sf = engine.build_semester_flowchart(self.catalog, completed, fp["terms"])
        mm = sf["mermaid"]
        n_edges = len(re.findall(r"^\w+ --> \w+$", mm, re.MULTILINE))
        n_linkstyles = len(re.findall(r"^linkStyle \d+ stroke:", mm, re.MULTILINE))
        self.assertEqual(n_edges, n_linkstyles)
        self.assertGreater(n_edges, 0, "expected at least one prereq arrow in a multi-term plan")

    def test_empty_state(self):
        sf = engine.build_semester_flowchart(self.catalog, set(), [])
        self.assertTrue(sf["mermaid"].startswith("flowchart"))
        self.assertNotIn("SEM_DONE", sf["mermaid"])


class TestNursingPlan(unittest.TestCase):
    """Nursing, B.S.N. (General Nursing Option) — built the same way as
    CMPSC/Premed. Surfaced three real scraper bugs in restricted-enrollment
    NURS course prereqs (see Courseplanner._BOUNDARY_RE) plus two more
    placement-level prereq gaps (CHEM 130, STAT 200)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("NURS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a nursing major", "I'm in the Nursing program"):
            self.assertEqual(_extract_major_from_prompt(phrase), "NURS", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_restricted_enrollment_sequence_respects_prereqs(self):
        """NURS 230 needs NURS 250+251 first; NURS 301 needs NURS 225+230
        first; NURS 480 needs NURS 405A first — regression test for the
        'Recommended Corequisite:' / bare 'enforced concurrent' scraper bugs
        that previously corrupted these into wrong same-term blocks."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for i, t in enumerate(fp["terms"]):
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = i
        for pre, course in [
            ("NURS 250", "NURS 230"), ("NURS 251", "NURS 230"),
            ("NURS 225", "NURS 301"), ("NURS 230", "NURS 301"),
            ("NURS 405A", "NURS 480"),
        ]:
            if pre in term_of and course in term_of:
                self.assertLessEqual(term_of[pre], term_of[course], f"{course} scheduled before {pre}")


class TestEnglishPlan(unittest.TestCase):
    """English, B.A. (Traditions of Innovation option) — mostly open
    electives/concentration slots rather than fixed course chains, unlike
    CMPSC/Premed/Nursing, so the main thing worth locking in is that the
    open-elective-heavy plan still reaches graduation cleanly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ENGL", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an English major"), "ENGL")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)


class TestBusinessPlan(unittest.TestCase):
    """Business, B.S. (Intercollege, Accounting option) — the only major so
    far with no University Park offering (Commonwealth Campuses/World
    Campus only). Surfaced a placement-level cascade: MATH 21 itself
    required MATH 4 (remedial), which blocked ACCTG 211, which blocked
    everything downstream of it."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("BUSINESS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a business major"), "BUSINESS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_accounting_sequence_respects_prereqs(self):
        """ACCTG 472/403 need ACCTG 471 first; BA 420/421 need BA 321+322 —
        regression test for the MATH 21 -> MATH 4 placement cascade."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for i, t in enumerate(fp["terms"]):
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = i
        for pre, course in [
            ("ACCTG 471", "ACCTG 472"), ("ACCTG 471", "ACCTG 403"),
            ("MATH 21", "ACCTG 211"),
        ]:
            if pre in term_of and course in term_of:
                self.assertLessEqual(term_of[pre], term_of[course], f"{course} scheduled before {pre}")


class TestPremedPlan(unittest.TestCase):
    """Premedicine, B.S. — built the same way as CMPSC (real bulletin data,
    deterministic engine, no LLM in the eligibility path)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PREMED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am premed", "I'm a pre-med student", "premedicine major"):
            self.assertEqual(_extract_major_from_prompt(phrase), "PREMED", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)  # matches PSU's official 8-semester plan

    def test_physics_sequence_respects_prereqs(self):
        """PHYS 213/214 require PHYS 211/212 — must never be scheduled early
        just because MATH 140 alone is done (regression test for the AND vs
        OR prereq-group parsing bug)."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for i, t in enumerate(fp["terms"]):
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = i
        for pre, course in [("PHYS 211", "PHYS 212"), ("PHYS 211", "PHYS 213"), ("PHYS 212", "PHYS 214")]:
            if pre in term_of and course in term_of:
                self.assertLessEqual(term_of[pre], term_of[course], f"{course} scheduled before {pre}")

    def test_chem_lab_pairs_with_lecture_same_term(self):
        """CHEM 111/113 are 'prerequisite or concurrent' with their lecture
        pairing — must not be pushed a full term later than necessary."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        codes_by_term = [
            {p["code"] for p in t["courses"] if p["code"]} for t in fp["terms"]
        ]
        term1 = codes_by_term[0]
        self.assertIn("CHEM 110", term1)
        self.assertIn("CHEM 111", term1)

    def test_semester_flowchart_renders_for_premed(self):
        """The semester-flowchart toggle (built after Premed's initial
        setup, generic across majors) must work for Premed data too, not
        just the CMPSC data it was originally tested against."""
        completed = {"BIOL 110", "CHEM 110", "CHEM 111", "MATH 140", "ENGL 15"}
        fp = engine.build_full_plan(
            self.plan, self.catalog, completed,
            start_year=2026, grad_years=4, today=self.today,
        )
        sf = engine.build_semester_flowchart(self.catalog, completed, fp["terms"])
        mm = sf["mermaid"]
        self.assertTrue(mm.startswith("flowchart"))
        self.assertRegex(mm, r"class [^\n]*N_BIOL_110[^\n]* done")
        self.assertRegex(mm, r"class [^\n]*N_MATH_140[^\n]* done")
        first_term_codes = [p["code"] for p in fp["terms"][0]["courses"] if p["code"]]
        self.assertTrue(first_term_codes)
        for code in first_term_codes:
            node_id = f"N_{code.replace(' ', '_')}"
            self.assertRegex(mm, rf"class [^\n]*{re.escape(node_id)}[^\n]* next")


class TestApiShape(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"status": "ok"})

    def test_transfer_credit_distance_only_for_uncovered_course(self):
        # MATH 140 has no cached equivalency yet — distance-only, with a note.
        r = self.client.post("/api/transfer-credit", json={
            "zip_code": "19104", "courses": ["MATH 140"],
        })
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["colleges"])
        self.assertTrue(all(c["courses_covered_count"] == 0 for c in d["colleges"]))
        distances = [c["distance_miles"] for c in d["colleges"]]
        self.assertEqual(distances, sorted(distances))

    def test_transfer_credit_real_engl15_equivalency(self):
        # Real record confirmed via a LionPATH PDF export (2026-07-18):
        # Delaware County CCC's ENG 100 transfers as PSU's ENGL 15. It must
        # rank first even though Community College of Philadelphia is closer
        # — course coverage outranks distance.
        r = self.client.post("/api/transfer-credit", json={
            "zip_code": "19104", "courses": ["ENGL 15"],
        })
        d = r.get_json()
        self.assertTrue(d["equivalencyDataAvailable"])
        top = d["colleges"][0]
        self.assertEqual(top["institution_id"], "100123622")
        self.assertEqual(top["courses_covered"], ["ENGL 15"])
        ccp = next(c for c in d["colleges"] if c["name"] == "Community College of Philadelphia")
        self.assertLess(ccp["distance_miles"], top["distance_miles"])  # closer, but 0 coverage

    def test_transfer_credit_rejects_out_of_scope_zip(self):
        r = self.client.post("/api/transfer-credit", json={"zip_code": "90210"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_transfer_credit_requires_zip(self):
        r = self.client.post("/api/transfer-credit", json={})
        self.assertEqual(r.status_code, 400)

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


class TestTransferCredit(unittest.TestCase):
    """PA community college distance ranking (real Census-derived zip data)
    and the equivalency-cache scaffold (synthetic data — the real LionPATH
    scraper isn't built yet; see docs/EXPANSION_PLAN.md §5)."""

    def test_haversine_known_distance(self):
        # Philadelphia to Pittsburgh is ~260 miles straight-line.
        philly = (39.9526, -75.1652)
        pittsburgh = (40.4406, -79.9959)
        d = tc.haversine_miles(*philly, *pittsburgh)
        self.assertGreater(d, 230)
        self.assertLess(d, 290)
        self.assertEqual(tc.haversine_miles(39.9, -75.1, 39.9, -75.1), 0)

    def test_zip_lookup(self):
        # 19063 is Delaware County CCC's own zip — should resolve close to
        # the college's stored coordinates.
        coords = tc.zip_to_coords("19063")
        self.assertIsNotNone(coords)
        college = next(c for c in tc.load_community_colleges() if c["zip"] == "19063")
        self.assertLess(tc.haversine_miles(*coords, college["lat"], college["lng"]), 5)

    def test_zip_outside_pa_returns_none(self):
        # 90210 (Beverly Hills, CA) — out of the current PA-only scope.
        self.assertIsNone(tc.zip_to_coords("90210"))

    def test_nearest_colleges_sorted_and_philly_is_close(self):
        ranked = tc.nearest_colleges("19104")  # University City, Philadelphia
        self.assertTrue(ranked)
        distances = [c["distance_miles"] for c in ranked]
        self.assertEqual(distances, sorted(distances))
        names = [c["name"] for c in ranked[:3]]
        self.assertIn("Community College of Philadelphia", names)

    def test_nearest_colleges_unknown_zip(self):
        self.assertEqual(tc.nearest_colleges("00000"), [])

    def test_soonest_expiring_excludes_open_ended_and_sorts(self):
        cache = {
            "ENGL 15": [
                {"institution_id": "A", "expiry_date": "2028-05-01"},
                {"institution_id": "B", "expiry_date": None},
            ],
            "MATH 140": [
                {"institution_id": "C", "expiry_date": "2027-01-01"},
            ],
        }
        result = tc.soonest_expiring(cache, limit=10)
        self.assertEqual([r["institution_id"] for r in result], ["C", "A"])

    def test_soonest_expiring_against_real_cache(self):
        # The real Delaware County CCC ENGL 15 record expires 2027-09-03 —
        # it must surface here so refresh scheduling actually picks it up.
        result = tc.soonest_expiring(tc.load_equivalency_cache(), limit=10)
        self.assertTrue(any(r["expiry_date"] == "2027-09-03" for r in result))

    def test_rank_colleges_prioritizes_course_coverage_over_distance(self):
        cache = {
            "ENGL 15": [{
                "institution_id": "100123622",  # Delaware County CCC
                "institution_name": "Delaware County Community College",
                "transfer_course_code": "ENG 101",
                "transfer_course_title": "English Composition",
                "credits": 3,
                "effective_date": "2020-01-01",
                "expiry_date": None,
                "scraped_at": "2026-07-18",
            }]
        }
        ranked = tc.rank_colleges_for_courses("19104", ["ENGL 15"], cache=cache)
        self.assertTrue(ranked)
        top = ranked[0]
        self.assertEqual(top["institution_id"], "100123622")
        self.assertEqual(top["courses_covered_count"], 1)
        # A closer-but-non-covering college must not outrank it.
        for c in ranked[1:]:
            self.assertLessEqual(c["courses_covered_count"], top["courses_covered_count"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
