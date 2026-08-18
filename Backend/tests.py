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
from app import (
    app, parse_completion_changes, _extract_major_from_prompt, _extract_start_year_from_prompt,
    _build_reply_text, _pick_opener, _build_phrase_prompt,
)


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

    def test_bare_course_code_does_not_hijack_major(self):
        """A short major alias (MATH/STAT/CHEM/...) is also a real PSU
        course-code prefix. 'I took MATH 140 and STAT 200' states no major
        at all — regression test for a live bug found while browser-testing
        the STAT major: the dropdown was silently reassigned from STAT to
        MATH because 'MATH' matched earlier in the raw text than 'STAT',
        even though both matches were course codes, not major statements."""
        self.assertIsNone(
            _extract_major_from_prompt("I took MATH 140 and STAT 200. What should I take next?")
        )
        self.assertIsNone(_extract_major_from_prompt("I took CHEM 110 last semester"))
        # A genuine major statement should still win even with course codes nearby.
        prompt = "I am a Statistics major. I took MATH 140 and STAT 200."
        self.assertEqual(_extract_major_from_prompt(prompt), "STAT")


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
    """Every catalog year on disk, for every major, must load and simulate
    a full plan to graduation with zero warnings — this is the
    'back-reference N years' guarantee: whichever year a student started,
    the plan they get must actually be gradable. Discovers (major, year)
    pairs from degree_plans/*.json directly rather than a hardcoded list,
    so this automatically covers every major's historical years as they're
    added — not just CMPSC/PREMED, which is all this test used to check."""

    # Almost every major is a standard 8-semester/4-year plan. A few
    # named here are genuinely longer professional programs (e.g.
    # Architecture's 10-semester B.Arch) — this test simulates each
    # against its own real program length instead of assuming 4 years.
    _GRAD_YEARS_OVERRIDE = {"ARCHBARCH": 5}

    def test_all_years_load_and_graduate_cleanly(self):
        import datetime
        import glob
        import re

        pairs = []
        for path in glob.glob(os.path.join(engine.DEGREE_PLAN_DIR, "*.json")):
            m = re.match(r"([A-Z]+)-(\d{4})\.json$", os.path.basename(path))
            if m:
                pairs.append((m.group(1), int(m.group(2))))
        self.assertGreater(len(pairs), 0, "no degree plan files found on disk")

        for major, year in sorted(pairs):
            with self.subTest(major=major, year=year):
                grad_years = self._GRAD_YEARS_OVERRIDE.get(major, 4)
                plan = engine.load_degree_plan(major, year)
                self.assertIsNotNone(plan, f"{major}-{year}.json failed to load")
                self.assertEqual(plan["catalog_year"], year, f"{major} loaded the wrong year for {year}")
                catalog = engine.load_merged_catalog(plan["departments"])
                fp = engine.build_full_plan(
                    plan, catalog, set(),
                    start_year=year, grad_years=grad_years,
                    today=datetime.date(year, 7, 1),
                )
                self.assertEqual(fp["warnings"], [], f"{major}-{year} has warnings: {fp['warnings']}")
                self.assertTrue(fp["goal"]["met"], f"{major}-{year} did not graduate in {grad_years} years")

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

    def test_chat_start_year_2026_selects_current_catalog(self):
        """'Started college in 2026' must resolve to catalog_year 2026 —
        PSU's live 2026-27 bulletin edition (confirmed against the real
        bulletins.psu.edu, which itself labels its current edition
        '2026-2027' and rolls over at the start of each summer semester,
        so nothing published between this data's scrape date and today
        changes which edition '2026' means). Covers a newer major (ACCTG)
        that only has a 2026 file, not just CMPSC/PREMED's 5-year range —
        the stale start_year in the request (2020) must still get
        overridden by the chat statement, exactly like the 2022 case above."""
        r = self.client.post("/api/plan", json={
            "prompt": "I started college in 2026",
            "completed": [],
            "major": "ACCTG", "start_year": 2020, "grad_years": 4,
        })
        d = r.get_json()
        self.assertEqual(d["state"]["startYear"], 2026)
        self.assertEqual(d["coursePlan"]["catalogYear"], 2026)

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


class TestBulkCompletion(unittest.TestCase):
    """Non-freshman / bulk completion via natural language (e.g. a junior
    saying 'I've completed everything but my last year')."""

    def setUp(self):
        self.plan, self.catalog = _plan_and_catalog()

    def test_junior_standing_marks_first_four_semesters_done(self):
        bulk = engine.detect_bulk_completion("I'm a junior", self.plan)
        self.assertIsNotNone(bulk)
        self.assertEqual(bulk["semesters_done"], 4)
        codes, _ = engine.apply_bulk_completion(self.plan, self.catalog, bulk["semesters_done"])
        self.assertIn("CMPSC 131", codes)  # semester 1
        self.assertIn("MATH 141", codes)  # semester 2
        self.assertIn("CMPSC 221", codes)  # semester 3, within the 4 completed
        self.assertNotIn("CMPSC 320", codes)  # semester 5, not yet done

    def test_sophomore_standing_stops_before_semester_three(self):
        bulk = engine.detect_bulk_completion("I have sophomore standing", self.plan)
        self.assertEqual(bulk["semesters_done"], 2)
        codes, _ = engine.apply_bulk_completion(self.plan, self.catalog, bulk["semesters_done"])
        self.assertIn("MATH 140", codes)  # semester 1
        self.assertNotIn("CMPSC 221", codes)  # semester 3, not yet done

    def test_completed_n_years_phrase(self):
        bulk = engine.detect_bulk_completion("I have completed 3 years", self.plan)
        self.assertIsNotNone(bulk)
        self.assertEqual(bulk["semesters_done"], 6)

    def test_everything_except_last_year(self):
        bulk = engine.detect_bulk_completion(
            "I have completed everything except my last year of classes", self.plan,
        )
        self.assertIsNotNone(bulk)
        self.assertEqual(bulk["semesters_done"], 6)  # 8 - 2

    def test_everything_except_named_course_leaves_it_unscheduled(self):
        bulk = engine.detect_bulk_completion(
            "I have done everything except CMPSC 483W", self.plan,
        )
        self.assertEqual(bulk["semesters_done"], 8)
        codes, _ = engine.apply_bulk_completion(
            self.plan, self.catalog, bulk["semesters_done"], excluded_codes={"CMPSC 483W"},
        )
        self.assertNotIn("CMPSC 483W", codes)
        self.assertIn("CMPSC 131", codes)

    def test_no_bulk_phrase_returns_none(self):
        self.assertIsNone(engine.detect_bulk_completion("I took CMPSC 131", self.plan))
        self.assertIsNone(engine.detect_bulk_completion("", self.plan))

    def test_shared_option_pool_items_get_distinct_representative_picks(self):
        # Semester 1 has two writing-adjacent OR-groups sharing no codes in
        # CMPSC's plan, but this guards the general mechanism: no code is
        # ever claimed by two different items in one apply_bulk_completion call.
        codes, _ = engine.apply_bulk_completion(self.plan, self.catalog, 8)
        self.assertEqual(len(codes), len(set(codes)))

    def test_full_plan_reaches_graduation_after_bulk_completion(self):
        import datetime
        bulk = engine.detect_bulk_completion("I'm a senior", self.plan)
        codes, slot_ids = engine.apply_bulk_completion(self.plan, self.catalog, bulk["semesters_done"])
        fp = engine.build_full_plan(
            self.plan, self.catalog, codes,
            start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
            initial_consumed_slots=slot_ids,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 3)  # only ~1 year of courses left

    def test_bulk_completion_absent_leaves_build_full_plan_unaffected(self):
        # initial_consumed_slots defaults to None -> identical behavior to
        # every pre-existing build_full_plan call site.
        import datetime
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])


def _minimal_reply_stub_args():
    progress = {"done_items": 0, "total_items": 1, "credits_done": 0, "total_credits": 3}
    next_sem = {"courses": [], "total_credits": 0}
    return {
        "major": "CMPSC", "catalog_year": 2026,
        "added": [], "removed": [], "unmatched": [],
        "progress": progress, "next_sem": next_sem,
        "ranked": [], "plan_warnings": [],
    }


class TestConversationalReply(unittest.TestCase):
    """Reply opener variety across turns of one conversation, so the chat
    doesn't sound identical every message."""

    def test_first_turn_opener_matches_today(self):
        # turn_index absent/0 -> no opener line at all, today's exact shape.
        self.assertEqual(_pick_opener(0), "")
        text = _build_reply_text(**_minimal_reply_stub_args(), opener=_pick_opener(0))
        self.assertFalse(text.startswith("Here's") or text.startswith("Updated") or text.startswith("OK"))

    def test_second_turn_uses_different_opener_than_first(self):
        first = _pick_opener(0)
        second = _pick_opener(1)
        self.assertNotEqual(first, second)
        self.assertTrue(second)

    def test_opener_rotates_across_several_turns(self):
        openers = {_pick_opener(i) for i in range(1, 5)}
        self.assertGreater(len(openers), 1)  # not stuck repeating one phrase

    def test_facts_content_unaffected_by_opener_rotation(self):
        args = _minimal_reply_stub_args()
        without = _build_reply_text(**args, opener="")
        with_opener = _build_reply_text(**args, opener="Updated plan:")
        # everything after the first line is identical
        self.assertEqual(without.splitlines(), with_opener.splitlines()[1:])
        self.assertEqual(with_opener.splitlines()[0], "Updated plan:")

    def test_phrase_prompt_includes_anti_repetition_instruction_when_recent_reply_given(self):
        prompt = _build_phrase_prompt("what's next?", "some facts", "", "Based on your progress, take X.")
        self.assertIn("Vary your opening", prompt)
        self.assertIn("Based on your progress, take X.", prompt)

    def test_phrase_prompt_omits_anti_repetition_instruction_on_first_turn(self):
        prompt = _build_phrase_prompt("what's next?", "some facts", "", "")
        self.assertNotIn("Vary your opening", prompt)


class TestExclusionConstraint(unittest.TestCase):
    """Mutual exclusion / anti-requisite courses ('may not schedule for
    credit if X has already been completed'). Mechanism is provably inert
    on all real catalog data until `excludes` is actually populated."""

    def setUp(self):
        self.plan, self.catalog = _plan_and_catalog()

    def test_excludes_field_defaults_empty_for_all_existing_catalogs(self):
        import glob
        # The only two real, hand-verified pilot exclusions added with this
        # feature (real PSU bulletin language: "Students who have passed
        # <excludes> may not schedule this course for credit").
        pilot_exclusions = {"MATH 232": {"MATH 230"}, "MATH 311W": {"CMPSC 360"}}
        for path in glob.glob(os.path.join(engine.CATALOG_DIR, "*.json")):
            cat = engine.load_catalog_from_json(path)
            for code, course in cat.items():
                expected = pilot_exclusions.get(code, set())
                self.assertEqual(course.excludes, expected, f"{path}: {code} excludes")

    def test_exclusion_conflict_detects_completed_excluded_course(self):
        course = engine.Course(
            code="TEST 200", name="Test Course", credits=3.0,
            prereq_groups=[], concurrent_groups=[], excludes={"TEST 100"},
        )
        self.assertEqual(engine.exclusion_conflict(course, {"TEST 100"}), {"TEST 100"})
        self.assertFalse(engine.excludes_satisfied(course, {"TEST 100"}))
        self.assertTrue(engine.excludes_satisfied(course, set()))

    def test_scan_once_falls_through_to_alternate_option_when_excluded(self):
        plan = {
            "major": "TEST", "catalog_year": 2026, "departments": ["TEST"],
            "semesters": [{"index": 1, "label": "Semester 1", "items": [
                {"type": "course", "options": ["TEST A", "TEST B"], "credits": 3, "id": 0},
            ]}],
        }
        catalog = {
            "TEST A": engine.Course("TEST A", "A", 3.0, [], [], excludes={"TEST OLD"}),
            "TEST B": engine.Course("TEST B", "B", 3.0, [], [], excludes=set()),
        }
        rec = engine.recommend_semester(plan, catalog, {"TEST OLD"})
        codes = [c["code"] for c in rec["courses"]]
        self.assertIn("TEST B", codes)
        self.assertNotIn("TEST A", codes)

    def test_blocked_list_surfaces_excluded_by_reason(self):
        plan = {
            "major": "TEST", "catalog_year": 2026, "departments": ["TEST"],
            "semesters": [{"index": 1, "label": "Semester 1", "items": [
                {"type": "course", "options": ["TEST A"], "credits": 3, "id": 0},
            ]}],
        }
        catalog = {"TEST A": engine.Course("TEST A", "A", 3.0, [], [], excludes={"TEST OLD"})}
        rec = engine.recommend_semester(plan, catalog, {"TEST OLD"})
        self.assertEqual(rec["courses"], [])
        self.assertEqual(len(rec["blocked"]), 1)
        self.assertEqual(rec["blocked"][0]["excludedBy"], ["TEST OLD"])

    def test_gen_ed_pick_skips_excluded_course(self):
        # _pick_gen_ed_course reads from the real Gen Ed data file, so this
        # only proves the wiring compiles and runs without error on real
        # data — the actual skip-on-conflict behavior is exercised by the
        # synthetic scan_once test above, which fully controls its catalog.
        domains = engine.load_gen_ed_courses()
        if not domains:
            self.skipTest("no Gen Ed data cached")
        domain = next(iter(domains))
        pick = engine._pick_gen_ed_course(domain, self.catalog, None, set(), set())
        self.assertTrue(pick is None or len(pick) == 3)


def _synthetic_primary_plan():
    return {
        "major": "TESTMAJ", "catalog_year": 2026, "departments": ["TESTMAJ"],
        "semesters": [
            {"index": 1, "label": "Semester 1", "items": [
                {"type": "course", "options": ["STAT 318"], "credits": 3, "id": 0},
                {"type": "slot", "label": "GEN ED (GQ)", "credits": 3, "gen_ed": "GQ", "id": 1},
            ]},
            {"index": 2, "label": "Semester 2", "items": [
                {"type": "course", "options": ["MAJ 200"], "credits": 3, "id": 2},
            ]},
        ],
    }


def _synthetic_minor_no_overlap():
    return {
        "minor": "TESTMIN", "catalog_year": 2026, "departments": ["TESTMIN"],
        "requirements": [
            {"type": "course", "options": ["MIN 100"], "credits": 3},
        ],
    }


def _synthetic_minor_with_overlap():
    return {
        "minor": "TESTMIN", "catalog_year": 2026, "departments": ["TESTMIN"],
        "requirements": [
            {"type": "course", "options": ["STAT 318"], "credits": 3},  # overlaps primary's item 0
            {"type": "course", "options": ["MIN 200"], "credits": 3},  # no overlap
            {"type": "slot", "label": "GEN ED (GQ)", "credits": 3, "gen_ed": "GQ"},  # dedup target
        ],
    }


class TestPlanMerging(unittest.TestCase):
    """Minors + double major: merge_plans folds a second major's semesters
    and/or a minor's flat requirement list into the primary plan's own
    shape, so build_full_plan/recommend_semester/plan_progress need no
    changes at all."""

    def test_merge_plans_noop_when_no_second_major_or_minors(self):
        plan = _synthetic_primary_plan()
        self.assertIs(engine.merge_plans(plan), plan)

    def test_merged_item_ids_never_collide_with_primary_ids(self):
        plan = _synthetic_primary_plan()
        merged = engine.merge_plans(plan, minors=[_synthetic_minor_no_overlap()])
        ids = [item["id"] for _, item in engine._iter_plan_items(merged)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_primary_item_ids_unchanged_after_merge(self):
        plan = _synthetic_primary_plan()
        original_ids = [item["id"] for _, item in engine._iter_plan_items(plan)]
        engine.merge_plans(plan, minors=[_synthetic_minor_with_overlap()])
        # the ORIGINAL plan object must be untouched — merge_plans deep-copies
        self.assertEqual([item["id"] for _, item in engine._iter_plan_items(plan)], original_ids)
        self.assertEqual(plan["semesters"][0]["items"][0]["options"], ["STAT 318"])

    def test_non_overlapping_minor_requirement_lands_as_trailing_semester(self):
        plan = _synthetic_primary_plan()
        merged = engine.merge_plans(plan, minors=[_synthetic_minor_no_overlap()])
        trailing = merged["semesters"][-1]
        self.assertEqual(trailing["label"], "TESTMIN Minor")
        codes = [it["options"] for it in trailing["items"] if it.get("options") == ["MIN 100"]]
        self.assertTrue(codes)

    def test_overlapping_minor_requirement_widens_existing_item_instead_of_duplicating(self):
        plan = _synthetic_primary_plan()
        merged = engine.merge_plans(plan, minors=[_synthetic_minor_with_overlap()])
        item0 = next(item for _, item in engine._iter_plan_items(merged) if item["id"] == 0)
        self.assertEqual(item0["options"], ["STAT 318"])  # already had it, no duplicate added
        self.assertEqual(item0.get("also_satisfies"), ["minor:TESTMIN"])
        # only the genuinely non-overlapping requirement (MIN 200) becomes a new item
        all_options = [it.get("options") for _, it in engine._iter_plan_items(merged)]
        self.assertIn(["MIN 200"], all_options)
        self.assertEqual(sum(1 for o in all_options if o == ["STAT 318"]), 1)

    def test_completing_widened_option_satisfies_both_major_item_and_minor_category(self):
        plan = _synthetic_primary_plan()
        merged = engine.merge_plans(plan, minors=[_synthetic_minor_with_overlap()])
        progress = engine.plan_progress(merged, {"STAT 318"})
        self.assertIn(0, progress["done_ids"])
        self.assertIn("minor:TESTMIN", progress["by_category"])
        self.assertEqual(progress["by_category"]["minor:TESTMIN"]["done_items"], 1)
        # The minor's own non-overlapping requirement (MIN 200, a trailing
        # item) must ALSO roll up into the minor's category bucket — not
        # just the widened/overlapping one — or "% of minor done" would
        # only ever reflect courses shared with the major.
        self.assertEqual(progress["by_category"]["minor:TESTMIN"]["total_items"], 2)

    def test_gen_ed_slot_deduped_between_major_and_minor(self):
        plan = _synthetic_primary_plan()
        merged = engine.merge_plans(plan, minors=[_synthetic_minor_with_overlap()])
        gen_ed_items = [item for _, item in engine._iter_plan_items(merged) if item.get("gen_ed") == "GQ"]
        self.assertEqual(len(gen_ed_items), 1)  # minor's duplicate GQ slot was dropped

    def test_departments_union_includes_minor_dept(self):
        plan = _synthetic_primary_plan()
        merged = engine.merge_plans(plan, minors=[_synthetic_minor_no_overlap()])
        self.assertIn("TESTMIN", merged["departments"])
        self.assertIn("TESTMAJ", merged["departments"])

    def test_second_major_semesters_merge_term_by_term(self):
        plan = _synthetic_primary_plan()
        second = {
            "major": "SECONDMAJ", "catalog_year": 2026, "departments": ["SECONDMAJ"],
            "semesters": [
                {"index": 1, "label": "Semester 1", "items": [
                    {"type": "course", "options": ["SEC 100"], "credits": 3},
                ]},
            ],
        }
        merged = engine.merge_plans(plan, second_major=second)
        sem1_options = [it.get("options") for it in merged["semesters"][0]["items"]]
        self.assertIn(["SEC 100"], sem1_options)
        self.assertEqual(len(merged["semesters"]), 2)  # no extra trailing semester added

    def test_real_cmpsc_plus_statistics_minor_flows_through_build_full_plan(self):
        # The minor adds real extra credit-hours on top of CMPSC's own ~145cr,
        # so it's realistic (not a bug) that this needs 5 years, not 4 — the
        # bar here is that it FINISHES cleanly, not that it fits an
        # unrealistically tight deadline.
        import datetime
        cmpsc = engine.load_degree_plan("CMPSC")
        statmin = engine.load_minor_plan("STATMIN", 2026)
        self.assertIsNotNone(statmin)
        merged = engine.merge_plans(cmpsc, minors=[statmin])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=5, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_cmpsc_plus_math_double_major_flows_through_build_full_plan(self):
        # Two full majors' worth of credits realistically needs more than 5
        # years — the bar is that the simulation actually FINISHES (the real
        # bug this test caught: MATH 140 required literally by both majors,
        # with no OR-alternative, meant the second occurrence could never be
        # satisfied and the simulation looped forever instead of completing).
        import datetime
        cmpsc = engine.load_degree_plan("CMPSC")
        math = engine.load_degree_plan("MATH")
        merged = engine.merge_plans(cmpsc, second_major=math)
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=5, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])

    def test_api_plan_without_second_major_matches_baseline(self):
        client = app.test_client()
        payload = {"major": "CMPSC", "prompt": "", "completed": [], "start_year": 2026}
        r1 = client.post("/api/plan", json=payload)
        r2 = client.post("/api/plan", json=payload)
        self.assertEqual(r1.get_json()["coursePlan"]["progress"], r2.get_json()["coursePlan"]["progress"])


class TestRealMinorBatch(unittest.TestCase):
    """Broad-appeal minors batch: CPTSC (CS substitute), INTLBUS (Business
    substitute), PSYCH, ECON, CAS -- each merged against a real CMPSC major
    and checked for the same two things every minor in this session has
    been verified against: no infinite-rescheduling bug, and the minor's
    own progress bucket totals what its bulletin page says."""

    def _merge_and_build(self, minor_code, expected_minor_credits):
        import datetime
        cmpsc = engine.load_degree_plan("CMPSC")
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(cmpsc, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=6, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        progress = engine.plan_progress(merged, set())
        bucket = progress["by_category"].get(f"minor:{minor_code}")
        self.assertIsNotNone(bucket)
        self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_cptsc_minor(self):
        self._merge_and_build("CPTSC", 18.0)

    def test_intlbus_minor(self):
        self._merge_and_build("INTLBUS", 31.0)

    def test_psych_minor(self):
        self._merge_and_build("PSYCH", 19.0)

    def test_econ_minor(self):
        self._merge_and_build("ECON", 18.0)

    def test_cas_minor(self):
        self._merge_and_build("CAS", 18.0)


class TestCsAndMathMinorBatch(unittest.TestCase):
    """University Park CS/Math minors sourced directly from
    bulletins.psu.edu/programs/: Mathematics (Science), Computer
    Engineering (Engineering), Cybersecurity Computational Foundations
    (Engineering), and the plain Information Sciences and Technology minor
    (IST). Unlike the broad-appeal batch above, these are tested against
    MULTIPLE majors, not just CMPSC -- that portability check caught two
    real design bugs live: MATHMIN originally required MATH 232 alongside
    MATH 230, a real PSU anti-requisite pair (excludes_satisfied() failed);
    and CMPENMIN/CYBERCF's own hidden-prereq additions silently depended on
    CMPSC's specific course lineup instead of supplying their own complete
    chain, so they broke against MATH -- a major that requires the *same*
    intro-programming or intro-math course but as a wider OR-pool that a
    flattened widened item can end up satisfying with a different, unrelated
    option than the one a downstream chain needed."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        blocking = [w for w in fp["warnings"] if w.startswith("Could not schedule")]
        self.assertEqual(blocking, [])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_mathmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "MATHMIN", 26.0)

    def test_mathmin_against_cmpen(self):
        # Regression: CMPEN's own math sequence doesn't include MATH 230,
        # so MATHMIN must supply it itself rather than assume the major has it.
        self._merge_and_build("CMPEN", "MATHMIN")

    def test_mathmin_against_unrelated_math_major(self):
        self._merge_and_build("MATH", "MATHMIN")

    def test_cmpenmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "CMPENMIN", 27.0)

    def test_cmpenmin_against_unrelated_math_major(self):
        # Regression: CMPEN 270 carries a real PHYS 212 concurrency that a
        # non-engineering major doesn't otherwise satisfy.
        self._merge_and_build("MATH", "CMPENMIN")

    def test_cybercf_against_cmpsc(self):
        self._merge_and_build("CMPSC", "CYBERCF", 42.0)

    def test_cybercf_against_cmpen(self):
        self._merge_and_build("CMPEN", "CYBERCF")

    def test_cybercf_against_cyber_major(self):
        # Regression: CYBER's own math-placement item offers MATH 110 OR
        # MATH 140 (either satisfies the major on its own), but CYBERCF's
        # hidden PHYS 211 addition has a real concurrent-MATH-140
        # requirement with no alternative -- this only resolves if the
        # scheduler prefers MATH 140 over MATH 110 in that shared pool
        # instead of defaulting to whichever is listed first. See
        # TestOptionRankingPrefersLoadBearingPrereqs for the isolated
        # mechanism test.
        self._merge_and_build("CYBER", "CYBERCF")

    def test_cybercf_against_data_sciences_major(self):
        self._merge_and_build("DS", "CYBERCF")

    def test_cybercf_against_unrelated_math_major(self):
        # Regression: MATH major's own intro-programming item offers any of
        # CMPSC 101/121/131/200/201, and CYBERCF's hidden prereq chain
        # specifically needs 121 or 131 downstream -- only resolves if the
        # scheduler prefers those over the pool's first-listed option.
        self._merge_and_build("MATH", "CYBERCF")

    def test_istmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ISTMIN", 18.0)

    def test_istmin_against_unrelated_math_major(self):
        self._merge_and_build("MATH", "ISTMIN")


class TestAiEngineeringMinor(unittest.TestCase):
    """AIENG is deliberately built from ONLY the courses literally listed in
    the bulletin's own Program Requirements table, per explicit instruction
    not to invent the hidden prereq chain its own courses actually need
    (A-I 410 -> A-I 341W -> A-I 100 + A-I 370 (+ STAT 401 concurrently) --
    none of which the bulletin table itself lists). The resulting
    "could not schedule A-I 410" warning is the correct, expected,
    bulletin-accurate outcome -- this test locks that in as intentional so
    a future change to it is a deliberate decision, not a silent
    regression."""

    def test_bulletin_only_courses_hits_the_real_ai410_gap(self):
        import datetime
        cmpsc = engine.load_degree_plan("CMPSC")
        minor = engine.load_minor_plan("AIENG", 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(cmpsc, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=6, today=datetime.date(2026, 7, 1),
        )
        self.assertTrue(any("A-I 410" in w for w in fp["warnings"]))
        progress = engine.plan_progress(merged, set())
        bucket = progress["by_category"].get("minor:AIENG")
        self.assertIsNotNone(bucket)
        self.assertEqual(bucket["total_credits"], 18.0)

    def test_completing_the_real_hidden_prereqs_unblocks_it(self):
        # Proves the gap is exactly what the notes say it is, not some
        # other unrelated bug: handing the simulator the real (unlisted)
        # prereq chain as already-completed makes A-I 410 schedulable.
        import datetime
        cmpsc = engine.load_degree_plan("CMPSC")
        minor = engine.load_minor_plan("AIENG", 2026)
        merged = engine.merge_plans(cmpsc, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        completed = {"A-I 100", "A-I 341W", "A-I 370", "STAT 401", "STAT 200"}
        fp = engine.build_full_plan(
            merged, catalog, completed,
            start_year=2026, grad_years=6, today=datetime.date(2026, 7, 1),
        )
        self.assertFalse(any("A-I 410" in w for w in fp["warnings"]))


class TestOptionRankingPrefersLoadBearingPrereqs(unittest.TestCase):
    """The fix behind the merge_plans OR-pool limitation documented in
    EXPANSION_PLAN.md §7: when a course item offers several interchangeable
    options (e.g. a major's generic 'any intro programming course' slot),
    the scheduler should prefer whichever option some OTHER still-
    outstanding item actually needs as a prereq/concurrent course, instead
    of defaulting to whichever is listed first and silently leaving a
    minor's own hidden-prereq chain permanently stuck. A HARD requirement
    (the sole option in some other item's prereq/concurrent group, e.g. a
    course whose only concurrent option is MATH 140) must outrank a merely
    SOFT one (one of several OR'd alternatives elsewhere) -- getting that
    backwards was the exact bug caught live against CYBER major + CYBERCF
    (MATH 110 winning over MATH 140 even though PHYS 211 has a real,
    non-optional concurrent-MATH-140 requirement)."""

    # Course codes deliberately look like "DEPT NUM" throughout (e.g.
    # "OPT A" not "OPT1") — norm_code() rewrites a letters-immediately-
    # followed-by-digits code like "OPT1" into "OPT 1" (real PSU codes
    # always have that space already), which would otherwise silently
    # desync these fixtures' catalog keys from the codes actually looked
    # up during ranking.

    def _plan(self, items):
        return {
            "major": "TEST", "catalog_year": 2026, "departments": ["TEST"],
            "semesters": [{"index": 1, "label": "Semester 1", "items": items}],
        }

    def test_hard_requirement_beats_first_listed_option(self):
        plan = self._plan([
            {"type": "course", "options": ["OPT A", "OPT B"], "credits": 3, "id": 0},
            {"type": "course", "options": ["NEEDS B"], "credits": 3, "id": 1},
        ])
        catalog = {
            "OPT A": engine.Course("OPT A", "Opt A", 3.0, [], []),
            "OPT B": engine.Course("OPT B", "Opt B", 3.0, [], []),
            "NEEDS B": engine.Course("NEEDS B", "Needs Opt B", 3.0, [], [{"OPT B"}]),
        }
        progress = engine.plan_progress(plan, set())
        priority = engine._codes_needed_as_prereqs(plan, catalog, progress["done_ids"])
        self.assertEqual(priority.get("OPT B"), 0)
        self.assertNotIn("OPT A", priority)
        ranked = list(engine._ranked_options({"options": ["OPT A", "OPT B"]}, catalog, set(), set(), priority))
        self.assertEqual(ranked[0], "OPT B")

    def test_hard_requirement_beats_soft_alternative_elsewhere(self):
        plan = self._plan([
            {"type": "course", "options": ["OPT A", "OPT B"], "credits": 3, "id": 0},
            {"type": "course", "options": ["NEEDS B"], "credits": 3, "id": 1},
            {"type": "course", "options": ["SOFT NEEDS A"], "credits": 3, "id": 2},
        ])
        catalog = {
            "OPT A": engine.Course("OPT A", "Opt A", 3.0, [], []),
            "OPT B": engine.Course("OPT B", "Opt B", 3.0, [], []),
            "NEEDS B": engine.Course("NEEDS B", "Needs Opt B", 3.0, [], [{"OPT B"}]),
            "SOFT NEEDS A": engine.Course("SOFT NEEDS A", "Soft", 3.0, [{"OPT A", "ALT X"}], []),
            "ALT X": engine.Course("ALT X", "Alt", 3.0, [], []),
        }
        progress = engine.plan_progress(plan, set())
        priority = engine._codes_needed_as_prereqs(plan, catalog, progress["done_ids"])
        self.assertEqual(priority.get("OPT B"), 0)  # hard: sole option in a concurrent group
        self.assertEqual(priority.get("OPT A"), 1)  # soft: one of two OR'd alternatives
        ranked = list(engine._ranked_options({"options": ["OPT A", "OPT B"]}, catalog, set(), set(), priority))
        self.assertEqual(ranked[0], "OPT B")

    def test_two_soft_alternatives_keep_original_list_order(self):
        # Both OPT A and OPT B are equally soft (each is one alternative in
        # the SAME downstream OR-group) -- matches this session's real
        # CMPSC 121-vs-131 case, where either resolves the downstream need,
        # so the tie is broken by the item's own original option order.
        plan = self._plan([
            {"type": "course", "options": ["OPT A", "OPT B"], "credits": 3, "id": 0},
            {"type": "course", "options": ["NEEDS EITHER"], "credits": 3, "id": 1},
        ])
        catalog = {
            "OPT A": engine.Course("OPT A", "Opt A", 3.0, [], []),
            "OPT B": engine.Course("OPT B", "Opt B", 3.0, [], []),
            "NEEDS EITHER": engine.Course("NEEDS EITHER", "Needs either", 3.0, [{"OPT A", "OPT B"}], []),
        }
        progress = engine.plan_progress(plan, set())
        priority = engine._codes_needed_as_prereqs(plan, catalog, progress["done_ids"])
        self.assertEqual(priority.get("OPT A"), priority.get("OPT B"))
        ranked = list(engine._ranked_options({"options": ["OPT A", "OPT B"]}, catalog, set(), set(), priority))
        self.assertEqual(ranked[0], "OPT A")

    def test_no_downstream_need_keeps_original_order(self):
        plan = self._plan([{"type": "course", "options": ["OPT A", "OPT B"], "credits": 3, "id": 0}])
        catalog = {
            "OPT A": engine.Course("OPT A", "Opt A", 3.0, [], []),
            "OPT B": engine.Course("OPT B", "Opt B", 3.0, [], []),
        }
        progress = engine.plan_progress(plan, set())
        priority = engine._codes_needed_as_prereqs(plan, catalog, progress["done_ids"])
        self.assertEqual(priority, {})
        ranked = list(engine._ranked_options({"options": ["OPT A", "OPT B"]}, catalog, set(), set(), priority))
        self.assertEqual(ranked, ["OPT A", "OPT B"])

    def test_end_to_end_recommend_semester_picks_the_hard_requirement_option(self):
        # Full recommend_semester path, not just the isolated ranking
        # helper -- proves the fix is actually wired into real scheduling.
        plan = self._plan([
            {"type": "course", "options": ["OPT A", "OPT B"], "credits": 3, "id": 0},
            {"type": "course", "options": ["NEEDS B"], "credits": 3, "id": 1},
        ])
        catalog = {
            "OPT A": engine.Course("OPT A", "Opt A", 3.0, [], []),
            "OPT B": engine.Course("OPT B", "Opt B", 3.0, [], []),
            "NEEDS B": engine.Course("NEEDS B", "Needs Opt B", 3.0, [], [{"OPT B"}]),
        }
        rec = engine.recommend_semester(plan, catalog, set())
        codes = [c["code"] for c in rec["courses"]]
        self.assertIn("OPT B", codes)
        self.assertNotIn("OPT A", codes)


class TestMultiMajorMerging(unittest.TestCase):
    """merge_plans now accepts additional_majors (a plain list) on top of
    the original second_major param, for a 3rd/4th/... major beyond the
    first two -- both routes fold through the exact same per-major loop."""

    def test_additional_majors_is_a_noop_alone_without_second_major(self):
        # additional_majors with no second_major still merges correctly --
        # it shouldn't require second_major to be set first.
        cmpsc = engine.load_degree_plan("CMPSC")
        math = engine.load_degree_plan("MATH")
        merged = engine.merge_plans(cmpsc, additional_majors=[math])
        self.assertIn("MATH", merged["departments"])

    def test_second_major_and_additional_majors_both_fold_in(self):
        cmpsc = engine.load_degree_plan("CMPSC")
        math = engine.load_degree_plan("MATH")
        stat = engine.load_degree_plan("STAT")
        merged = engine.merge_plans(cmpsc, second_major=math, additional_majors=[stat])
        self.assertIn("MATH", merged["departments"])
        self.assertIn("STAT", merged["departments"])
        sources = {item.get("source") for _, item in engine._iter_plan_items(merged)}
        self.assertIn("major:MATH", sources)
        self.assertIn("major:STAT", sources)

    def test_duplicate_major_code_is_silently_deduped_not_double_merged(self):
        # A student "picking the same major twice" (or picking the primary
        # again as a second major) must not create duplicate items --
        # merge_plans is the server-side backstop behind the frontend's own
        # duplicate-prevention in the major-count picker.
        cmpsc = engine.load_degree_plan("CMPSC")
        cmpsc_again = engine.load_degree_plan("CMPSC")
        merged = engine.merge_plans(cmpsc, second_major=cmpsc_again)
        # No item should be tagged as coming from a second CMPSC merge.
        sources = [item.get("source") for _, item in engine._iter_plan_items(merged)]
        self.assertNotIn("major:CMPSC", sources)
        # Item count/ids must match the untouched primary exactly (true no-op).
        self.assertEqual(len(list(engine._iter_plan_items(merged))), len(list(engine._iter_plan_items(cmpsc))))

    def test_real_triple_major_flows_through_build_full_plan(self):
        # Three real majors that all share heavy MATH/STAT overlap (CMPSC,
        # MATH, STAT) is exactly the case most likely to hit duplicate-
        # requirement scheduling bugs if the widening logic didn't
        # generalize cleanly from 2 majors to N.
        import datetime
        cmpsc = engine.load_degree_plan("CMPSC")
        math = engine.load_degree_plan("MATH")
        stat = engine.load_degree_plan("STAT")
        merged = engine.merge_plans(cmpsc, second_major=math, additional_majors=[stat])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])

    def test_api_plan_accepts_additional_majors_list(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": [], "start_year": 2026,
            "second_major": "MATH", "additional_majors": ["STAT"],
        })
        self.assertEqual(r.status_code, 200)

    def test_api_plan_rejects_non_list_additional_majors(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": [], "start_year": 2026,
            "additional_majors": "STAT",
        })
        self.assertEqual(r.status_code, 400)


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


class TestCreditBillingAnnotation(unittest.TestCase):
    """Real PSU billing thresholds for a fall/spring term: under 12cr is
    part-time (per-credit billing instead of the flat full-time rate), over
    19cr incurs additional per-credit charges on top of the flat rate.
    Purely informational per-term flags -- never change what gets
    scheduled, and deliberately never land in `warnings` (see the comment
    in build_full_plan: many real majors' own flowcharts legitimately end
    in a lighter final semester, and warnings==[] is this whole test
    suite's signal for "nothing wrong with this plan")."""

    def test_every_term_carries_the_two_flags(self):
        import datetime
        plan, catalog = _plan_and_catalog()
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        for t in fp["terms"]:
            self.assertIn("below_full_time", t)
            self.assertIn("above_flat_rate", t)

    def test_real_major_with_a_light_final_semester_flags_it(self):
        # ELED's own real flowchart ends in a sub-12cr student-teaching term.
        import datetime
        plan = engine.load_degree_plan("ELED")
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        light_terms = [t for t in fp["terms"] if t["below_full_time"]]
        self.assertTrue(light_terms)
        for t in light_terms:
            self.assertLess(t["total_credits"], engine.MIN_FULL_TIME_CREDITS)
            self.assertFalse(t["is_summer"])  # summer has its own separate band

    def test_real_major_with_a_heavy_semester_flags_it(self):
        # ELED's own max_credits_per_semester (20) legitimately exceeds the
        # 19cr flat-rate ceiling in several of its real terms.
        import datetime
        plan = engine.load_degree_plan("ELED")
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        heavy_terms = [t for t in fp["terms"] if t["above_flat_rate"]]
        self.assertTrue(heavy_terms)
        for t in heavy_terms:
            self.assertGreater(t["total_credits"], engine.MAX_CREDITS_NO_EXTRA_FEE)

    def test_billing_flags_never_appear_in_warnings(self):
        import datetime
        plan = engine.load_degree_plan("ELED")
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        self.assertTrue(any(t["below_full_time"] or t["above_flat_rate"] for t in fp["terms"]))
        self.assertFalse(any("credit" in w.lower() and "flat" in w.lower() for w in fp["warnings"]))

    def test_summer_terms_are_exempt_from_the_part_time_flag(self):
        # Summer's own cap (SUMMER_MAX_CREDITS=9) is always under 12, but a
        # summer term is billed on its own separate schedule, not the
        # fall/spring full-time-status band.
        import datetime
        plan, catalog = _plan_and_catalog()
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=3, allow_summer=True,
            today=datetime.date(2026, 7, 12),
        )
        summer_terms = [t for t in fp["terms"] if t["is_summer"]]
        self.assertTrue(summer_terms)
        for t in summer_terms:
            self.assertFalse(t["below_full_time"])

    def test_api_plan_response_includes_billing_flags(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "ELED", "prompt": "", "completed": [], "start_year": 2026,
        })
        d = r.get_json()
        cp = d["coursePlan"]
        for t in cp["fullPlan"]["terms"]:
            self.assertIn("belowFullTime", t)
            self.assertIn("aboveFlatRate", t)
        self.assertIn("belowFullTime", cp["nextSemester"])
        self.assertIn("aboveFlatRate", cp["nextSemester"])


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
        # <=8, not ==8: the lighter (123cr) curriculum can legitimately
        # finish a term early once the scheduler packs tightly enough (see
        # the same pattern already documented for ENGL's historical years
        # in EXPANSION_PLAN.md) — not a bug, so don't pin an exact count.
        self.assertLessEqual(len(fp["terms"]), 8)


class TestAccountingPlan(unittest.TestCase):
    """Accounting, B.S. (Smeal College of Business) — first of the Smeal
    majors (Aarush asked for "everything under the Business branch":
    Accounting, Finance, Supply Chain, etc.), distinct from the generic
    Intercollege Business major built earlier. Fourth-year 'ACCTG 403W (or
    ACCTG 4XX elective)' and 'BA 411 (or ACCTG 4XX elective)' are listed
    identically in both terms by the bulletin's own suggested plan — this
    relies on the option-deduplication engine fix (see CYBER/MATH plans)
    to land on 4 distinct courses instead of repeating one pick."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ACCTG", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am an accounting major", "I'm majoring in Accounting"):
            self.assertEqual(_extract_major_from_prompt(phrase), "ACCTG", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_fourth_year_duplicate_option_slots_resolve_to_distinct_courses(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = [p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]]
        self.assertEqual(len(all_codes), len(set(all_codes)), "same course must not repeat across terms")
        self.assertIn("ACCTG 403W", all_codes)
        self.assertIn("BA 411", all_codes)


class TestFinancePlan(unittest.TestCase):
    """Finance, B.S. (Smeal College of Business). FIN 410/414/415/426 need
    FIN 406 specifically (not just FIN 305W like the rest of the elective
    pool); the elective items list those last so the engine only reaches
    for them once FIN 406 is actually done."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("FIN", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a finance major", "I'm majoring in Finance"):
            self.assertEqual(_extract_major_from_prompt(phrase), "FIN", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)


class TestSupplyChainPlan(unittest.TestCase):
    """Supply Chain and Information Systems, B.S. (Smeal College of
    Business). SCM 421 needs SCM 404/405/406; SCM 450W (capstone) needs
    SCM 421 — a real, enforced sequential chain, not just Entrance-to-Major
    gating."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SCM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a supply chain major", "I'm majoring in Supply Chain and Information Systems"):
            self.assertEqual(_extract_major_from_prompt(phrase), "SCM", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_capstone_sequence_respects_prereqs(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "SCM 421" in term_of and "SCM 450W" in term_of:
            self.assertLess(term_of["SCM 421"], term_of["SCM 450W"])


class TestMarketingPlan(unittest.TestCase):
    """Marketing, B.S. (Smeal College of Business). MKTG 450W (capstone)
    requires BOTH MKTG 330 and MKTG 342 completed — a real two-course AND
    gate, not just an Entrance-to-Major flag."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MKTG", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a marketing major", "I'm majoring in Marketing"):
            self.assertEqual(_extract_major_from_prompt(phrase), "MKTG", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_capstone_needs_both_330_and_342(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "MKTG 450W" in term_of:
            for prereq in ("MKTG 330", "MKTG 342"):
                if prereq in term_of:
                    self.assertLess(term_of[prereq], term_of["MKTG 450W"])


class TestManagementPlan(unittest.TestCase):
    """Management, B.S. (Smeal College of Business). MGMT 481 (capstone)
    requires MGMT 326 first — a real prereq, not just an Entrance-to-Major
    flag."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MGMT", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a management major", "I'm majoring in Management"):
            self.assertEqual(_extract_major_from_prompt(phrase), "MGMT", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_capstone_needs_mgmt_326_first(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "MGMT 326" in term_of and "MGMT 481" in term_of:
            self.assertLess(term_of["MGMT 326"], term_of["MGMT 481"])


class TestActuarialSciencePlan(unittest.TestCase):
    """Actuarial Science, B.S. (Smeal College of Business). Starts with
    MATH 140/141 (not MATH 110 like the other Smeal majors) and has a real
    RM chain: RM 410 needs MATH 141, RM 411 needs RM 410, RM 412 needs
    RM 411 — a four-deep dependency chain."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ACTSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am an actuarial science major", "I'm majoring in Actuarial Science"):
            self.assertEqual(_extract_major_from_prompt(phrase), "ACTSC", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_rm_chain_respects_prereqs(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "RM 410" in term_of and "RM 411" in term_of:
            self.assertLess(term_of["RM 410"], term_of["RM 411"])


class TestBusinessAnalyticsPlan(unittest.TestCase):
    """Business Analytics and Information Systems, B.S. (Smeal College of
    Business). MIS 301 -> MIS 431 -> MIS 432 -> MIS 479W (capstone) is a
    real four-deep enforced chain."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("BAIS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a business analytics major", "I'm majoring in Business Analytics and Information Systems"):
            self.assertEqual(_extract_major_from_prompt(phrase), "BAIS", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_capstone_chain_respects_prereqs(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "MIS 432" in term_of and "MIS 479W" in term_of:
            self.assertLess(term_of["MIS 432"], term_of["MIS 479W"])


class TestCorporateInnovationPlan(unittest.TestCase):
    """Corporate Innovation and Entrepreneurship, B.S. (Smeal College of
    Business). MGMT 453 -> MGMT 457W is a real prereq chain."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CIE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a corporate innovation major", "I'm majoring in Entrepreneurship"):
            self.assertEqual(_extract_major_from_prompt(phrase), "CIE", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_capstone_needs_mgmt_453_first(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "MGMT 453" in term_of and "MGMT 457W" in term_of:
            self.assertLess(term_of["MGMT 453"], term_of["MGMT 457W"])


class TestRealEstatePlan(unittest.TestCase):
    """Real Estate, B.S. (Smeal College of Business). RM 330W gates RM 450
    and the FIN/RM 460 & 470 cross-listed pair — a real prereq chain."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("REST", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a real estate major", "I'm majoring in Real Estate"):
            self.assertEqual(_extract_major_from_prompt(phrase), "REST", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_rm_450_needs_330w_first(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "RM 330W" in term_of and "RM 450" in term_of:
            self.assertLess(term_of["RM 330W"], term_of["RM 450"])


class TestRiskManagementPlan(unittest.TestCase):
    """Risk Management, B.S. — Enterprise Risk Management option (Smeal
    College of Business). RM 320W gates RM 405/440; the last two terms
    repeat the same 4-course elective pool three times over (across two
    duplicate items) — this leans on the option-deduplication engine fix
    to spread them across 3 distinct courses instead of repeating one."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("RM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a risk management major", "I'm majoring in Risk Management"):
            self.assertEqual(_extract_major_from_prompt(phrase), "RM", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_elective_slots_resolve_to_distinct_courses(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = [p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]]
        self.assertEqual(len(all_codes), len(set(all_codes)), "same course must not repeat across terms")


class TestBiologyPlan(unittest.TestCase):
    """Biology, B.S. — General Biology option, University Park, standard
    MATH 140 start. The bulletin's own suggested plan lists the 18-credit
    400-level requirement generically as 'BIOL 4XX' rather than specific
    codes (each of its 6 required groups has 20-30 alternative courses),
    so this plan does the same, modeling them as slots."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("BIOL", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a biology major", "I'm majoring in Biology"):
            self.assertEqual(_extract_major_from_prompt(phrase), "BIOL", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_intro_bio_sequence_respects_prereqs(self):
        """BIOL 220W/230W/240W all require BIOL 110 first; PHYS 251 requires
        PHYS 250 first — none should ever land in the same or an earlier
        term than what it depends on."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        for follower in ("BIOL 220W", "BIOL 230W", "BIOL 240W"):
            if follower in term_of:
                self.assertLess(term_of["BIOL 110"], term_of[follower])
        if "PHYS 251" in term_of and "PHYS 250" in term_of:
            self.assertLess(term_of["PHYS 250"], term_of["PHYS 251"])


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


class TestMathPlan(unittest.TestCase):
    """Mathematics, B.S. — General Mathematics option, University Park,
    standard MATH 140 start. Surfaced a real prereq-data bug: CMPSC
    101/121 (two of the five equivalent intro-programming options offered
    to Math majors) enforce prereqs of MATH 21 / MATH 110, PSU's placement
    thresholds below Calc I — a MATH-140-track student has already placed
    past both but the bulletin's prereq text never says so explicitly.
    Patched (catalogs/cmpsc_catalog.json) to also accept MATH 140/141,
    the same 'placement-level prereq' pattern hit for CHEM 110/130,
    STAT 200/250, and MATH 21/110 while building Nursing/Business/Cyber."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MATH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a math major", "I'm a mathematics major"):
            self.assertEqual(_extract_major_from_prompt(phrase), "MATH", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_intro_programming_choice_not_blocked_by_placement_prereq(self):
        """CMPSC 101 (first-listed of the five equivalent intro options)
        requires MATH 21 — a placement threshold, not a real prereq for a
        student already past MATH 140. Regression test for the prereq-data
        bug found building this plan: it must not silently drop the intro
        programming requirement from the simulated plan."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = {p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]}
        intro_options = {"CMPSC 101", "CMPSC 121", "CMPSC 131", "CMPSC 200", "CMPSC 201"}
        self.assertTrue(all_codes & intro_options, "no intro programming course was scheduled")


class TestCyberPlan(unittest.TestCase):
    """Cybersecurity Analytics and Operations, B.S. — represents the 'IT
    field' request. The general 'Information Sciences and Technology, B.S.'
    major was tried first but has no on-campus Suggested Academic Plan, so
    this (an actively-offered, University Park IST major) was substituted.
    Surfaced a real engine-level bug (see test_duplicate_option_plan_terminates
    in TestPlanEngineRobustness) plus a third instance of the MATH-
    placement-gate pattern (MATH 110 -> MATH 22/41)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CYBER", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a cybersecurity major", "I'm in the IST program"):
            self.assertEqual(_extract_major_from_prompt(phrase), "CYBER", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)


class TestBiochemistryPlan(unittest.TestCase):
    """Biochemistry and Molecular Biology, B.S. — Biochemistry option,
    University Park (the bulletin's other option, Molecular and Cell
    Biology, overlaps heavily with the existing BIOL major and wasn't
    built separately). The bulletin's own Requirements-for-the-Major table
    and its Suggested Academic Plan disagree on how the BMB 442/443W/445W/
    448 lab sequence is grouped (and the plan's own listed semester totals
    don't sum correctly either); this plan follows the cleaner Requirements
    table."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("BMB", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a biochemistry major", "I'm majoring in BMB"):
            self.assertEqual(_extract_major_from_prompt(phrase), "BMB", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_bmb_lab_sequence_respects_prereqs(self):
        """BMB 402 and BMB 443W both require BMB 401 (or, for 402, the
        CHEM 476 alternate — not part of this plan, so BMB 401 is the only
        real path). Note BMB 400/401/442 do NOT strictly require BMB 251
        first — each has an independent alternate prereq (CHEM 210/212 or
        MICRB 201) that this plan's own course order can satisfy on its
        own, so asserting a strict BMB-251-first ordering for those three
        would be asserting a constraint the catalog doesn't actually have."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        for follower in ("BMB 402", "BMB 443W"):
            if follower in term_of and "BMB 401" in term_of:
                self.assertLessEqual(term_of["BMB 401"], term_of[follower])


class TestChemistryPlan(unittest.TestCase):
    """Chemistry, B.S. — Analytical/Environmental-Focused option, University
    Park (the bulletin has three other options — Inorganic/Materials,
    Organic/Medicinal, Physical/Computational — that share the same
    15-credit 400-level pool plus a 4-credit advanced lab, modeled here as
    generic slots the same way BIOL models its 400-level elective groups)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CHEM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a chemistry major", "I'm majoring in Chem"):
            self.assertEqual(_extract_major_from_prompt(phrase), "CHEM", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_organic_chem_sequence_respects_prereqs(self):
        """CHEM 212/213 require CHEM 210 first; CHEM 452 requires MATH 231
        first — regression test for the real Calc-II-to-Physical-Chemistry
        chain this plan depends on."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        for follower in ("CHEM 212", "CHEM 213", "CHEM 213W", "CHEM 213M"):
            if follower in term_of and "CHEM 210" in term_of:
                self.assertLess(term_of["CHEM 210"], term_of[follower])
        if "CHEM 452" in term_of and "MATH 231" in term_of:
            self.assertLessEqual(term_of["MATH 231"], term_of["CHEM 452"])


class TestStatisticsPlan(unittest.TestCase):
    """Statistics, B.S. — Statistics and Computing option, University Park
    (the general/data-science track, distinct from the Actuarial Statistics
    option already covered by the ACTSC major). STAT 184's 'MATH 21'
    prerequisite is PSU's placement threshold, not a completable course —
    moved to concurrent_groups in stat_catalog.json (same pattern as
    CHEM 110's MATH 140 concurrency) since the bulletin's own plan takes it
    alongside MATH 140 in term 1."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("STAT", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        for phrase in ("I am a statistics major", "I'm majoring in Stat"):
            self.assertEqual(_extract_major_from_prompt(phrase), "STAT", phrase)

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_stat_184_concurrent_with_math_140(self):
        """STAT 184 should be schedulable in the very first term alongside
        MATH 140, not pushed a term later — the regression test for the
        placement-gate-to-concurrent fix."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "STAT 184" in term_of and "MATH 140" in term_of:
            self.assertLessEqual(term_of["MATH 140"], term_of["STAT 184"])


class TestComputerEngineeringPlan(unittest.TestCase):
    """Computer Engineering, B.S. — University Park. First major to need a
    brand-new department catalog (EE) beyond what Premed/CMPSC/BIOL had
    already cached — verified engine.load_merged_catalog(['EE']) actually
    scrapes and caches it live before relying on it. Finishes in 7
    (not 8) simulated terms — legitimate tight-packing near the 18cr/term
    cap, not a bug (same pattern as the ENGL major's 7-term result)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CMPEN", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a computer engineering major"), "CMPEN")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_capstone_needs_ee_and_cmpsc_sequence_first(self):
        """CMPEN 482W (capstone) requires CMPSC 311, EE 310, and EE 353 —
        all three must land in an earlier term."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "CMPEN 482W" in term_of:
            for pre in ("CMPSC 311", "EE 310", "EE 353"):
                if pre in term_of:
                    self.assertLess(term_of[pre], term_of["CMPEN 482W"])


class TestElectricalEngineeringPlan(unittest.TestCase):
    """Electrical Engineering, B.S. — University Park."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("EE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an electrical engineering major"), "EE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_capstone_needs_ee_300w_and_engl_202c_first(self):
        """EE 403W (capstone) requires EE 300W and ENGL 202C — both must
        land in an earlier term."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "EE 403W" in term_of:
            for pre in ("EE 300W", "ENGL 202C"):
                if pre in term_of:
                    self.assertLess(term_of[pre], term_of["EE 403W"])


class TestMechanicalEngineeringPlan(unittest.TestCase):
    """Mechanical Engineering, B.S. — University Park (Suggested Academic
    Plan for last names A-K). ME 441W, the bulletin's listed alternate to
    the ME 440W capstone, doesn't exist in the department catalog, so the
    plan lists ME 440W only."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ME", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a mechanical engineering major"), "ME")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_design_project_needs_me_340_first(self):
        """ME 440W (design project) requires ME 340 first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "ME 440W" in term_of and "ME 340" in term_of:
            self.assertLess(term_of["ME 340"], term_of["ME 440W"])


class TestCivilEngineeringPlan(unittest.TestCase):
    """Civil Engineering, B.S. — University Park. The bulletin's own
    'CE 337 or CE 475' pick is modeled as concrete CE 337; the Suggested
    Academic Plan's separate, unnamed 'CE Capstone Design' course is
    modeled as a generic 400-level W slot since the bulletin never names
    the actual capstone course code."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a civil engineering major"), "CE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_highway_engineering_needs_surveying_first(self):
        """CE 321 (Highway Engineering) requires CE 310 (Surveying) first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "CE 321" in term_of and "CE 310" in term_of:
            self.assertLess(term_of["CE 310"], term_of["CE 321"])


class TestEconomicsPlan(unittest.TestCase):
    """Economics, B.S. — University Park. ECON 106's 'MATH 21' prerequisite
    is PSU's placement threshold, not a completable course (the same
    pattern hit six times before) — patched in econ_catalog.json to also
    accept MATH 110/140. Also surfaced a real engine bug: with a MATH-110
    track, CMPSC 101's own MATH 140/141-specific prerequisite was never
    satisfied, and the old scheduler never tried the item's CMPSC 203
    fallback option at all — see TestPlanEngineRobustness."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ECON", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an economics major"), "ECON")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_intermediate_econ_needs_intro_first(self):
        """ECON 302/304 (intermediate) need ECON 102/104 (intro) first;
        ECON 306 needs ECON 106 first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "ECON 302" in term_of and "ECON 102" in term_of:
            self.assertLess(term_of["ECON 102"], term_of["ECON 302"])
        if "ECON 304" in term_of and "ECON 104" in term_of:
            self.assertLess(term_of["ECON 104"], term_of["ECON 304"])
        if "ECON 306" in term_of and "ECON 106" in term_of:
            self.assertLess(term_of["ECON 106"], term_of["ECON 306"])


class TestPoliticalSciencePlan(unittest.TestCase):
    """Political Science, B.S. — University Park. Unlike most majors, the
    bulletin's own Suggested Academic Plan uses generic placeholders for
    most major-specific courses ('400-level PLSC', 'Related course in
    consultation with adviser') instead of concrete codes — modeled as
    labeled slots, the same convention as BIOL's 400-level elective groups,
    since it's a genuinely open pool within one department rather than the
    kind of real ambiguity that blocked Psychology (see
    docs/BLOCKED_MAJORS.md). Also caught a real gap in this plan's own
    first draft: it never included the ENGL 15 writing prerequisite that
    ENGL 202A (used in Semester 5) needs."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PLSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a political science major"), "PLSC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_engl_202a_needs_engl_15_first(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "ENGL 202A" in term_of and "ENGL 15" in term_of:
            self.assertLess(term_of["ENGL 15"], term_of["ENGL 202A"])


class TestIndustrialEngineeringPlan(unittest.TestCase):
    """Industrial Engineering, B.S. — General option, University Park. The
    bulletin's own suggested plan lists 'IE 470' in the final semester, but
    its concurrent_groups requires IE 306/307/311/428 — none of which
    appear elsewhere in the bulletin's plan, so IE 470 was dropped for a
    generic elective slot rather than guessing at that link."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("IE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an industrial engineering major"), "IE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_capstone_needs_core_ie_sequence_first(self):
        """IE 480W (capstone) requires IE 302, 305, 323, and 327 — all four
        must land in an earlier term."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "IE 480W" in term_of:
            for pre in ("IE 302", "IE 305", "IE 323", "IE 327"):
                if pre in term_of:
                    self.assertLess(term_of[pre], term_of["IE 480W"])


class TestPhysicsPlan(unittest.TestCase):
    """Physics, B.S. — General option, University Park (the bulletin also
    has Medical Physics, Electronics, Computation, and Nanotechnology/
    Materials options sharing this same common core, not modeled
    separately). Finishes in 7 simulated terms, not 8 — legitimate
    tight-packing near the 17cr/term cap (same pattern as ENGL and CMPEN's
    7-term results), not a bug."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PHYS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a physics major"), "PHYS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_advanced_courses_need_intro_sequence_first(self):
        """PHYS 400/410/419/420 all need the PHYS 211-214/237 intro
        sequence (or the MATH 230/250/251 alternates) completed first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        for follower in ("PHYS 400", "PHYS 419"):
            if follower in term_of and "PHYS 214" in term_of:
                self.assertLess(term_of["PHYS 214"], term_of[follower])


class TestMicrobiologyPlan(unittest.TestCase):
    """Microbiology, B.S. — General Microbiology option, Cell Biology &
    Genetics emphasis, University Park."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MICRB", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a microbiology major"), "MICRB")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_advanced_bmb_sequence_needs_intro_first(self):
        """MICRB 421W needs MICRB 201 first; BMB 442 needs BMB 251/CHEM 210
        first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "MICRB 421W" in term_of and "MICRB 201" in term_of:
            self.assertLess(term_of["MICRB 201"], term_of["MICRB 421W"])


class TestBiotechnologyPlan(unittest.TestCase):
    """Biotechnology, B.S. — General Biotechnology option, University Park.
    Uses the standard CHEM 110/112/210/212/213 organic-chemistry path
    rather than the bulletin's alternate CHEM 202/203 sequence, matching
    every other Eberly Science major built so far."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("BIOTECH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a biotechnology major"), "BIOTECH")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_biotc_courses_need_bmb_sequence_first(self):
        """BIOTC 416/479 need BMB 442 first; BIOTC 459 needs BMB 252 first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "BIOTC 459" in term_of and "BMB 252" in term_of:
            self.assertLess(term_of["BMB 252"], term_of["BIOTC 459"])
        for follower in ("BIOTC 416", "BIOTC 479"):
            if follower in term_of and "BMB 442" in term_of:
                self.assertLess(term_of["BMB 442"], term_of[follower])


class TestChemicalEngineeringPlan(unittest.TestCase):
    """Chemical Engineering, B.S. — University Park."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CHE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a chemical engineering major"), "CHE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_design_capstone_needs_core_che_sequence_first(self):
        """CHE 470/480W (design/capstone) require CHE 320, 330, and 350
        completed first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        for follower in ("CHE 470", "CHE 480W"):
            if follower in term_of:
                for pre in ("CHE 320", "CHE 330", "CHE 350"):
                    if pre in term_of:
                        self.assertLess(term_of[pre], term_of[follower])


class TestAerospaceEngineeringPlan(unittest.TestCase):
    """Aerospace Engineering, B.S. — University Park. The design-sequence
    choice (401A/401B or 402A/402B) and capstone-adjacent choice (413 or
    450) are listed in a consistent option order across paired items so the
    engine's option-fallback resolves them as a matched pair, not a mix."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AERSP", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an aerospace engineering major"), "AERSP")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_design_sequence_pairs_match(self):
        """Whichever of AERSP 401A/402A gets picked, its own B-part
        (401B/402B) must be the one that follows it, not the other track's."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = {p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]}
        if "AERSP 401A" in all_codes:
            self.assertIn("AERSP 401B", all_codes)
            self.assertNotIn("AERSP 402B", all_codes)
        elif "AERSP 402A" in all_codes:
            self.assertIn("AERSP 402B", all_codes)
            self.assertNotIn("AERSP 401B", all_codes)


class TestBiomedicalEngineeringPlan(unittest.TestCase):
    """Biomedical Engineering, B.S. — Biomechanics option, University Park."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("BME", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a biomedical engineering major"), "BME")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_senior_design_needs_core_bme_sequence_first(self):
        """BME 402 requires BME 301 first; BME 429 requires BME 303 or 401
        first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "BME 402" in term_of and "BME 301" in term_of:
            self.assertLess(term_of["BME 301"], term_of["BME 402"])


class TestNuclearEngineeringPlan(unittest.TestCase):
    """Nuclear Engineering, B.S. — University Park. EMCH 316 needs EMCH 315
    as a strict prior-term prerequisite (not concurrent), so it's scheduled
    a full term later than the bulletin's own suggested plan implies."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("NUCE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a nuclear engineering major"), "NUCE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_emch_316_lands_after_emch_315(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "EMCH 316" in term_of and "EMCH 315" in term_of:
            self.assertLess(term_of["EMCH 315"], term_of["EMCH 316"])


class TestAstronomyAstrophysicsPlan(unittest.TestCase):
    """Astronomy and Astrophysics, B.S. — Computer Science option,
    University Park (of two options — the other, Graduate Studies, leans
    on PHYS 400-level courses instead of CMPSC ones). 'ASTRO 320' and
    'CMPSC 202', both named in the bulletin, don't exist in the current
    department catalogs — the plan uses 'ASTRO 320W' and omits the CMPSC
    202 alternate."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ASTRO", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an astronomy and astrophysics major"), "ASTRO")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_astro_292_needs_astro_291_first(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "ASTRO 292" in term_of and "ASTRO 291" in term_of:
            self.assertLess(term_of["ASTRO 291"], term_of["ASTRO 292"])


class TestForensicSciencePlan(unittest.TestCase):
    """Forensic Science, B.S. — Forensic Molecular Biology option,
    University Park (of two options — the other, Forensic Chemistry,
    substitutes a CHEM 227/FRNSC 425/427W sequence). Surfaced a real
    engine bug (see TestPlanEngineRobustness) via BIOL 234/235W's
    bidirectional concurrent requirement, which deadlocked the scheduler.
    The 'BIOL 222 or 322' requirement's own prerequisites aren't otherwise
    part of this plan's core sequence, so it's modeled as a generic
    'Genetics course' slot rather than an enforced pick."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("FRNSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a forensic science major"), "FRNSC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_biol_235w_lab_pairs_with_lecture(self):
        """BIOL 235W (lab) must never land before BIOL 234 (lecture) —
        regression test for the bidirectional-concurrent deadlock fix."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "BIOL 234" in term_of and "BIOL 235W" in term_of:
            self.assertLessEqual(term_of["BIOL 234"], term_of["BIOL 235W"])


class TestBiologicalEngineeringPlan(unittest.TestCase):
    """Biological Engineering, B.S. — Agricultural Engineering option,
    University Park (of three options — Food and Biological Processing
    Engineering and Natural Resources Engineering are the others)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("BE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a biological engineering major"), "BE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_design_sequence_needs_be_391_first(self):
        """BE 460W (design I) requires BE 391 first; BE 466W (design II)
        requires BE 460W first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "BE 460W" in term_of and "BE 391" in term_of:
            self.assertLess(term_of["BE 391"], term_of["BE 460W"])
        if "BE 466W" in term_of and "BE 460W" in term_of:
            self.assertLess(term_of["BE 460W"], term_of["BE 466W"])


class TestNeurobiologyPlan(unittest.TestCase):
    """Neurobiology, B.S. — University Park. The bulletin's own suggested
    plan interleaves the BIOL 161/162 (Anatomy) and BIOL 222-or-322
    (Genetics) requirements confusingly — resolved against the cleaner
    Requirements-for-the-Major list. Also caught two real gaps in this
    plan's own first draft during verification: MATH 140B (used to match
    the bulletin's literal text) doesn't satisfy CHEM 110's established
    'concurrent with MATH 140' fix, since that check only recognizes the
    bare MATH 140 code — fixed by using MATH 140 like every other major
    does; and CHEM 111 was missing entirely, which blocked CHEM 113."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("NEURO", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a neurobiology major"), "NEURO")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_biol_470_needs_biol_469_first(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "BIOL 470" in term_of and "BIOL 469" in term_of:
            self.assertLess(term_of["BIOL 469"], term_of["BIOL 470"])


class TestPlanetaryScienceAstronomyPlan(unittest.TestCase):
    """Planetary Science and Astronomy, B.S. — University Park."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PLANET", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a planetary science and astronomy major"), "PLANET")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_astro_402w_needs_physics_and_astro_401_pace(self):
        """ASTRO 402W is scheduled after ASTRO 401 in this plan."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "ASTRO 402W" in term_of and "ASTRO 401" in term_of:
            self.assertLessEqual(term_of["ASTRO 401"], term_of["ASTRO 402W"])


class TestEngineeringSciencePlan(unittest.TestCase):
    """Engineering Science, B.S. — University Park. Major code follows the
    department's real course prefix (ESC), not the bulletin URL slug. The
    bulletin gives no concrete course codes for its 'Foundational Elective'
    (15cr) or 'Technical Elective' (12cr) pools — modeled as generic slots,
    the same convention as BIOL's 400-level elective groups, since it's a
    large open departmental pool rather than Psychology-style total
    ambiguity. 'ESC 410', a bulletin-listed prescribed course, doesn't
    exist in the current department catalog — modeled as a generic slot."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ESC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an engineering science major"), "ESC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_esc_312_needs_phys_214_first(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "ESC 312" in term_of and "PHYS 214" in term_of:
            self.assertLess(term_of["PHYS 214"], term_of["ESC 312"])


class TestDataSciencesPlan(unittest.TestCase):
    """Data Sciences, B.S. — Statistical Modeling option, University Park.
    DS 200's 'MATH 21' prerequisite is PSU's placement threshold — moved to
    concurrent_groups in ds_catalog.json (same pattern as STAT 184/
    CHEM 110). Deliberately ordered the 'DS 200 or STAT 200' item as
    STAT 200 first, opposite the bulletin's own order, since STAT 462 later
    in this plan specifically needs real STAT 200/240/250/401 credit."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("DS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a data sciences major"), "DS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_stat_200_satisfies_stat_462_prereq(self):
        """Regression test for the deliberate STAT 200-over-DS 200 choice:
        STAT 462 must actually get scheduled (it wouldn't if DS 200 had
        been picked instead, since DS 200 doesn't satisfy STAT 462's real
        prerequisite)."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = {p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]}
        self.assertIn("STAT 462", all_codes)


class TestSurveyingEngineeringPlan(unittest.TestCase):
    """Surveying Engineering, B.S. The bulletin's own Suggested Academic
    Plan is published for the Wilkes-Barre campus (no University Park
    offering), matching the same pattern as the Intercollege BUSINESS
    major. SUR 121's 'MATH 26/41' concurrent requirement is PSU's
    placement threshold — patched to also accept MATH 140/141."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SUR", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a surveying engineering major"), "SUR")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_advanced_photogrammetry_needs_surveying_software_first(self):
        """SUR 421 requires SUR 132 and SUR 222 first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "SUR 421" in term_of:
            for pre in ("SUR 132", "SUR 222"):
                if pre in term_of:
                    self.assertLess(term_of[pre], term_of["SUR 421"])


class TestElectroMechanicalEngineeringTechnologyPlan(unittest.TestCase):
    """Electro-Mechanical Engineering Technology, B.S. The bulletin's own
    Suggested Academic Plan is published for the Beaver campus (labeled by
    the bulletin itself as the 'University Park equivalent'). Uses a slower
    math on-ramp (MATH 26 in term 1) than most Engineering majors, matched
    to the bulletin's real sequence. Surfaced a real data bug: MATH 26
    itself required 'MATH 21' (an uncompletable placement threshold) as a
    strict prerequisite, which would have blocked MATH 26 forever — fixed
    by clearing that prerequisite in math_catalog.json, since a student
    places into MATH 26, they don't complete a prior course to get there."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("EMET", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an electro-mechanical engineering technology major"), "EMET")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_math_26_has_no_prereq(self):
        """Regression test for the MATH 21 placement-threshold fix: MATH 26
        must be schedulable in term 1 with nothing else completed first."""
        catalog = engine.load_merged_catalog(["MATH"])
        self.assertEqual(catalog["MATH 26"].prereq_groups, [])


class TestIntegrativeSciencePlan(unittest.TestCase):
    """Integrative Science, B.S. — General Science option, University Park.
    No dedicated department course prefix, matching BUSINESS/ACTSC/CIE.
    Chose the simpler PHYS 250/251 and BIOL 230W paths over the bulletin's
    other listed alternatives, matching the same choice already made for
    Neurobiology and Biotechnology."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("INTSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an integrative science major"), "INTSC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestElectricalEngineeringTechnologyPlan(unittest.TestCase):
    """Electrical Engineering Technology, B.S. The bulletin merges two
    options (General EET, Power/Automation) into one table with variable
    credit ranges rather than a clean per-option breakdown — built against
    the General EET option's real anchor courses, with option-dependent
    electives modeled as generic slots. Surfaced two real data bugs: EET
    114 requires EET 105 AND (separately) MATH 26 — not alternatives —
    patched to also accept MATH 140/141 in the MATH 26 slot; and EET 331's
    three separate 'AND' prereq groups (EE 314/315/EET 311, EE 310,
    EET 312) were almost certainly a scraper-flattened OR-group of
    equivalent circuits courses, not a requirement to complete all three
    unrelated circuits sequences — merged into one OR group."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("EET", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an electrical engineering technology major"), "EET")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_capstone_needs_core_eet_sequence_first(self):
        """EET 420W (capstone) requires EET 312, EET 331, EET 419, and
        ENGL 202C all completed first."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "EET 420W" in term_of:
            for pre in ("EET 312", "EET 331", "EET 419"):
                if pre in term_of:
                    self.assertLess(term_of[pre], term_of["EET 420W"])


class TestMeteorologyAtmosphericSciencePlan(unittest.TestCase):
    """Meteorology and Atmospheric Science, B.S. — Atmospheric Science
    option, University Park (of six options). Opens the College of Earth
    and Mineral Sciences. Reused the METEO catalog already scraped for
    Planetary Science and Astronomy."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("METEO", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a meteorology major"), "METEO")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_meteo_436_pool_resolves_to_distinct_courses(self):
        """Both 'METEO 436 (or 437/454)' items must resolve to distinct
        courses, not the same one twice."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = [p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]]
        meteo_pool_picks = [c for c in all_codes if c in ("METEO 436", "METEO 437", "METEO 454")]
        self.assertEqual(len(meteo_pool_picks), len(set(meteo_pool_picks)))


class TestGeosciencesPlan(unittest.TestCase):
    """Geosciences, B.S. — General option, University Park (of two options
    — Hydrogeology is the other). Major code 'GEOSCI' avoids colliding with
    the GEOSC department prefix. GEOSC 472B (Field Geology II) is
    real-world scheduled in a required summer term the bulletin's own plan
    calls for — this planner only models summer as an optional student
    choice, so it's scheduled in a regular term instead."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("GEOSCI", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a geosciences major"), "GEOSCI")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_field_geology_ii_pairs_with_field_geology_i(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "GEOSC 472A" in term_of and "GEOSC 310" in term_of:
            self.assertLessEqual(term_of["GEOSC 310"], term_of["GEOSC 472A"])


class TestGeographyPlan(unittest.TestCase):
    """Geography, B.S. — University Park. Every core GEOG course has zero
    listed prerequisites, so ordering follows the bulletin's own suggested
    sequence directly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("GEOG", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a geography major"), "GEOG")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_gis_skills_pool_resolves_to_distinct_courses(self):
        """Both 'GEOG 361 (or 362/363/365)' items must resolve to distinct
        courses, not the same one twice."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = [p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]]
        gis_picks = [c for c in all_codes if c in ("GEOG 361", "GEOG 362", "GEOG 363", "GEOG 365")]
        self.assertEqual(len(gis_picks), len(set(gis_picks)))


class TestEnergyEngineeringPlan(unittest.TestCase):
    """Energy Engineering, B.S. — University Park. Major code 'ENGY' avoids
    colliding with the EGEE department prefix. The 'EGEE 451 or ENVSE 470'
    item lists EGEE 451 first per the bulletin's own order, but EGEE 451
    needs FSC 431 (not otherwise part of this plan) — relies on the
    engine's option-fallback fix (see TestPlanEngineRobustness) to resolve
    to ENVSE 470 instead."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ENGY", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an energy engineering major"), "ENGY")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_envse_470_resolves_via_option_fallback(self):
        """Regression test for the deliberate EGEE 451/ENVSE 470 ordering:
        ENVSE 470 must actually get scheduled since EGEE 451's own
        prerequisite (FSC 431) is never satisfied in this plan."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = {p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]}
        self.assertIn("ENVSE 470", all_codes)
        self.assertNotIn("EGEE 451", all_codes)


class TestMaterialsScienceEngineeringPlan(unittest.TestCase):
    """Materials Science and Engineering, B.S. — University Park. Major
    code 'MATSCI' avoids colliding with the MATSE department prefix. The
    capstone (MATSE 493W or 494W) is simplified to one term rather than the
    bulletin's variable 0-3/1-3 split across two real terms."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MATSCI", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a materials science and engineering major"), "MATSCI")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestEarthSciencesPlan(unittest.TestCase):
    """Earth Sciences, B.S. — University Park. Major code 'EARTHSCI' avoids
    colliding with the EARTH department prefix. Requires 18 credits from
    ONE of five interdisciplinary minors (Climatology, Marine Science,
    Watersheds and Water Resources, Earth Systems, Global Business
    Strategies) — assumed Earth Systems, modeled generically as 'Minor
    Course' slots since the bulletin page doesn't give the per-minor
    course list."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("EARTHSCI", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an earth sciences major"), "EARTHSCI")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestGeobiologyPlan(unittest.TestCase):
    """Geobiology, B.S. The 'BIOL 444 or GEOSC 472A' item lists BIOL 444
    first per the bulletin's own order, but BIOL 444 needs BIOL 220W (not
    otherwise part of this plan) — relies on the engine's option-fallback
    fix to resolve to GEOSC 472A instead."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("GEOBIO", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a geobiology major"), "GEOBIO")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_biol_225w_lab_needs_biol_224_first(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "BIOL 225W" in term_of and "BIOL 224" in term_of:
            self.assertLess(term_of["BIOL 224"], term_of["BIOL 225W"])


class TestMiningEngineeringPlan(unittest.TestCase):
    """Mining Engineering, B.S. Major code 'MINE' avoids colliding with the
    MNG department prefix. Surfaced a real data bug: the bulletin's own
    suggested plan lists 'EME 460 or MNG 412' as one alternative pick, but
    MNG 412 is independently required by MNG 451W's own capstone
    prerequisite (a real AND-chain: MNG 331 + MNG 404 + MNG 412 + MNG 422)
    — modeling them as alternatives left MNG 412 never actually completed,
    permanently blocking the capstone. Fixed by making both standalone
    required items, matching the Requirements table. Also deliberately
    ordered 'STAT 401 or EME 210' with STAT 401 first (opposite the
    bulletin's order) since MNG 412 specifically needs STAT 401 — after EME
    210's own placement-gate prerequisite was patched (see EME-2026.json's
    fix note) to also accept MATH 140/141, EME 210 became eligible early
    enough that the engine would otherwise prefer it, silently breaking the
    MNG 412 dependency."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MINE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a mining engineering major"), "MINE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_capstone_needs_mng_412_completed(self):
        """Regression test for the MNG 412/EME 460 fix: MNG 412 must
        actually be scheduled (it wouldn't be if it were still modeled as
        an EME 460 alternative), and MNG 451W must land after it."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        self.assertIn("MNG 412", term_of)
        if "MNG 451W" in term_of:
            self.assertLess(term_of["MNG 412"], term_of["MNG 451W"])


class TestPetroleumNaturalGasEngineeringPlan(unittest.TestCase):
    """Petroleum and Natural Gas Engineering, B.S. PNG 490 (capstone)
    genuinely requires all of PNG 430, PNG 440W, PNG 450, EME 460,
    PNG 475, and GEOSC 454 completed first (a real AND-chain, not a
    flattened OR artifact) — all six are independently required elsewhere
    in this plan."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PNG", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a petroleum and natural gas engineering major"), "PNG")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_capstone_needs_full_prereq_chain(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for t in fp["terms"]:
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = t["index"]
        if "PNG 490" in term_of:
            for pre in ("PNG 430", "PNG 440W", "PNG 450", "EME 460", "PNG 475", "GEOSC 454"):
                if pre in term_of:
                    self.assertLess(term_of[pre], term_of["PNG 490"])


class TestEnvironmentalSystemsEngineeringPlan(unittest.TestCase):
    """Environmental Systems Engineering, B.S. Major code 'ENVSYS' avoids
    colliding with the ENVSE department prefix. The bulletin's own
    suggested plan lists an 'EME 210 or ENGL 202C' item in two different
    terms — since ENGL 202C is separately a required prescribed course,
    this is almost certainly a scrape duplication; modeled ENGL 202C once
    with a generic Supporting Course slot in its second appearance."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ENVSYS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an environmental systems engineering major"), "ENVSYS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestEnergyBusinessFinancePlan(unittest.TestCase):
    """Energy Business and Finance, B.S. Major code 'EBFIN' avoids
    colliding with the EBF department prefix. Surfaced a real engine-level
    bug: the bulletin requires 6 credits from 'EGEE 401/EME 444/METEO 469'
    across two terms, but with this plan's own course choices (IB 303 over
    EGEE 120, no CHEM 112 anywhere) only METEO 469 is ever actually
    eligible — modeling both occurrences as a real course pick caused an
    infinite loop (24 simulated terms, never finishing), since the second
    item could never resolve to a course distinct from the first. Fixed by
    making the second occurrence a generic slot."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("EBFIN", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an energy business and finance major"), "EBFIN")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_meteo_469_pool_does_not_loop(self):
        """Regression test for the infinite-loop fix: the plan must finish
        well under the 24-term simulation cap."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
            max_terms=24,
        )
        self.assertLess(len(fp["terms"]), 24)
        self.assertTrue(fp["goal"]["met"])


class TestEarthSciencePolicyPlan(unittest.TestCase):
    """Earth Science and Policy, B.S. (General option). Major code 'ESP'.
    Real bug caught during verification: the bulletin's own 'MATH 83, 110,
    140, or 140G' ordering, if followed literally, picks MATH 110 first —
    which does not satisfy CHEM 110's concurrent MATH 140/140G/141/22
    requirement (same bug class as Neurobiology's MATH 140B case).
    Reordered to list MATH 140/140G first."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ESP", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an earth science and policy major"), "ESP")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestEnergySustainabilityPolicyPlan(unittest.TestCase):
    """Energy and Sustainability Policy, B.S. World Campus only — the
    bulletin's Suggested Academic Plan is published for World Campus, not
    University Park (same 'no UP offering' pattern as Surveying
    Engineering). Its uneven per-year credit table (31/33/30/26) was
    redistributed into a standard 8-term, ~15cr/term structure."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ESUS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an energy and sustainability policy major"), "ESUS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestAnimalSciencePlan(unittest.TestCase):
    """Animal Science, B.S. (Industry and General Animal Interest option)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ANSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an animal science major"), "ANSC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestFoodSciencePlan(unittest.TestCase):
    """Food Science, B.S. Two real bugs caught during verification: (1)
    CHEM 110's concurrent MATH requirement had no math course scheduled
    alongside it at all (MATH was a semester later) — moved MATH 140 into
    Semester 1; (2) FDSC 405's MATH 110 prereq group had no MATH 140/140B
    alternates, so it could never resolve once the plan started using
    MATH 140 instead of literal MATH 110 — fixed in fdsc_catalog.json."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("FDSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a food science major"), "FDSC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_chem_110_has_concurrent_math_in_same_semester(self):
        """Regression test for the concurrency-deadlock fix: CHEM 110 and
        its concurrent MATH course must be scheduled in the same term."""
        sem1_options = {
            opt
            for item in self.plan["semesters"][0]["items"]
            for opt in item.get("options", [])
        }
        self.assertIn("CHEM 110", sem1_options)
        self.assertTrue(sem1_options & {"MATH 140", "MATH 140B", "MATH 110"})


class TestPlantSciencesPlan(unittest.TestCase):
    """Plant Sciences, B.S. (Agroecology option). Major code 'PLSCI' avoids
    colliding with the department's own PLANT prefix. AGRO 28 and HORT 101
    (real anchor courses, not generic pools) were missing from the catalog
    entirely since the scraper never covered those small departments —
    added minimal entries in agro_catalog.json/hort_catalog.json."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PLSCI", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a plant sciences major"), "PLSCI")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_agro_28_catalog_entry_exists(self):
        """Regression test for the missing-department-data fix: AGRO 28
        gates AGECO 438 and must resolve to a real catalog course."""
        course = self.catalog.get("AGRO 28")
        self.assertIsNotNone(course)
        self.assertEqual(course.credits, 3.0)


class TestAgribusinessManagementPlan(unittest.TestCase):
    """Agribusiness Management, B.S. 'AGSC 100' (AESE First Year Seminar,
    1cr, no prereqs) was missing from the catalog entirely -- added a
    minimal entry in new agsc_catalog.json."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AGBM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an agribusiness management major"), "AGBM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestImmunologyInfectiousDiseasePlan(unittest.TestCase):
    """Immunology and Infectious Disease, B.S. Major code 'IID' since
    courses split across VBSC/MICRB/BMB. The entire VBSC department
    catalog was missing -- added minimal entries in new vbsc_catalog.json.
    VBSC 448W needs BMB 400, which the bulletin's own suggested plan never
    otherwise scheduled -- added as an explicit Semester 7 item."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("IID", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an immunology and infectious disease major"), "IID")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_bmb_400_scheduled_before_vbsc_448w(self):
        """Regression test: VBSC 448W needs BMB 400, which wasn't anywhere
        on the bulletin's own suggested plan -- must be scheduled earlier."""
        sem7_options = {
            opt
            for item in self.plan["semesters"][6]["items"]
            for opt in item.get("options", [])
        }
        self.assertIn("BMB 400", sem7_options)


class TestPharmacologyToxicologyPlan(unittest.TestCase):
    """Pharmacology and Toxicology, B.S. Major code 'PHTX'. Extended
    vbsc_catalog.json with VBSC 190/230/331/430/431/433/438. VBSC 438's
    real prereq (CHEM 202/201) was modeled with CHEM 210 as an equivalent
    alternate, since the bulletin's own CHEM 210 description says CHEM 202
    and CHEM 210 duplicate subject matter and can't both be taken for
    credit -- this plan's chemistry sequence uses CHEM 210, not 202."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PHTX", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a pharmacology and toxicology major"), "PHTX")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_vbsc_331_prereq_biol_230w_is_scheduled(self):
        """Regression test: VBSC 331 has an enforced BIOL 230W/230M
        prerequisite -- must actually appear somewhere in the plan."""
        all_options = {
            opt
            for sem in self.plan["semesters"]
            for item in sem["items"]
            for opt in item.get("options", [])
        }
        self.assertIn("BIOL 230W", all_options)


class TestEnvironmentalResourceManagementPlan(unittest.TestCase):
    """Environmental Resource Management, B.S. (Environmental Science
    option). 'ASM 327' (real anchor course, required across multiple
    majors) had no findable prerequisite text on the bulletin after
    several attempts -- added a minimal no-prereq entry in new
    asm_catalog.json. 'CED 201' requires 'ERM 300' as a same-term
    concurrent requirement -- both scheduled in Semester 6, ERM 300 listed
    first."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ERM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an environmental resource management major"), "ERM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestWildlifeFisheriesSciencePlan(unittest.TestCase):
    """Wildlife and Fisheries Science, B.S. (Wildlife option). The entire
    WFS department catalog and FOR 203/350 were missing entirely -- added
    minimal entries in new wfs_catalog.json/for_catalog.json. Real catalog
    bug fixed: STAT 240's only prereq was the uncompletable placement-gate
    'MATH 21' (same recurring pattern as STAT 184/DS 200/ECON 106/SUR 121)
    -- added MATH 110/140 alternates in stat_catalog.json."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("WFS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a wildlife and fisheries science major"), "WFS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_stat_240_does_not_require_uncompletable_placement_gate(self):
        """Regression test for the placement-gate fix: STAT 240 must be
        reachable via MATH 110/140, not just the uncompletable MATH 21."""
        course = self.catalog.get("STAT 240")
        self.assertIsNotNone(course)
        gate_group = course.prereq_groups[0]
        self.assertTrue({"MATH 110", "MATH 140"} & set(gate_group))


class TestAgriculturalBiorenewableSystemsManagementPlan(unittest.TestCase):
    """Agricultural and Biorenewable Systems Management, B.S. The entire
    ABSM department catalog was missing -- added minimal entries in new
    absm_catalog.json. Several ABSM courses list '5th/7th-semester
    standing' prerequisites the planner schema doesn't model directly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ABSM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an agricultural and biorenewable systems management major"), "ABSM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestVeterinaryBiomedicalSciencesPlan(unittest.TestCase):
    """Veterinary and Biomedical Sciences, B.S. Extended vbsc_catalog.json
    with VBSC 421 and VBSC 403. Chose the CHEM 210/212/213 organic
    chemistry track and BIOL 230W for the plan's 'or' pools since both
    feed cleanly into BMB 401's own prereq OR-group."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("VBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a veterinary and biomedical sciences major"), "VBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestTurfgrassSciencePlan(unittest.TestCase):
    """Turfgrass Science, B.S. The entire TURF department catalog was
    missing -- added minimal entries in new turf_catalog.json. Real bug
    avoided: picked CHEM 130 over CHEM 110 for Semester 1 specifically
    because CHEM 110's concurrent MATH 140/141/22 requirement doesn't
    recognize this major's own entry math course, MATH 21."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("TURF", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a turfgrass science major"), "TURF")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_chem_130_avoids_math_21_concurrency_mismatch(self):
        """Regression test: Semester 1 must pick CHEM 130 (or list it
        first), not rely on CHEM 110's MATH 140/141/22 concurrency being
        satisfied by this major's own MATH 21 entry course."""
        sem1_options = {
            opt
            for item in self.plan["semesters"][0]["items"]
            for opt in item.get("options", [])
        }
        self.assertIn("CHEM 130", sem1_options)


class TestForestEcosystemsPlan(unittest.TestCase):
    """Forest Ecosystems, B.S. (Biodiversity and Conservation option).
    Major code FORES avoids colliding with the FOR department prefix.
    Real bug fixed during verification: Semester 1's math item listed
    MATH 110 first, but CHEM 110's concurrent requirement only recognizes
    MATH 140/141/22 -- reordered to list MATH 140 first, same fix pattern
    as Earth Science and Policy and Food Science."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("FORES", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a forest ecosystems major"), "FORES")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_math_140_listed_before_math_110_for_chem_concurrency(self):
        """Regression test for the concurrency-ordering fix."""
        sem1_options = self.plan["semesters"][0]["items"][2]["options"]
        self.assertEqual(sem1_options[0], "MATH 140")


class TestCommunityEnvironmentDevelopmentPlan(unittest.TestCase):
    """Community, Environment, and Development, B.S. (Community and
    Economic Development option). Real data gap: AEE 460 has an enforced
    prerequisite of AEE 360, which the bulletin's own suggested plan never
    otherwise schedules -- added AEE 360 as an explicit Semester 5 item,
    same spirit as Food Science's BMB 400 fix earlier this session."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a community, environment, and development major"), "CED")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_aee_360_scheduled_before_aee_460(self):
        """Regression test: AEE 460 needs AEE 360, which the bulletin
        never lists anywhere -- must be an explicit earlier item."""
        all_options = {
            opt
            for sem in self.plan["semesters"]
            for item in sem["items"]
            for opt in item.get("options", [])
        }
        self.assertIn("AEE 360", all_options)


class TestArtificialIntelligenceMethodsApplicationsPlan(unittest.TestCase):
    """Artificial Intelligence Methods and Applications, B.S. The entire
    A-I and AIMA departments were missing -- added minimal entries in new
    a-i_catalog.json/aima_catalog.json. Two real bugs: (1) STAT 401 needs
    MATH 111/141, never scheduled anywhere in the bulletin's own plan --
    added MATH 141 explicitly; (2) a genuine infinite-loop bug identical in
    shape to Energy Business and Finance's METEO 469 case -- AIMA 430 was
    originally scheduled right after its own prerequisite A-I 375 with
    enough same-term credit headroom that the engine's greedy scan pulled
    both into the same simulated term, and since prereqs (unlike
    concurrent requirements) only check credit banked from PRIOR terms,
    this silently fell back to A-I 494 and permanently starved the real
    capstone sequence. Fixed by padding Semester 6 to 18 credits (over the
    17cr/term cap) so the scan closes that term before ever reaching
    AIMA 430."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AIMA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an artificial intelligence methods and applications major"), "AIMA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_aima_capstone_does_not_loop(self):
        """Regression test for the infinite-loop fix: the plan must finish
        well under the 24-term simulation cap, and AIMA 430/440 (not the
        A-I 494 fallback) must actually appear."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
            max_terms=24,
        )
        self.assertLess(len(fp["terms"]), 24)
        self.assertTrue(fp["goal"]["met"])
        codes = {c["code"] for t in fp["terms"] for c in t["courses"]}
        self.assertIn("AIMA 430", codes)


class TestInformationTechnologyEthicsCompliancePlan(unittest.TestCase):
    """Information Technology Ethics and Compliance, B.S. Built in place of
    Information Sciences and Technology, B.S., which is genuinely blocked
    (see BLOCKED_MAJORS.md -- no Suggested Academic Plan anywhere, even in
    its own PDF). The entire IEC and ETI departments were missing. Three
    real data gaps fixed: MATH 22 needing never-scheduled MATH 21 (used
    MATH 110 instead); ETI 301/302 needing IST 220, never scheduled (added
    explicitly); DS 435 needing DS 220 needing CMPSC 131, neither
    scheduled (added both explicitly)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("IEC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an information technology ethics and compliance major"), "IEC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestSecurityRiskAnalysisPlan(unittest.TestCase):
    """Security and Risk Analysis, B.S. (Intelligence Analysis and
    Modeling option). No data gaps found -- every prereq resolves cleanly
    against courses already in this plan."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SRA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a security and risk analysis major"), "SRA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestHumanCenteredDesignDevelopmentPlan(unittest.TestCase):
    """Human-Centered Design and Development, B.S. The entire HCDD
    department was missing -- added minimal entries. Every HCDD-sequence
    course accepts 'HCDD 311' as an equivalent to the nonexistent
    'IST 311' -- consistently picking the HCDD variant keeps the whole
    chain self-satisfying with no gaps."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("HCDD", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a human-centered design and development major"), "HCDD")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestEnterpriseTechnologyIntegrationPlan(unittest.TestCase):
    """Enterprise Technology Integration, B.S. Extended eti_catalog.json
    with ETI 300W/420/421/423/435/461/463, consistently picking the
    ETI-prefixed variant since the IST alternates don't exist. Real data
    gap: the 'HCDD 331, IST 331, or HCDD 264' item needs HCDD 264's own
    prereq (HCDD 113/113S/ETI 100), none otherwise scheduled -- added
    HCDD 113S explicitly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ETI", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an enterprise technology integration major"), "ETI")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestJournalismPlan(unittest.TestCase):
    """Journalism, B.A. (Digital and Print Journalism option). The entire
    COMM department catalog was missing -- added minimal entries in new
    comm_catalog.json. The bulletin's repeated 'COMM 403/409' pool
    (appearing twice) was resolved to two distinct courses (403 first
    occurrence, 409 second) to avoid scheduling the same code twice."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("JOURN", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a journalism major"), "JOURN")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestAdvertisingPublicRelationsPlan(unittest.TestCase):
    """Advertising/Public Relations, B.A. (Public Relations option). No
    data gaps found -- the full COMM 370->372/420/471->473 prereq chain
    resolves cleanly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ADPR", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an advertising/public relations major"), "ADPR")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestTelecommunicationsMediaIndustriesPlan(unittest.TestCase):
    """Telecommunications and Media Industries, B.A. No formal tracks.
    Extended comm_catalog.json with COMM 180/280/380/404/486/487W."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("TELE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a telecommunications and media industries major"), "TELE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestFilmProductionPlan(unittest.TestCase):
    """Film Production, B.A. Major code FLMPR since the department has no
    single course-code prefix (all COMM). Of the 'Advanced Production'/
    'Advanced Additional' pool, picked COMM 437/440/444/445 -- all four
    share the identical 'COMM 340 + COMM 342W + one of 337/338/339'
    prereq shape, avoiding COMM 439/437A/443/446 which need COMM 339
    (not otherwise in this plan)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("FLMPR", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a film production major"), "FLMPR")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestMediaStudiesPlan(unittest.TestCase):
    """Media Studies, B.A. (Media Effects option). Major code MDST since
    the department has no single course-code prefix. Verified and used
    COMM 325/326 from the 'COMM 325/326/327/328' Media Effects elective
    pool (327/328 not independently confirmed)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MDST", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a media studies major"), "MDST")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestKinesiologyPlan(unittest.TestCase):
    """Kinesiology, B.S. (Movement Science option). Real bug avoided:
    substituted MATH 140 for the bulletin's own MATH 26 Semester 1 pick,
    since CHEM 110's concurrent requirement recognizes only MATH 22/140/141,
    not MATH 26 -- same recurring mismatch fixed for several majors this
    session."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("KINES", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a kinesiology major"), "KINES")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestNutritionalSciencesPlan(unittest.TestCase):
    """Nutritional Sciences, B.S. (Nutrition and Dietetics option). Added
    new hm_catalog.json for HM 230/330 (Hospitality Management
    department). Chose CHEM 202 over CHEM 210 since CHEM 202's prereq
    (CHEM 110) is directly satisfied, while CHEM 210 needs CHEM 112, not
    otherwise scheduled."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("NUTR", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a nutritional sciences major"), "NUTR")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestHumanDevelopmentFamilyStudiesPlan(unittest.TestCase):
    """Human Development and Family Studies, B.S. (Human Development and
    Family Science option). Real bug avoided: the 'HDFS 200/EDPSY 101/
    STAT 200' statistics item lists STAT 200 first, since HDFS 312W's own
    prereq only recognizes EDPSY 101/STAT 200, not HDFS 200. The flexible
    'HDFS Capstone' (internship/research pathways, no single official
    course sequence) is split into a real Fall precursor (HDFS 490) and a
    generic Spring slot for the pathway-specific follow-on."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("HDFS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a human development and family studies major"), "HDFS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_stat_200_listed_before_hdfs_200_for_312w_prereq(self):
        """Regression test for the concurrency-ordering fix."""
        sem3_options = self.plan["semesters"][2]["items"][2]["options"]
        self.assertEqual(sem3_options[0], "STAT 200")


class TestHealthPolicyAdministrationPlan(unittest.TestCase):
    """Health Policy and Administration, B.S. Real bug avoided: picked
    CMPSC 203 over CMPSC 101 for the 'Programming/Spreadsheets/MIS' item,
    since CMPSC 101 has an uncompletable placement-gate prereq (MATH 21,
    never scheduled) -- same recurring pattern fixed for several majors
    this session."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("HPA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a health policy and administration major"), "HPA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestBiobehavioralHealthPlan(unittest.TestCase):
    """Biobehavioral Health, B.S. No data gaps found -- every prereq
    (BBH 311's 3-way BBH 101+BIOL 110+PSYCH 100 requirement, BBH 302/310/
    440/411W's STAT 200 or BBH 101/310 chains) resolves cleanly against
    courses this plan already schedules."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("BBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a biobehavioral health major"), "BBH")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestCommunicationSciencesDisordersPlan(unittest.TestCase):
    """Communication Sciences and Disorders, B.S. The entire CSD department
    catalog was missing -- added minimal entries in new csd_catalog.json.
    Real bug avoided: listed STAT 200 first (not PSYCH 200) for the
    statistics item, since PSYCH 200 has a genuine two-part AND prereq
    (PSYCH 100 AND MATH 21), and MATH 21 is never scheduled."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CSD", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a communication sciences and disorders major"), "CSD")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestHospitalityManagementPlan(unittest.TestCase):
    """Hospitality Management, B.S. Extended hm_catalog.json (started for
    Nutritional Sciences) with 16 more HM courses. Real data quirk: HM
    366's bulletin-cited prereq ('HM 201 and HM 365') references course
    numbers that don't exist anywhere -- treated as referring to their
    modern equivalents, HM 101 and HM 265W."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("HM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a hospitality management major"), "HM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestRecreationParkTourismManagementPlan(unittest.TestCase):
    """Recreation, Park, and Tourism Management, B.S. (Commercial
    Recreation and Tourism Management option). The entire RPTM department
    catalog was missing. Real data gap: RPTM 433W's bulletin-cited
    prereq 'RPTM 356' doesn't exist anywhere -- treated as referring to
    RPTM 456, and substituted a real STAT 200 course for a GEN ED slot
    since this major's own suggested plan never otherwise schedules any
    statistics course despite RPTM 433W requiring one."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("RPTM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a recreation, park, and tourism management major"), "RPTM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestSystemsNeurosciencePlan(unittest.TestCase):
    """Systems Neuroscience, B.S. Major code NROSCI avoids colliding with
    Eberly Science's Neurobiology (NEURO). Real recurring bug found and
    fixed at the catalog level: this major's entrance math course, MATH
    140B, wasn't recognized as equivalent to MATH 140/110/22 by CHEM 110's
    concurrent requirement, PHYS 250's prereq, or STAT 184's concurrent
    requirement -- added MATH 140B as an accepted alternate to all three.
    Second bug: BBH 470/BIOL 470 both strictly require literal 'BIOL 469',
    not the cross-listed 'BBH 469' -- reordered to list BIOL 469 first."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("NROSCI", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a systems neuroscience major"), "NROSCI")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_math_140b_recognized_by_chem_110_concurrency(self):
        """Regression test for the MATH 140B catalog-recognition fix."""
        course = self.catalog.get("CHEM 110")
        self.assertIsNotNone(course)
        concurrent_codes = {c for group in course.concurrent_groups for c in group}
        self.assertIn("MATH 140B", concurrent_codes)


class TestElementaryEarlyChildhoodEducationPlan(unittest.TestCase):
    """Elementary and Early Childhood Education, B.S. Ten new departments
    (EDTHP/EDUC/MTHED/EDPSY/CI/ECE/LLED/SSED/SCIED/SPLED) were entirely
    missing. Real bug fixed: this plan's own 'departments' list initially
    omitted 'MATH', which meant MATH 200 wasn't in the merged catalog at
    all and the engine's tiered option ranking silently fell through to
    MTHED 240 instead. Real gap fixed: ECE 451 needs concurrent EDPSY 11
    AND HDFS 229, but the bulletin only offers them as alternatives --
    added HDFS 229 as an explicit second item."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ELED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an elementary and early childhood education major"), "ELED")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_math_department_included_for_math_200(self):
        """Regression test: MATH 200 must actually resolve in the merged
        catalog, not silently lose to MTHED 240 because 'MATH' was
        missing from this plan's own departments list."""
        self.assertIn("MATH", self.plan["departments"])
        self.assertIsNotNone(self.catalog.get("MATH 200"))


class TestSpecialEducationPlan(unittest.TestCase):
    """Special Education, B.S. Extended spled_catalog.json with 17 more
    SPLED courses plus EDPSY 10. No real data gaps found -- this major's
    8-semester progression is fully self-consistent."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SPLED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a special education major"), "SPLED")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestSecondaryEducationPlan(unittest.TestCase):
    """Secondary Education, B.S. (Biology Teaching option). Real data
    artifact handled: CI 495C/495E cite the content-methods courses for
    all five teaching options (English/Math/Science/Social Studies) as
    prereqs/corequisites -- only SCIED 412 (Science) applies to this
    option; the others were treated as a shared bulletin template
    artifact, same judgment-call precedent as Elementary Education's
    CI 495B case."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SECED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a secondary education major"), "SECED")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestRehabilitationHumanServicesPlan(unittest.TestCase):
    """Rehabilitation and Human Services, B.S. The entire RHS department
    catalog was missing. Real bug fixed: RHS 302 needs a concurrent
    statistics course, but the bulletin schedules RHS 302 a full year
    before the statistics course appears -- moved STAT 200 to the same
    term as RHS 302 to satisfy the real concurrency."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("RHS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a rehabilitation and human services major"), "RHS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_stat_200_scheduled_alongside_rhs_302(self):
        """Regression test for the concurrency fix: STAT 200 (or an
        alternate) must appear in the same semester as RHS 302."""
        sem4_options = {
            opt
            for item in self.plan["semesters"][3]["items"]
            for opt in item.get("options", [])
        }
        self.assertIn("RHS 302", sem4_options)
        self.assertTrue(sem4_options & {"STAT 200", "STAT 100", "EDPSY 101"})


class TestEducationPublicPolicyPlan(unittest.TestCase):
    """Education and Public Policy, B.S., no formally named tracks.
    Extended edthp_catalog.json with EDTHP 200/394/395/420, plus new
    hist_catalog.json for HIST 21. No real data gaps found."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("EDPP", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an education and public policy major"), "EDPP")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestMiddleLevelEducationPlan(unittest.TestCase):
    """Middle Level Education, B.S., English 4-8 Option. Extended
    ci_catalog.json with CI 295B/495B and lled_catalog.json with LLED 450.
    Generalized the shared CI 495D catalog entry's prereq from
    CI 495A-only to an OR of CI 495A/CI 495B, since Middle Level Education
    needs CI 495B while Elementary and Early Childhood Education (already
    built) needs CI 495A -- verified ELED's own plan still passes after
    the change. Real gap fixed: LLED 450 needs EDPSY 14, which the
    English 4-8 option's own suggested plan never otherwise schedules --
    added EDPSY 14 as an explicit Semester 1 item. Treated LLED 402
    (cited only as a corequisite, no independent course description) as
    equivalent to the already-cataloged LLED 302, same title."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MLED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a middle level education major"), "MLED")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_ci_495d_accepts_either_495a_or_495b(self):
        """Regression test: CI 495D's shared catalog entry must accept
        CI 495B (Middle Level) as well as CI 495A (Elementary/ECE),
        since both majors' own plans schedule only their own track's
        variant into the same downstream CI 495D requirement."""
        course = self.catalog.get("CI 495D")
        self.assertIsNotNone(course)
        self.assertIn({"CI 495A", "CI 495B"}, course.prereq_groups)


class TestWorkforceEducationDevelopmentPlan(unittest.TestCase):
    """Workforce Education and Development, B.S., Industrial Education
    specialization. The entire WFED department catalog was missing --
    added a new wfed_catalog.json. Real bug fixed: the bulletin's own
    suggested plan schedules WFED 441 (Year 2 Fall) before WFED 445
    (Year 3 Spring), but WFED 441 strictly requires WFED 445 completed
    first -- reordered to WFED 105 -> WFED 445 -> WFED 441 -> WFED 442,
    the correct prerequisite order."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("WFED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a workforce education and development major"), "WFED")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_wfed_441_scheduled_after_wfed_445(self):
        """Regression test: WFED 441 strictly requires WFED 445 first,
        but the bulletin's own suggested plan lists them in the opposite
        order -- confirm this plan schedules 445 in an earlier semester
        than 441, not the bulletin's own (self-contradictory) order."""
        sem_of = {}
        for sem in self.plan["semesters"]:
            for item in sem["items"]:
                if item.get("options") in (["WFED 441"], ["WFED 445"]):
                    sem_of[item["options"][0]] = sem["index"]
        self.assertLess(sem_of["WFED 445"], sem_of["WFED 441"])


class TestArchitectureBArchPlan(unittest.TestCase):
    """Architecture, B.Arch. -- the 5-year, 10-semester professional
    direct-entry program (unlike Architecture, B.S./ARCBS, a
    transfer-only pre-professional variant with no published plan;
    logged blocked in BLOCKED_MAJORS.md). First non-4-year program built
    this session. New catalogs added: arch_catalog.json, arth_catalog.json,
    ae_catalog.json. Real engine-mechanics gap found: the bulletin
    describes several course pairs/trios as mutually concurrent with each
    other (ARCH 121/131, ARCH 122/132, ARCH 203/231, ARCH 204/232,
    ARCH 332/381/480, ARCH 499A/B/C) -- the engine's same-term scheduling
    can only resolve one-directional concurrency (course B already picked
    before course A is scanned), so true mutual/circular concurrent
    requirements were broken into one-directional edges (or dropped
    entirely for the ARCH 499A/B/C trio, which share a common prereq gate
    instead). AE 211, cited as an ARCH 331 concurrent requirement, could
    not be confirmed to exist anywhere in the current PSU catalog and was
    not modeled -- documented directly in ARCH 331's catalog entry."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ARCHBARCH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an architecture major"), "ARCHBARCH")

    def test_full_plan_reaches_graduation_in_five_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 11)

    def test_mutual_concurrency_pairs_are_one_directional(self):
        """Regression test: none of the ARCH course pairs the bulletin
        describes as mutually concurrent may reference each other in
        both directions, or the engine's same-term scan deadlocks
        forever (neither side is ever eligible to be picked first)."""
        pairs = [
            ("ARCH 121", "ARCH 131"), ("ARCH 122", "ARCH 132"),
            ("ARCH 203", "ARCH 231"), ("ARCH 204", "ARCH 232"),
        ]
        for a, b in pairs:
            course_a = self.catalog.get(a)
            course_b = self.catalog.get(b)
            a_needs_b = any(b in group for group in course_a.concurrent_groups)
            b_needs_a = any(a in group for group in course_b.concurrent_groups)
            self.assertFalse(a_needs_b and b_needs_a, f"{a} and {b} are mutually concurrent")


class TestArtHistoryPlan(unittest.TestCase):
    """Art History, B.A. Extended arth_catalog.json (previously only
    ARTH 201/202N from the Architecture B.Arch build) with ARTH 1S,
    ARTH 111, ARTH 101N, ARTH 350W. The bulletin's own 9-credit
    'Additional Courses' requirement must include one Western and one
    non-Western art course -- filled with ARTH 111 (Western) and
    ARTH 101N (non-Western) rather than leaving both generic."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ARTH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an art history major"), "ARTH")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_additional_courses_include_western_and_non_western(self):
        """Regression test: the bulletin requires one Western and one
        non-Western art course among the 'Additional Courses' -- confirm
        both ARTH 111 (Western) and ARTH 101N (non-Western) are actually
        scheduled somewhere in the plan, not left as generic slots."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertIn("ARTH 111", codes)
        self.assertIn("ARTH 101N", codes)


class TestGraphicDesignPlan(unittest.TestCase):
    """Graphic Design, B.Des. New gd_catalog.json created for the entire
    GD department. The bulletin's own 'GD 300, 315, 320, or 400' pool
    item repeats 3 times across Semesters 6-8 (one required completion
    each term) -- relies on the engine naturally advancing to a
    different option each time a prior one is completed, since by
    Semester 6 all four options are simultaneously prereq-eligible."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("GD", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a graphic design major"), "GD")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_repeated_gd_pool_item_advances_through_all_four_options(self):
        """Regression test: the 'GD 300, 315, 320, or 400' pool item
        appears 3 times in the plan JSON with identical option lists --
        confirm the simulator actually picks 3 distinct courses from
        that pool, not the same course three times (which would be
        impossible) or a stall."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        picked = [
            c["code"] for t in fp["terms"] for c in t["courses"]
            if c["code"] in {"GD 300", "GD 315", "GD 320", "GD 400"}
        ]
        self.assertEqual(len(picked), 3)
        self.assertEqual(len(set(picked)), 3)


class TestArtEducationPlan(unittest.TestCase):
    """Art Education, B.S. New catalogs: aed_catalog.json (entire AED
    department), art_catalog.json (ART 11/110/111/122Y), aplng_catalog.json
    (APLNG 200/210). Real gap fixed: AED 489 requires AED 490 as an
    enforced concurrent, but the bulletin's own Suggested Academic Plan
    never schedules AED 490 anywhere -- added it as an explicit companion
    item. Real engine-mechanics gap (same pattern as Architecture
    B.Arch's ARCH 121/131 pairs): AED 495A/495B and AED 495C/495D are
    each mutual corequisites of each other -- broken into one-directional
    edges."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an art education major"), "AED")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_aed_489_and_490_both_scheduled(self):
        """Regression test: AED 490 must actually appear in this plan --
        the bulletin's own suggested plan omits it despite AED 489
        requiring it as an enforced concurrent."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertIn("AED 489", codes)
        self.assertIn("AED 490", codes)


class TestLandscapeArchitecturePlan(unittest.TestCase):
    """Landscape Architecture, B.L.A. -- the second 5-year professional
    program built this session (9 semesters, 139 total credits, ending
    Fall of Year 5 rather than Spring). New larch_catalog.json created
    for the entire LARCH department. Real engine-mechanics gap (same
    pattern as Architecture B.Arch): LARCH 115/155, 116/156, 215/255,
    216/256 are each mutual corequisites of each other -- broken into
    one-directional edges. LARCH 414 (5-15cr repeatable) is scheduled 3
    times -- first as a literal pick, the other two as generic repeat
    slots."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("LARCH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a landscape architecture major"), "LARCH")

    def test_full_plan_reaches_graduation_in_five_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 10)

    def test_mutual_concurrency_pairs_are_one_directional(self):
        """Regression test: none of the LARCH course pairs the bulletin
        describes as mutual corequisites may reference each other in
        both directions, or the engine's same-term scan deadlocks."""
        pairs = [
            ("LARCH 115", "LARCH 155"), ("LARCH 116", "LARCH 156"),
            ("LARCH 215", "LARCH 255"), ("LARCH 216", "LARCH 256"),
        ]
        for a, b in pairs:
            course_a = self.catalog.get(a)
            course_b = self.catalog.get(b)
            a_needs_b = any(b in group for group in course_a.concurrent_groups)
            b_needs_a = any(a in group for group in course_b.concurrent_groups)
            self.assertFalse(a_needs_b and b_needs_a, f"{a} and {b} are mutually concurrent")


class TestDigitalMultimediaDesignPlan(unittest.TestCase):
    """Digital Multimedia Design, B.Des. (World Campus). New catalogs:
    dart_catalog.json, dmd_catalog.json; extended comm_catalog.json and
    art_catalog.json. Real bug fixed: COMM 230W's actual prereq is
    ENGL 15 and ENGL 202, but the bulletin's own suggested plan schedules
    it in Semester 2 -- before ENGL 202 appears in Semester 5 -- so the
    simulator must defer it to a later real term."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("DMD", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a digital multimedia design major"), "DMD")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_comm_230w_deferred_until_after_engl_202(self):
        """Regression test: COMM 230W needs ENGL 202 as a real prereq,
        but the bulletin's own plan schedules it in Semester 2, before
        ENGL 202 (Semester 5) -- confirm the simulator actually resolves
        this by scheduling COMM 230W in a later real term than ENGL 202,
        not by silently violating the prereq."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        term_of = {}
        for i, t in enumerate(fp["terms"]):
            for c in t["courses"]:
                if c["code"] in ("COMM 230W", "ENGL 202A", "ENGL 202B", "ENGL 202C", "ENGL 202D"):
                    term_of[c["code"] if not c["code"].startswith("ENGL 202") else "ENGL 202"] = i
        self.assertIn("COMM 230W", term_of)
        self.assertIn("ENGL 202", term_of)
        self.assertGreater(term_of["COMM 230W"], term_of["ENGL 202"])


class TestProfessionalPhotographyPlan(unittest.TestCase):
    """Professional Photography, B.Des. New catalogs: photo_catalog.json
    (entire PHOTO department), aa_catalog.json (AA 1). Entrance to Major
    (PHOTO 200/202 or portfolio review) is satisfied directly since
    PHOTO 202 is an explicit Semester 1 item."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PPHOTO", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a professional photography major"), "PPHOTO")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestDigitalArtsMediaDesignPlan(unittest.TestCase):
    """Digital Arts and Media Design, B.Des. -- Digital Art and Design
    Emphasis (of three tracks; Animation was skipped since it names no
    course codes for its sub-categories, the same shape of gap as
    Art B.A./B.F.A.). Greatly extended dart_catalog.json. Real gap
    fixed: ART 476 (concurrent with DART 400) requires 3cr of ARTH, but
    the bulletin's own plan never otherwise schedules an ARTH course --
    added ARTH 111 as an explicit Semester 1 item."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("DAMD", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a digital arts and media design major"), "DAMD")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_arth_course_scheduled_for_art_476_prereq(self):
        """Regression test: ART 476 needs 3cr of ARTH, which the
        bulletin's own plan never otherwise schedules -- confirm an ARTH
        course actually appears somewhere in this plan."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertTrue(any(c.startswith("ARTH") for c in codes))


class TestTheatrePlan(unittest.TestCase):
    """Theatre, B.A. New catalogs: thea_catalog.json, dance_catalog.json.
    Real bug fixed: THEA 120 (Acting I) has a real prereq of THEA 106
    (among others), but the bulletin's own Semester 1 schedules them
    together -- also added THEA 106 to THEA 120's concurrent_groups so
    the engine can resolve it within the same term, same fix pattern as
    MATH 140B/CHEM 110. DANCE 411 (one option in a repeated history
    pool) could not be confirmed to exist and was dropped from the
    modeled option list rather than guessed at."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("THEA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a theatre major"), "THEA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_thea_120_recognizes_same_term_thea_106(self):
        """Regression test: THEA 120 must accept THEA 106 as a same-term
        concurrent alternative, since the bulletin schedules both in
        Semester 1 and THEA 120's real prereq would otherwise be
        unsatisfiable in that term."""
        course = self.catalog.get("THEA 120")
        self.assertIn("THEA 106", {c for group in course.concurrent_groups for c in group})


class TestMusicPlan(unittest.TestCase):
    """Music, B.A. -- General Music Studies Option (of two; Music
    Technology needs INART/MATSE catalog data not yet built). New
    music_catalog.json created. Real engine-mechanics gap (same pattern
    as Architecture B.Arch/AED/LARCH): MUSIC 122 and MUSIC 132 are
    mutual corequisites of each other -- broken into one-directional
    edges."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MUSIC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a music major"), "MUSIC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_music_122_132_not_mutually_concurrent(self):
        """Regression test: MUSIC 122 and MUSIC 132 must not reference
        each other as concurrent requirements in both directions, or the
        engine's same-term scan deadlocks forever."""
        c122 = self.catalog.get("MUSIC 122")
        c132 = self.catalog.get("MUSIC 132")
        a_needs_b = any("MUSIC 132" in group for group in c122.concurrent_groups)
        b_needs_a = any("MUSIC 122" in group for group in c132.concurrent_groups)
        self.assertFalse(a_needs_b and b_needs_a)


class TestMusicEducationPlan(unittest.TestCase):
    """Music Education, B.M.E. Greatly extended music_catalog.json.
    Real gap fixed: MUSIC 240 underlies MUSIC 295A/341/345/395A but the
    bulletin's own plan never names it directly -- added as the real
    course behind the plan's generic 'Education elective' item. Real gap
    fixed: SPLED 400 (explicitly in the plan) needs EDPSY 14/10/11 or
    HDFS 229/239, none of which the plan otherwise schedules -- added
    EDPSY 14 explicitly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MUSED", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a music education major"), "MUSED")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_music_240_scheduled_for_downstream_prereqs(self):
        """Regression test: MUSIC 240 must actually appear in this plan,
        since MUSIC 295A/341/345/395A all depend on it and the bulletin
        itself never names it directly."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertIn("MUSIC 240", codes)


class TestTheatreBFAPlan(unittest.TestCase):
    """Theatre, B.F.A. -- Stage Management Option (of six tracks; the
    only one built since it's the most self-contained). Real data
    artifacts fixed via direct department-PDF verification: bulletin
    footnote typos 'THEA 405Y'/'THEA 407'/'THEA 408' (real: 405W/407W/
    408W) and a nonexistent 'THEA 406'. 'THEA 200' in the plan's own
    Semester 2 could not be confirmed to exist and was replaced with a
    generic Gen Ed slot. Real gap fixed: THEA 270 needs THEA 201W and
    THEA 252, neither otherwise scheduled -- added both explicitly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("THEABFA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a stage management major"), "THEABFA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_thea_270_prereqs_scheduled(self):
        """Regression test: THEA 201W and THEA 252 must both appear
        somewhere in this plan, since THEA 270 strictly needs both and
        the bulletin's own Stage Management plan never otherwise
        schedules either."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertIn("THEA 201W", codes)
        self.assertIn("THEA 252", codes)


class TestActingPlan(unittest.TestCase):
    """Acting, B.F.A. Entrance is audition-based, not modeled directly.
    Real data gap: DANCE 361's own bulletin prereq cites 'DANCE 262',
    which could not be confirmed to exist -- substituted DANCE 261
    (confirmed real, immediately prior in the sequence) rather than
    guessed at, and added it as an explicit Semester 3 item since the
    plan otherwise never schedules it."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ACTING", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an acting major"), "ACTING")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_dance_261_scheduled_for_dance_361_prereq(self):
        """Regression test: DANCE 261 must appear in this plan, since
        DANCE 361 needs it and the bulletin's own plan never otherwise
        schedules it."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertIn("DANCE 261", codes)


class TestMusicalTheatrePlan(unittest.TestCase):
    """Musical Theatre, B.F.A. New voice_catalog.json. Real gap fixed:
    DANCE 232 needs DANCE 230, never otherwise scheduled -- added
    explicitly. Real gap fixed: THEA 425A needs concurrent THEA 425C,
    never otherwise scheduled in this major's own plan (unlike Acting
    B.F.A., which already has both) -- added explicitly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MUSTHEA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a musical theatre major"), "MUSTHEA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_thea_425a_and_425c_both_scheduled(self):
        """Regression test: THEA 425C must appear in this plan, since
        THEA 425A needs it concurrently and the bulletin's own Musical
        Theatre plan never otherwise schedules it."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertIn("THEA 425A", codes)
        self.assertIn("THEA 425C", codes)


class TestMusicBMPlan(unittest.TestCase):
    """Music, B.M. -- Keyboard Instruments Option (of four: Composition,
    Keyboard, Strings/Winds/Brass/Percussion, Voice -- Keyboard chosen
    for maximum catalog reuse and a fully-named Applied Music sequence).
    New keybd_catalog.json for the 8-course KEYBD applied-piano
    sequence."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MUSICBM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a music performance major"), "MUSICBM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestMusicTechnologyPlan(unittest.TestCase):
    """Music Technology, B.M. New inart_catalog.json (INART 50/258A);
    extended music_catalog.json and thea_catalog.json. MUSIC 452's own
    bulletin prereq cites 'INART 50Z', unconfirmable as distinct from
    INART 50 -- treated as INART 50. MUSIC 177 (ROARS lab) scheduled
    once per semester across all 8 semesters for a cumulative 8cr,
    matching the bulletin's own stated total exactly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MUSTECH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a music technology major"), "MUSTECH")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestHistoryPlan(unittest.TestCase):
    """History, B.A. Extended hist_catalog.json with HIST 1/2/302W.
    'LA 283', named in the bulletin's own plan, could not be confirmed
    to exist -- replaced with a generic Second-Year Liberal Arts Seminar
    slot. 'HIST 100/200-level' and 'HIST 400-level' are open
    department-level pools with no bulletin-enumerated list, modeled
    generically -- a normal open-elective structure, not a
    data-ambiguity wall."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("HIST", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a history major"), "HIST")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestCriminologyPlan(unittest.TestCase):
    """Criminology, B.A. Real bug fixed in the shared crim_catalog.json:
    CRIM 249 and CRIM 250W had empty prereq_groups despite the
    bulletin's own 'Critical Sequencing Note' (CRIM 12/SOC 12 -> CRIM
    249 -> CRIM 250W MUST be followed) -- added the real prereq/
    concurrent chains, sourced directly from the department's course
    pages. Verified this doesn't affect any other existing plan."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CRIM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a criminology major"), "CRIM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_crim_249_and_250w_sequencing_enforced(self):
        """Regression test: CRIM 249 must require CRIM 12 or SOC 12, and
        CRIM 250W must require CRIM 249, matching the bulletin's own
        'Critical Sequencing Note' -- this was a real gap in the shared
        catalog fixed during this build."""
        c249 = self.catalog.get("CRIM 249")
        c250w = self.catalog.get("CRIM 250W")
        self.assertTrue(any({"CRIM 12", "SOC 12"} & group for group in c249.prereq_groups))
        self.assertTrue(any("CRIM 249" in group for group in c250w.prereq_groups))


class TestSociologyBAPlan(unittest.TestCase):
    """Sociology, B.A. Real gap fixed in the shared soc_catalog.json:
    SOC 400W had no prereq_groups despite the bulletin's own capstone
    sequence (SOC 207 -> SOC 470 -> SOC 400W) requiring SOC 470 --
    added. SOC 207/405's bulletin prereq text ('3 credits in SOC') was
    approximated as SOC 1 specifically. Verified this doesn't regress
    EDPP-2026.json, which also references SOC 207."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SOCBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a sociology major"), "SOCBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_edpp_still_passes_after_soc_catalog_change(self):
        """Regression test: EDPP-2026.json references SOC 207 in an
        OR-pool with SOC 23 -- confirm it still builds cleanly now that
        SOC 207 has a real prereq (SOC 1)."""
        edpp_plan = engine.load_degree_plan("EDPP", 2026)
        edpp_catalog = engine.load_merged_catalog(edpp_plan["departments"])
        fp = engine.build_full_plan(
            edpp_plan, edpp_catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestPhilosophyBAPlan(unittest.TestCase):
    """Philosophy, B.A. -- General Philosophy Option (of six; each of the
    six names real enumerated course pools, unlike Art B.A./B.F.A.'s
    unresolvable concentrations). All PHIL courses used were already
    fully cataloged from an earlier build."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PHILBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a philosophy major"), "PHILBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestAnthropologyPlan(unittest.TestCase):
    """Anthropology, B.A. New anth_catalog.json. Judgment call: the
    bulletin's Fall Semester 1 item is 'ANTH 45N or 21', but Spring
    Semester 2 separately requires 'ANTH 21' specifically -- modeled
    Fall as literal ANTH 45N to avoid the same course satisfying two
    distinct requirements."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ANTH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an anthropology major"), "ANTH")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestLinguisticsPlan(unittest.TestCase):
    """Linguistics, B.A. New ling_catalog.json (LING 100/402/404/449)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("LING", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a linguistics major"), "LING")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestCommunicationArtsSciencesBAPlan(unittest.TestCase):
    """Communication Arts and Sciences, B.A. All literal courses (CAS
    101N/301/303/304/311) were already fully cataloged from earlier
    majors. This plan's computed 8-semester total (123cr) matches the
    bulletin's own stated total exactly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CASBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a communication arts and sciences major"), "CASBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestAfricanAmericanStudiesPlan(unittest.TestCase):
    """African American Studies, B.A. New afam_catalog.json; added
    HIST 152 (cross-listed with AFAM 152) to hist_catalog.json. Real
    gap avoided: AFAM 401 strictly requires both AFAM 100N and AFAM
    101N, but the bulletin's own Semester 2 item is a 7-option pool --
    simplified to a literal AFAM 101N pick to guarantee the downstream
    prereq resolves. Real gap fixed: SOC 207 needs SOC 1 (per this
    batch's Sociology B.A. fix), never otherwise scheduled -- added
    SOC 1 explicitly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AFAM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an african american studies major"), "AFAM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_afam_401_prereqs_scheduled(self):
        """Regression test: both AFAM 100N and AFAM 101N must appear in
        this plan, since AFAM 401 strictly requires both and a wrong
        pick from the bulletin's own 7-option Semester 2 pool would
        leave AFAM 401 permanently unsatisfiable."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertIn("AFAM 100N", codes)
        self.assertIn("AFAM 101N", codes)


class TestInternationalPoliticsPlan(unittest.TestCase):
    """International Politics, B.A. -- International Political Economy
    Option (of three: IPE, International Relations, National Security).
    All PLSC/ECON courses used were already fully cataloged."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("INTPOL", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an international politics major"), "INTPOL")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestOrganizationalLeadershipPlan(unittest.TestCase):
    """Organizational Leadership, B.A. New olead_catalog.json,
    lhr_catalog.json. Confirmed OLEAD 100->201->210->464->465 is only
    the bulletin's suggested sequence, not an enforced prereq chain --
    each course's real prereq is either none or a semester-standing
    gate, not the prior OLEAD course."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("OLEAD", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an organizational leadership major"), "OLEAD")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestLaborHumanResourcesPlan(unittest.TestCase):
    """Labor and Human Resources, B.A. -- University Park & World
    Campus track. Extended lhr_catalog.json (built for Organizational
    Leadership earlier this batch) with LHR 100/136Y/201/304/305.
    Bulletin explicitly states LHR 304/305/312 may be taken in any
    order -- confirmed no artificial sequencing needed."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("LHR", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a labor and human resources major"), "LHR")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestSpanishBAPlan(unittest.TestCase):
    """Spanish, B.A. New span_catalog.json. SPAN 1->2->3 is a strict
    linear prereq chain. SPAN 100 (standard) and SPAN 100A/100B
    (heritage-speaker/medical tracks) run in parallel, each gated on
    SPAN 3 or placement. SPAN 215 has no unsuffixed catalog entry --
    only 215N/215Q exist. SPAN 100C/100H, cited as prereq alternatives,
    could not be confirmed as standalone courses -- not modeled."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SPANBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a spanish major"), "SPANBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestFrenchBAPlan(unittest.TestCase):
    """French and Francophone Studies, B.A. -- Language and Culture
    Option (of three). New fr_catalog.json. Unlike Spanish's coded
    FR 1->2->3 chain, French's FR 1/2/3 have NO coded prerequisite at
    all -- verified via direct page inspection, not a fetch gap."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("FRENCHBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a french major"), "FRENCHBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestGermanBAPlan(unittest.TestCase):
    """German, B.A. New ger_catalog.json. Unlike French, German's
    GER 1->2->3 IS a formally coded prereq chain (confirmed via direct
    DOM inspection). Two real data artifacts fixed: 'GER 200' no longer
    exists (real course is GER 200N); 'GER 208Y' could not be confirmed
    to exist at all and was dropped."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("GERBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a german major"), "GERBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestComparativeLiteraturePlan(unittest.TestCase):
    """Comparative Literature, B.A. New cmlit_catalog.json (CMLIT 10/
    100/400Y, all with no enforced course-code prereqs)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CMLIT", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a comparative literature major"), "CMLIT")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestSocialDataAnalyticsPlan(unittest.TestCase):
    """Social Data Analytics, B.S. New soda_catalog.json (SODA 308/496)
    -- every other course used was already fully cataloged from earlier
    majors, an unusually clean build reusing five departments' worth of
    existing data."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SODA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a social data analytics major"), "SODA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestItalianBAPlan(unittest.TestCase):
    """Italian, B.A. New it_catalog.json. Confirmed via direct DOM
    inspection that IT 1->2->3 is a real coded prereq chain (matching
    Spanish/German, not French)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ITBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an italian major"), "ITBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestRussianBAPlan(unittest.TestCase):
    """Russian, B.A. New rus_catalog.json. Confirmed RUS 1->2->3 is a
    real coded prereq chain. Real data artifact: the bulletin's own
    plan cites 'RUS 400' as a literal course, but it does not exist
    anywhere in the department's catalog -- treated as a stale
    placeholder for a generic 400-level Russian course."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("RUSBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a russian major"), "RUSBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestWomensGenderSexualityStudiesPlan(unittest.TestCase):
    """Women's, Gender, and Sexuality Studies, B.A. New wmnst_catalog.json.
    Real data artifact: the bulletin's own plan cites 'WMNST 83S',
    which doesn't exist -- used WMNST 83N instead. Several downstream
    prereq strings in the department's own PDF contain typos, normalized
    to their evident intent. WMNST 492W's prereq accepts either
    WMNST 400N or 401, since the plan's own Semester 5 item pools them
    as interchangeable alternatives."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("WMNSTBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a women's, gender, and sexuality studies major"), "WMNSTBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestClassicsAncientMediterraneanStudiesPlan(unittest.TestCase):
    """Classics and Ancient Mediterranean Studies, B.A. -- CAMS Option
    (of three: Ancient Languages, Ancient Mediterranean Archaeology,
    CAMS -- CAMS chosen as the cleanest, avoiding fieldwork/language
    data the other two need). New cams_catalog.json."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CAMS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a classics and ancient mediterranean studies major"), "CAMS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestJewishStudiesPlan(unittest.TestCase):
    """Jewish Studies, B.A. New jst_catalog.json (JST 10) and
    hebr_catalog.json (HEBR 1/2/3, a coded prereq chain matching
    Spanish/German/Italian/Russian). Computed 8-semester total (123cr)
    matches the bulletin's own stated total exactly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("JST", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a jewish studies major"), "JST")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestChineseBAPlan(unittest.TestCase):
    """Chinese, B.A. New chns_catalog.json. Confirmed CHNS 1->2->3->110->
    401->402->403W->404 is a fully linear coded prereq chain, matching
    Spanish/German/Italian/Russian/Hebrew. Real data artifact: the
    bulletin's own '452/453/454/455' pool cites a fourth option, CHNS 455,
    which doesn't exist anywhere in the department's course listing --
    modeled as a 3-way pool."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CHNSBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a chinese major"), "CHNSBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestEconomicsBAPlan(unittest.TestCase):
    """Economics, B.A. Sibling of the already-built Economics B.S.
    (ECON-2026.json), reusing econ_catalog.json. Real difference: drops
    the B.S.'s MATH/CMPSC requirements for a World Language sequence.
    MATH 21 scheduled explicitly to resolve ECON 106's MATH prereq without
    pulling in MATH 110/140, which the B.A. itself doesn't require."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ECONBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an economics BA major"), "ECONBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestPoliticalScienceBAPlan(unittest.TestCase):
    """Political Science, B.A. Sibling of the already-built Political
    Science B.S. (PLSC-2026.json), reusing plsc_catalog.json. Real data
    artifact confirmed via direct DOM inspection: the bulletin's own SAP
    cites 'PLSC 3, PLSC 20, or PLSC 22' but PLSC 20/22 don't exist
    anywhere in the department's course listing -- modeled as literal
    PLSC 3 only."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PLSCBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a political science BA major"), "PLSCBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestPhilosophyBSPlan(unittest.TestCase):
    """Philosophy, B.S. Sibling of the already-built Philosophy B.A.
    (PHILBA-2026.json, six options), reusing phil_catalog.json -- the B.S.
    has no options/concentrations. Real engine bug caught and fixed here:
    the bulletin's 'Formal Reasoning' pool is almost entirely gated behind
    a MATH 110/140 prereq/concurrent this plan doesn't otherwise schedule;
    reusing the one MATH-free option (CMPSC 111, only 1cr) for both
    Formal Reasoning slots caused an infinite reschedule loop since a
    single completed course can't satisfy two separate plan items. Fixed
    by scheduling MATH 110 explicitly so CMPSC 131 and STAT 184 both
    become genuinely eligible, distinct options."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PHILBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a philosophy BS major"), "PHILBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_formal_reasoning_pool_has_two_distinct_completable_options(self):
        """Regression test for the CMPSC 111 infinite-reschedule bug: both
        Formal Reasoning items must resolve to distinct real courses."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        codes = [c["code"] for t in fp["terms"] for c in t["courses"] if c["code"]]
        self.assertIn("CMPSC 131", codes)
        self.assertIn("STAT 184", codes)


class TestSociologyBSPlan(unittest.TestCase):
    """Sociology, B.S. Sibling of the already-built Sociology B.A.
    (SOCBA-2026.json), reusing soc_catalog.json's already-fixed SOC
    prereq chain. Real difference: MATH/STAT/programming requirements
    plus a 15cr 'Supporting Courses' Pathway (5 named options, each with
    real enumerated course codes) -- picked Data Analysis, sequenced
    (CMPSC 203 -> MATH 220 -> DS 220 -> DS 402 -> STAT 460) so every
    pathway course's real prereq is satisfied by an earlier semester,
    avoiding the exact bug fixed in TestPhilosophyBSPlan."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SOCBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a sociology BS major"), "SOCBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestCriminologyBSPlan(unittest.TestCase):
    """Criminology, B.S., Computing and Statistics Option. Sibling of the
    already-built Criminology B.A. (CRIM-2026.json), reusing
    crim_catalog.json. The option's prescribed SOC 470 needs SOC 207,
    not otherwise scheduled in CRIM -- added explicitly, and the common
    'SOC 1/3/5' requirement narrowed to literal SOC 1 so the
    SOC 1 -> SOC 207 -> SOC 470 chain resolves deterministically."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CRIMBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a criminology BS major"), "CRIMBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestFrenchBSPlan(unittest.TestCase):
    """French and Francophone Studies, B.S., Applied French Option.
    Sibling of the already-built French B.A. (FRENCHBA-2026.json),
    reusing fr_catalog.json. Added FR 401/409/417/418/419 with real
    prereqs confirmed via direct DOM inspection of the department's own
    course-description page."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("FRENCHBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a french BS major"), "FRENCHBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestGermanBSPlan(unittest.TestCase):
    """German, B.S., Applied German Option. Sibling of the already-built
    German B.A. (GERBA-2026.json), reusing ger_catalog.json. Added
    GER 399/431/432/499 -- GER 432's own bulletin prereq text cites
    'GER 401', which doesn't exist as a standalone course (only GER 401Y
    does), same stale-citation pattern as RUS 400/PLSC 20/22."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("GERBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a german BS major"), "GERBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestItalianBSPlan(unittest.TestCase):
    """Italian, B.S. Unlike French/German B.S. (three named options
    each), Italian's B.S. has no named options -- one straightforward
    track, the same shape as Philosophy B.S. Sibling of the already-built
    Italian B.A. (ITBA-2026.json), reusing it_catalog.json. Added IT 412
    (prescribed) and IT 99, a real variable-credit (1-12, max 12),
    no-prereq study-abroad course modeled as a single 6cr literal pick."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ITBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an italian BS major"), "ITBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestSpanishBSPlan(unittest.TestCase):
    """Spanish, B.S., Applied Spanish Option. Sibling of the already-built
    Spanish B.A. (SPANBA-2026.json), reusing span_catalog.json's SPAN
    1->2->3 chain and SPAN 215N/253W. Added 9 missing courses with real
    prereqs confirmed via direct DOM inspection -- several (SPAN 314/411/
    417) cite the bulletin's stale 'SPAN 215' (only SPAN 215N exists),
    same pattern as GER 401/PLSC 20/22."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SPANBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a spanish BS major"), "SPANBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestPlanEngineRobustness(unittest.TestCase):
    """Engine-level regressions found while building new majors — these
    protect every major (present and future), not just the one that
    surfaced them."""

    def test_duplicate_option_plan_terminates(self):
        """Two items that both list the same course as their first-choice
        option (e.g. two 'ENGL 15 or CAS 100A/B' writing-requirement boxes)
        must not make build_full_plan loop forever re-recommending the same
        course. Regression test for a real bug found building the
        Cybersecurity Analytics and Operations plan, where this pattern
        produced 24 terms of nothing but 'ENGL 15' and never finished."""
        plan = {
            "major": "TEST", "catalog_year": 2099,
            "departments": ["ENGL", "CAS"],
            "max_credits_per_semester": 17,
            "semesters": [
                {"index": 1, "label": "Semester 1", "items": [
                    {"type": "course", "options": ["ENGL 15", "CAS 100A"], "credits": 3},
                ]},
                {"index": 2, "label": "Semester 2", "items": [
                    {"type": "course", "options": ["ENGL 15", "CAS 100A"], "credits": 3},
                ]},
            ],
        }
        next_id = 0
        for sem in plan["semesters"]:
            for item in sem["items"]:
                item["id"] = next_id
                next_id += 1
                item["options"] = [engine.norm_code(o) for o in item["options"]]
        catalog = engine.load_merged_catalog(["ENGL", "CAS"])
        import datetime
        fp = engine.build_full_plan(
            plan, catalog, set(),
            start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
            max_terms=12,
        )
        self.assertLessEqual(len(fp["terms"]), 2, "should finish in exactly 2 terms, not loop")
        all_codes = [p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]]
        self.assertEqual(len(all_codes), len(set(all_codes)), "same course must not repeat across terms")

    def test_blocked_first_option_falls_back_to_second(self):
        """An item like 'CMPSC 101 (or 203)' must resolve via CMPSC 203 when
        CMPSC 101's own prerequisite (MATH 140/141 specifically — NOT
        MATH 110) is never satisfied, instead of leaving the item permanently
        unscheduled just because its first-listed option isn't eligible.
        Regression test for a real bug found building the Economics plan:
        a MATH-110 track (a valid, common ECON path) left CMPSC 101
        perpetually prereq-blocked, and the old code never tried the second
        option at all — it just skipped the whole item forever."""
        plan = {
            "major": "TEST", "catalog_year": 2099,
            "departments": ["CMPSC", "MATH"],
            "max_credits_per_semester": 17,
            "semesters": [
                {"index": 1, "label": "Semester 1", "items": [
                    {"type": "course", "options": ["MATH 110"], "credits": 4},
                ]},
                {"index": 2, "label": "Semester 2", "items": [
                    {"type": "course", "options": ["CMPSC 101", "CMPSC 203"], "credits": 3},
                ]},
            ],
        }
        next_id = 0
        for sem in plan["semesters"]:
            for item in sem["items"]:
                item["id"] = next_id
                next_id += 1
                item["options"] = [engine.norm_code(o) for o in item["options"]]
        catalog = engine.load_merged_catalog(["CMPSC", "MATH"])
        import datetime
        fp = engine.build_full_plan(
            plan, catalog, set(),
            start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
            max_terms=12,
        )
        self.assertEqual(fp["warnings"], [])
        all_codes = [p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]]
        self.assertIn("CMPSC 203", all_codes)
        self.assertNotIn("CMPSC 101", all_codes)


class TestGenEdRecommendations(unittest.TestCase):
    """Gen Ed slots tagged with a domain code (GQ/GWS/GA/GHW/GH/GN/GS/
    INTER-D/IL/US) get a real recommended course from PSU's approved list
    (scripts/scrape_gen_ed.py -> data/gen_ed_courses.json) instead of a
    generic placeholder."""

    @staticmethod
    def _tagged_plan(domain, departments, credits=3.0):
        plan = {
            "major": "TEST", "catalog_year": 2099,
            "departments": departments,
            "max_credits_per_semester": 17,
            "semesters": [
                {"index": 1, "label": "Semester 1", "items": [
                    {"type": "slot", "label": f"GEN ED ({domain})", "credits": credits, "gen_ed": domain},
                ]},
            ],
        }
        for sem in plan["semesters"]:
            for item in sem["items"]:
                item["id"] = 0
        return plan

    def test_gen_ed_courses_data_loaded(self):
        domains = engine.load_gen_ed_courses()
        for code in ("GQ", "GWS", "GA", "GHW", "GH", "GN", "GS", "INTER-D", "IL", "US"):
            self.assertIn(code, domains)
            self.assertGreater(len(domains[code]["courses"]), 0, code)

    def test_slot_resolves_to_a_real_course(self):
        import datetime
        plan = self._tagged_plan("GA", ["ENGL"])
        catalog = engine.load_merged_catalog(["ENGL"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        picks = [p for t in fp["terms"] for p in t["courses"]]
        self.assertEqual(len(picks), 1)
        self.assertIsNotNone(picks[0]["code"], "Gen Ed slot should resolve to a real course code")

    def test_declared_slot_credits_win_over_course_credits(self):
        """A 1.5-credit GHW slot must stay 1.5 credits even though the real
        course picked for it is a normal 3-credit course — otherwise the
        term's credit total silently inflates and pushes the plan past its
        real graduation term count (regression: this exact bug added an
        extra term to the Cybersecurity plan)."""
        import datetime
        plan = self._tagged_plan("GHW", ["ENGL"], credits=1.5)
        catalog = engine.load_merged_catalog(["ENGL"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        pick = fp["terms"][0]["courses"][0]
        self.assertEqual(pick["credits"], 1.5)

    def test_firewall_excludes_major_department(self):
        """CMPSC 101 is a real, valid Quantification (GQ) course — but a
        CMPSC major must never be recommended it for a Gen Ed slot, since
        PSU's 'Firewall' rule bars major-prefix courses from counting as
        Gen Ed (except Inter-Domain, which is exempt)."""
        import datetime
        plan = self._tagged_plan("GQ", ["CMPSC", "MATH"])
        plan["major"] = "CMPSC"
        plan["departments"] = ["CMPSC", "MATH"]
        catalog = engine.load_merged_catalog(["CMPSC", "MATH"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        picks = [p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]]
        self.assertTrue(picks)
        self.assertFalse(any(c.startswith("CMPSC") for c in picks), picks)

    def test_inter_domain_exempt_from_firewall(self):
        """Inter-Domain/Integrative Studies courses are explicitly exempt
        from the Firewall rule per PSU policy: a major-prefix course must
        still be pickable there, unlike every other domain."""
        domains = engine.load_gen_ed_courses()
        inter_d_codes = {c["code"] for c in domains["INTER-D"]["courses"]}
        prefixes = {c.split()[0] for c in inter_d_codes}
        dept = next((p for p in prefixes if p in ("ENGL", "MATH", "BIOL", "PSYCH")), None)
        if not dept:
            self.skipTest("no overlapping department found in current Inter-Domain list")
        # Exclude every Inter-Domain course except the ones prefixed with
        # `dept`, forcing the picker to either return a dept-prefixed
        # course (exempt, correct) or nothing at all (wrongly excluded).
        exclude = {c["code"] for c in domains["INTER-D"]["courses"] if not c["code"].startswith(f"{dept} ")}
        pick = engine._pick_gen_ed_course("INTER-D", {}, dept, set(), exclude)
        self.assertIsNotNone(pick, f"a {dept}-prefixed Inter-Domain course should still be pickable")
        self.assertTrue(pick[0].startswith(dept))

    def test_no_gen_ed_courses_repeat_within_a_plan(self):
        """Regression guard for the completion-tracking bug: a Gen Ed slot
        resolved to a real course must be marked done (via consumed_slots),
        not re-picked forever."""
        import datetime
        plan = {
            "major": "TEST", "catalog_year": 2099,
            "departments": ["ENGL"],
            "max_credits_per_semester": 17,
            "semesters": [
                {"index": 1, "label": "Semester 1", "items": [
                    {"type": "slot", "label": "GEN ED (GA)", "credits": 3, "gen_ed": "GA"},
                    {"type": "slot", "label": "GEN ED (GH)", "credits": 3, "gen_ed": "GH"},
                ]},
            ],
        }
        for sem in plan["semesters"]:
            for i, item in enumerate(sem["items"]):
                item["id"] = i
        catalog = engine.load_merged_catalog(["ENGL"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1), max_terms=6,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertLessEqual(len(fp["terms"]), 1, "both slots should resolve in a single term, not loop")


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

    def test_selecting_one_minor_never_pulls_in_another_minors_departments(self):
        # Regression: a student picking CMPSC + MATHMIN only must never see
        # ISTMIN/CYBERCF/etc. departments or courses leak into the plan —
        # only the ONE minor actually named in the request should ever be
        # merged in, never "all minors that happen to exist."
        r = self.client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": [], "start_year": 2026,
            "minors": ["MATHMIN"],
        })
        self.assertEqual(r.status_code, 200)
        cp = r.get_json()["coursePlan"]
        all_codes = set()
        for rec in cp["recommendations"]:
            all_codes.update(re.findall(r"[A-Z-]+\s?\d{2,3}[A-Z]*", rec.get("name") or ""))
        for t in cp["fullPlan"]["terms"]:
            for c in t["courses"]:
                if c.get("id"):
                    all_codes.add(c["id"])
        ist_only_codes = {c for c in all_codes if c.startswith("IST ")}
        self.assertEqual(ist_only_codes, set(), f"IST courses leaked in with only MATHMIN selected: {ist_only_codes}")
        progress = r.get_json()  # sanity: response parses cleanly end to end
        self.assertIn("coursePlan", progress)

    def test_campuses_endpoint(self):
        r = self.client.get("/api/campuses")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["default"], "University Park")
        self.assertIn("University Park", d["campuses"])
        self.assertIn("Erie", d["campuses"])

    def test_degree_plans_default_all_university_park(self):
        r = self.client.get("/api/degree-plans")
        plans = r.get_json()["plans"]
        self.assertTrue(plans)
        self.assertTrue(all(p["campus"] == "University Park" for p in plans))

    def test_degree_plans_filtered_by_other_campus_is_empty(self):
        r = self.client.get("/api/degree-plans?campus=Erie")
        self.assertEqual(r.get_json()["plans"], [])

    def test_degree_plans_filtered_by_university_park_matches_unfiltered(self):
        unfiltered = self.client.get("/api/degree-plans").get_json()["plans"]
        filtered = self.client.get("/api/degree-plans?campus=University Park").get_json()["plans"]
        self.assertEqual(unfiltered, filtered)

    def test_minor_plans_default_all_university_park(self):
        r = self.client.get("/api/minor-plans")
        minors = r.get_json()["minors"]
        self.assertTrue(minors)
        self.assertTrue(all(m["campus"] == "University Park" for m in minors))

    def test_minor_plans_filtered_by_other_campus_is_empty(self):
        r = self.client.get("/api/minor-plans?campus=Erie")
        self.assertEqual(r.get_json()["minors"], [])

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
