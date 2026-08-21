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


class TestPlanLoaderCaching(unittest.TestCase):
    """load_degree_plan/load_minor_plan/list_degree_plans/list_minor_plans
    are @lru_cache'd (Tier-0 scaling fix: every /api/plan call was doing a
    full os.listdir + json.load from scratch). Safe only because nothing
    mutates the cached object in place -- merge_plans always
    copy.deepcopy()s before widening anything, and every other reader
    just reads. These tests prove both halves: caching is actually
    active, and a plan pulled through the merge path doesn't corrupt the
    cached original for the next caller."""

    def test_load_degree_plan_returns_the_same_cached_object_twice(self):
        a = engine.load_degree_plan("CMPSC")
        b = engine.load_degree_plan("CMPSC")
        self.assertIs(a, b)

    def test_list_degree_plans_returns_the_same_cached_object_twice(self):
        a = engine.list_degree_plans()
        b = engine.list_degree_plans()
        self.assertIs(a, b)

    def test_merging_a_cached_plan_does_not_mutate_the_cached_original(self):
        cmpsc = engine.load_degree_plan("CMPSC")
        original_item_count = sum(1 for _ in engine._iter_plan_items(cmpsc))
        statmin = engine.load_minor_plan("STATMIN", 2026)
        engine.merge_plans(cmpsc, minors=[statmin])
        # merge_plans must deep-copy before widening -- the cached CMPSC
        # plan every other concurrent request sees has to come back
        # unchanged, not gain the minor's extra items.
        cmpsc_again = engine.load_degree_plan("CMPSC")
        self.assertIs(cmpsc, cmpsc_again)
        self.assertEqual(
            sum(1 for _ in engine._iter_plan_items(cmpsc_again)),
            original_item_count,
        )


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
    # BMB/CHE/IID/MICRB/PREMED joined this list after the MATH 21
    # placement-gate fix (see each major's own TestXxxPlan class): even
    # with that fix, each has a genuinely longer real chemistry/
    # biochemistry course sequence than 8 terms. (CYBER briefly joined
    # this list too, but its overflow turned out to be caused entirely by
    # its own plan's now-unnecessary MATH 3->4->21 scaffold, not a real
    # structural minimum — removing that padding brought it back to a
    # clean 4 years, so it was removed from here again.)
    _GRAD_YEARS_OVERRIDE = {
        "ARCHBARCH": 5, "AE": 5,
        "BMB": 5, "CHE": 5, "IID": 5, "MICRB": 5, "PREMED": 5,
    }

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


def _redundant_reply_stub_args():
    # Non-empty progress/next_sem/ranked so the pointer-vs-full-list
    # branches are actually exercised, unlike the all-empty minimal stub.
    progress = {"done_items": 6, "total_items": 41, "credits_done": 20, "total_credits": 126}
    next_sem = {
        "courses": [
            {"code": "PHYS 211", "name": "", "credits": 4, "reason": "unlocks future courses"},
            {"code": "CMPSC 221", "name": "", "credits": 3, "reason": "next on the flowchart"},
        ],
        "total_credits": 7,
        "blocked": [{"code": "CMPSC 465", "missing": ["CMPSC 360"], "excludedBy": []}],
    }
    ranked = [
        {"code": "PHYS 211", "score": 260, "source": "Official Advising Flowchart", "reasons": ["ok"]},
        {"code": "MATH 220", "score": 220, "source": "Official Advising Flowchart", "reasons": ["ok"]},
    ]
    return {
        "major": "CMPSC", "catalog_year": 2026,
        "added": [], "removed": [], "unmatched": [],
        "progress": progress, "next_sem": next_sem,
        "ranked": ranked, "plan_warnings": [],
    }


class TestReplyTextNoRedundancy(unittest.TestCase):
    """The reply text must not re-render whole pages (Progress, Flowchart/
    Home's next-semester list, Recommendations' ranked list) as text --
    each gets one short line with a pointer instead. 'Still locked' stays
    a full itemized list since nothing else in the UI surfaces it."""

    def test_no_itemized_next_semester_course_list(self):
        text = _build_reply_text(**_redundant_reply_stub_args())
        self.assertNotIn("Recommended for", text)
        self.assertNotIn("unlocks future courses", text)
        self.assertIn("2 courses recommended for next semester (7 credits)", text)
        self.assertIn("see Flowchart or Home for the full list", text)

    def test_no_itemized_ranked_course_list(self):
        text = _build_reply_text(**_redundant_reply_stub_args())
        self.assertNotIn("Top ranked eligible courses", text)
        self.assertNotIn("score 260", text)
        self.assertIn("2 eligible course(s) ranked with reasons on the Recommendations page", text)

    def test_progress_is_one_line_with_pointer_not_full_breakdown(self):
        text = _build_reply_text(**_redundant_reply_stub_args())
        self.assertIn("6/41 requirements complete on the CMPSC 2026 plan", text)
        self.assertIn("see Progress for the full breakdown", text)
        # the old standalone "Progress on the ... plan: N/M requirements
        # (A/B credits)." sentence shape is gone, folded into one line
        self.assertNotIn("Progress on the CMPSC 2026 plan:", text)

    def test_still_locked_remains_a_full_itemized_list(self):
        # Unlike the sections above, nothing else in the UI shows blocked
        # courses -- this one stays fully spelled out, not a pointer.
        text = _build_reply_text(**_redundant_reply_stub_args())
        self.assertIn("Still locked:", text)
        self.assertIn("CMPSC 465 — needs: CMPSC 360", text)

    def test_phrase_prompt_instructs_llm_to_keep_pointers_not_expand_them(self):
        prompt = _build_phrase_prompt("what's next?", "some facts", "")
        self.assertIn("keep those pointers", prompt)
        self.assertIn("110 words", prompt)


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

    def test_second_majors_own_gen_ed_slot_is_deduped_not_doubled(self):
        # Real, bulletin-verified PSU policy (AAPPM M-3): a concurrent
        # majors student fulfills the generic Gen Ed pool ONCE, not once
        # per major -- so a second major's own generic "GEN ED (GQ)" slot
        # must be dropped once the primary already covers GQ, same as a
        # minor's already-tested dedup. A course the second major
        # actually REQUIRES (SEC 100 above) is never touched by this --
        # only bare generic slots with no specific course attached.
        plan = _synthetic_primary_plan()  # already has a GEN ED (GQ) slot
        second = {
            "major": "SECONDMAJ", "catalog_year": 2026, "departments": ["SECONDMAJ"],
            "semesters": [
                {"index": 1, "label": "Semester 1", "items": [
                    {"type": "course", "options": ["SEC 100"], "credits": 3},
                    {"type": "slot", "label": "GEN ED (GQ)", "credits": 3, "gen_ed": "GQ"},
                ]},
            ],
        }
        merged = engine.merge_plans(plan, second_major=second)
        gq_items = [it for _, it in engine._iter_plan_items(merged) if it.get("gen_ed") == "GQ"]
        self.assertEqual(len(gq_items), 1)  # second major's duplicate GQ slot was dropped
        all_options = [it.get("options") for _, it in engine._iter_plan_items(merged)]
        self.assertIn(["SEC 100"], all_options)  # the major's real course requirement is untouched

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

    def _merge_minor_and_build(self, minor_code, grad_years=6):
        import datetime
        cmpsc = engine.load_degree_plan("CMPSC")
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(cmpsc, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=grad_years, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])
        return merged

    def test_real_cmpsc_plus_entrepreneurship_innovation_minor(self):
        merged = self._merge_minor_and_build("ENTI")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:ENTI"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_labor_human_resources_minor(self):
        merged = self._merge_minor_and_build("LHR")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:LHR"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_leadership_development_minor(self):
        # LDEV has a genuinely deep real prereq chain (AEE 495 -> AEE 412 ->
        # AEE 100/295/311) that this session's other minors don't have --
        # needs the full 6 years to fit, same "finishes cleanly, not on an
        # unrealistically tight deadline" bar as the rest of this class.
        merged = self._merge_minor_and_build("LDEV", grad_years=6)
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:LDEV"]["total_credits"], 32.0)

    def test_real_cmpsc_plus_information_systems_management_minor(self):
        merged = self._merge_minor_and_build("ISM")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:ISM"]["total_credits"], 19.0)

    def test_real_cmpsc_plus_legal_environment_business_minor(self):
        merged = self._merge_minor_and_build("LEBUS")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:LEBUS"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_chemistry_minor(self):
        # Real hidden-prereq gap: CHEM 227 needs MATH 140, not present
        # anywhere else in the minor's own bulletin-listed courses.
        merged = self._merge_minor_and_build("CHEMMIN", grad_years=7)
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:CHEMMIN"]["total_credits"], 30.0)

    def test_real_cmpsc_plus_biology_minor(self):
        merged = self._merge_minor_and_build("BIOLMIN", grad_years=6)
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:BIOLMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_physics_minor(self):
        merged = self._merge_minor_and_build("PHYSMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:PHYSMIN"]["total_credits"], 29.0)

    def test_real_cmpsc_plus_astronomy_astrophysics_minor(self):
        # Real hidden-prereq gap: ASTRO 291 needs PHYS 212, which the
        # bulletin's own prescribed-course list never mentions.
        merged = self._merge_minor_and_build("ASTROMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:ASTROMIN"]["total_credits"], 28.0)

    def test_real_cmpsc_plus_geosciences_minor(self):
        merged = self._merge_minor_and_build("GEOSCMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:GEOSCMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_history_minor(self):
        merged = self._merge_minor_and_build("HISTMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:HISTMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_philosophy_minor(self):
        merged = self._merge_minor_and_build("PHILMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:PHILMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_sociology_minor(self):
        merged = self._merge_minor_and_build("SOCMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:SOCMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_political_science_minor(self):
        merged = self._merge_minor_and_build("PLSCMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:PLSCMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_art_history_minor(self):
        merged = self._merge_minor_and_build("ARTHMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:ARTHMIN"]["total_credits"], 21.0)

    def test_real_cmpsc_plus_english_minor(self):
        merged = self._merge_minor_and_build("ENGLMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:ENGLMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_spanish_minor(self):
        merged = self._merge_minor_and_build("SPANMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:SPANMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_french_francophone_studies_minor(self):
        merged = self._merge_minor_and_build("FRMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:FRMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_german_minor(self):
        merged = self._merge_minor_and_build("GERMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:GERMIN"]["total_credits"], 19.0)

    def test_real_cmpsc_plus_journalism_minor(self):
        merged = self._merge_minor_and_build("JOURNMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:JOURNMIN"]["total_credits"], 19.0)

    def test_real_cmpsc_plus_theatre_minor(self):
        merged = self._merge_minor_and_build("THEAMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:THEAMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_anthropology_minor(self):
        merged = self._merge_minor_and_build("ANTHMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:ANTHMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_kinesiology_minor(self):
        merged = self._merge_minor_and_build("KINESMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:KINESMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_music_technology_minor(self):
        merged = self._merge_minor_and_build("MUSTECHMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:MUSTECHMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_nutritional_sciences_minor(self):
        # Real hidden-prereq gap: NUTR 445's actual enforced prereq is BIOL
        # 161 and 162 and 163 and (164 or BMB 211) and NUTR 251 -- a full
        # intro-biology sequence, not just NUTR 251 as the bulletin's own
        # prescribed-course table implies -- so the minor's own computed
        # total (26cr) runs over the bulletin's stated 18cr.
        merged = self._merge_minor_and_build("NUTRMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:NUTRMIN"]["total_credits"], 26.0)

    def test_real_theatre_major_plus_theatre_minor(self):
        # Verified against its own matching major, not just CMPSC: the
        # minor's courses are drawn from the same THEA department the THEA
        # major itself is built on.
        import datetime
        thea = engine.load_degree_plan("THEA")
        minor = engine.load_minor_plan("THEAMIN", 2026)
        merged = engine.merge_plans(thea, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_anthropology_major_plus_anthropology_minor(self):
        import datetime
        anth = engine.load_degree_plan("ANTH")
        minor = engine.load_minor_plan("ANTHMIN", 2026)
        merged = engine.merge_plans(anth, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_kinesiology_major_plus_kinesiology_minor(self):
        import datetime
        kines = engine.load_degree_plan("KINES")
        minor = engine.load_minor_plan("KINESMIN", 2026)
        merged = engine.merge_plans(kines, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_music_technology_major_plus_music_technology_minor(self):
        # Major code (MUSTECH) is distinct from the minor code (MUSTECHMIN)
        # even though both share the same real-world name -- the B.M. major
        # and this minor are two separate PSU bulletin programs.
        import datetime
        mustech = engine.load_degree_plan("MUSTECH")
        minor = engine.load_minor_plan("MUSTECHMIN", 2026)
        merged = engine.merge_plans(mustech, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_nutrition_major_plus_nutritional_sciences_minor(self):
        # The NUTR major already schedules BIOL 161/162/163/164 in its own
        # first year -- the same sequence the minor adds explicitly to
        # unlock NUTR 445's real prereq -- so merge_plans should widen the
        # existing major items rather than duplicate them.
        import datetime
        nutr = engine.load_degree_plan("NUTR")
        minor = engine.load_minor_plan("NUTRMIN", 2026)
        merged = engine.merge_plans(nutr, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_english_major_plus_english_minor(self):
        # Verified against its own matching major, not just CMPSC: the
        # minor's courses (ENGL 200/201/205/206/400/401) are drawn from the
        # same department the ENGL major itself is built on.
        import datetime
        engl = engine.load_degree_plan("ENGL")
        minor = engine.load_minor_plan("ENGLMIN", 2026)
        merged = engine.merge_plans(engl, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_journalism_major_plus_journalism_minor(self):
        # The bulletin itself notes Journalism majors must complete an
        # approved minor OUTSIDE the Bellisario College -- this test doesn't
        # encode that policy, it only confirms the merge/schedule mechanism
        # itself doesn't deadlock when both share the COMM department.
        import datetime
        journ = engine.load_degree_plan("JOURN")
        minor = engine.load_minor_plan("JOURNMIN", 2026)
        merged = engine.merge_plans(journ, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_cmpsc_plus_japanese_language_minor(self):
        merged = self._merge_minor_and_build("JAPNSMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:JAPNSMIN"]["total_credits"], 19.0)

    def test_real_cmpsc_plus_korean_language_minor(self):
        merged = self._merge_minor_and_build("KORMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:KORMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_chinese_language_minor(self):
        merged = self._merge_minor_and_build("CHNSMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:CHNSMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_geography_minor(self):
        merged = self._merge_minor_and_build("GEOGMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:GEOGMIN"]["total_credits"], 18.0)

    def test_real_cmpsc_plus_security_risk_analysis_minor(self):
        merged = self._merge_minor_and_build("SRAMIN")
        progress = engine.plan_progress(merged, set())
        self.assertEqual(progress["by_category"]["minor:SRAMIN"]["total_credits"], 21.0)

    def test_real_japanese_major_plus_japanese_language_minor(self):
        # Verified against its own matching major, not just CMPSC: the
        # minor's courses are drawn from the same JAPNS department the
        # JAPNSBA major itself is built on.
        import datetime
        japns = engine.load_degree_plan("JAPNSBA")
        minor = engine.load_minor_plan("JAPNSMIN", 2026)
        merged = engine.merge_plans(japns, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_korean_major_plus_korean_language_minor(self):
        import datetime
        kor = engine.load_degree_plan("KORBA")
        minor = engine.load_minor_plan("KORMIN", 2026)
        merged = engine.merge_plans(kor, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_chinese_major_plus_chinese_language_minor(self):
        import datetime
        chns = engine.load_degree_plan("CHNSBA")
        minor = engine.load_minor_plan("CHNSMIN", 2026)
        merged = engine.merge_plans(chns, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_geography_major_plus_geography_minor(self):
        import datetime
        geog = engine.load_degree_plan("GEOG")
        minor = engine.load_minor_plan("GEOGMIN", 2026)
        merged = engine.merge_plans(geog, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertNotIn("Plan did not finish within 24 simulated terms.", fp["warnings"])
        self.assertTrue(fp["goal"]["met"])

    def test_real_sra_major_plus_security_risk_analysis_minor(self):
        import datetime
        sra = engine.load_degree_plan("SRA")
        minor = engine.load_minor_plan("SRAMIN", 2026)
        merged = engine.merge_plans(sra, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
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


class TestLowCostMinors(unittest.TestCase):
    """suggest_low_cost_minors: which minors a student could add without
    piling on many extra courses, ranked by real overlap with their major
    (via the same merge_plans widening plan_progress's per-minor bucket
    already relies on), not by size or popularity."""

    def setUp(self):
        self.plan = engine.load_degree_plan("CMPSC", 2026)

    def test_minor_sharing_completed_courses_ranks_above_one_with_no_overlap(self):
        # A student who's already done STAT 318/319 (real CMPSC requirements
        # STATMIN also requires) should see STATMIN rank as cheap — those
        # credits are already earned, not additional work.
        completed = {"CMPSC 131", "CMPSC 132", "MATH 140", "MATH 141", "STAT 318", "STAT 319"}
        results = engine.suggest_low_cost_minors(self.plan, completed, 2026)
        by_code = {r["minor"]: r for r in results}
        self.assertIn("STATMIN", by_code)
        # Every returned minor's "new courses needed" excludes what's shared
        # with the major or already completed — never double-counts either.
        for r in results:
            self.assertGreaterEqual(r["newCoursesNeeded"], 0)
            self.assertLessEqual(r["sharedWithMajor"], r["totalRequirements"])

    def test_results_sorted_cheapest_first(self):
        results = engine.suggest_low_cost_minors(self.plan, set(), 2026, max_results=10)
        pairs = [(r["newCoursesNeeded"], r["extraCreditsNeeded"]) for r in results]
        self.assertEqual(pairs, sorted(pairs))

    def test_excluded_minors_never_appear(self):
        results = engine.suggest_low_cost_minors(
            self.plan, set(), 2026, exclude_minors={"STATMIN", "MATHMIN"}, max_results=20,
        )
        codes = {r["minor"] for r in results}
        self.assertNotIn("STATMIN", codes)
        self.assertNotIn("MATHMIN", codes)

    def test_max_results_respected(self):
        results = engine.suggest_low_cost_minors(self.plan, set(), 2026, max_results=3)
        self.assertLessEqual(len(results), 3)

    def test_api_plan_includes_low_cost_minors(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": [], "start_year": 2026,
        })
        self.assertEqual(r.status_code, 200)
        low_cost = r.get_json()["coursePlan"]["lowCostMinors"]
        self.assertTrue(low_cost)
        self.assertIn("newCoursesNeeded", low_cost[0])
        self.assertIn("title", low_cost[0])

    def test_already_selected_minor_excluded_from_its_own_suggestions(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": [], "start_year": 2026,
            "minors": ["STATMIN"],
        })
        codes = {m["minor"] for m in r.get_json()["coursePlan"]["lowCostMinors"]}
        self.assertNotIn("STATMIN", codes)


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

    def test_sgsmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "SGSMIN", 18.0)

    def test_sgsmin_against_wmnstba(self):
        self._merge_and_build("WMNSTBA", "SGSMIN")

    def test_lingmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "LINGMIN", 18.0)

    def test_lingmin_against_ling_major(self):
        self._merge_and_build("LING", "LINGMIN")

    def test_afammin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "AFAMMIN", 18.0)

    def test_afammin_against_afam_major(self):
        self._merge_and_build("AFAM", "AFAMMIN")

    def test_mediamin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "MEDIAMIN", 18.0)

    def test_mediamin_against_journ_major(self):
        self._merge_and_build("JOURN", "MEDIAMIN")

    def test_jstmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "JSTMIN", 18.0)

    def test_jstmin_against_jst_major(self):
        self._merge_and_build("JST", "JSTMIN")


class TestSecondRealMinorBatch(unittest.TestCase):
    """5 more real minors (51 total): Legal Studies (Liberal Arts, code
    LEGSTMIN -- paired with CRIM), Health Policy and Administration (Health
    and Human Development, code HPAMIN -- paired with the HPA major itself;
    renamed off the bulletin's plan code to avoid colliding with the
    already-built HPA major's own code), Classics and Ancient Mediterranean
    Studies (Liberal Arts, code CAMSMIN -- paired with the CAMS major,
    similarly renamed to avoid a code collision), Graphic Design (Arts and
    Architecture, code GDMIN -- paired with the GD major, same collision
    avoidance), and Supply Chain and Information Sciences and Technology
    (Smeal, code SCISTMIN -- paired with BAIS rather than the SCM major,
    since SCM's own required-for-the-minor courses are real-bulletin
    restricted against Smeal business students). HPAMIN, GDMIN, and
    SCISTMIN each carry a real hidden prerequisite chain (documented in
    their own notes fields) needed to make them self-sufficient against a
    baseline major (CMPSC) that doesn't already supply those prereqs --
    same pattern as CYBERCF's hidden chain in an earlier batch."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_legstmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "LEGSTMIN", 18.0)

    def test_legstmin_against_crim_major(self):
        self._merge_and_build("CRIM", "LEGSTMIN")

    def test_hpamin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "HPAMIN", 21.0)

    def test_hpamin_against_hpa_major(self):
        self._merge_and_build("HPA", "HPAMIN")

    def test_camsmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "CAMSMIN", 18.0)

    def test_camsmin_against_cams_major(self):
        self._merge_and_build("CAMS", "CAMSMIN")

    def test_gdmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "GDMIN", 27.0)

    def test_gdmin_against_gd_major(self):
        self._merge_and_build("GD", "GDMIN")

    def test_scistmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "SCISTMIN", 32.0)

    def test_scistmin_against_bais_major(self):
        self._merge_and_build("BAIS", "SCISTMIN")


class TestThirdRealMinorBatch(unittest.TestCase):
    """5 more real minors (56 total), a batch deliberately picked to pair
    with majors that had no minor yet: Human Development and Family Studies
    (Health and Human Development, code HDFSMIN -- paired with the HDFS
    major itself, renamed off the bulletin's plan code to avoid colliding
    with the already-built HDFS major's own code), Wildlife and Fisheries
    Science (Agricultural Sciences, code WFSMIN -- paired with the WFS
    major, same collision avoidance), Nuclear Engineering (Engineering,
    code NUCEMIN -- paired with Mechanical Engineering rather than NUCE,
    since the real bulletin explicitly restricts this minor to students
    admitted to a major OTHER than nuclear engineering), World Literature
    (College of the Liberal Arts, code WLITMIN -- the real minor's actual
    bulletin title, paired with the Comparative Literature major, code
    CMLIT), and Politics and Public Policy (Liberal Arts, code POLPOLMIN --
    paired with Political Science B.A., code PLSCBA, distinct from the
    already-built plain PLSCMIN). WFSMIN and NUCEMIN each carry a real
    hidden prerequisite chain (documented in their own notes fields) needed
    to make them self-sufficient against a baseline major (CMPSC) that
    doesn't already supply those prereqs -- same pattern as CYBERCF/HPAMIN/
    GDMIN/SCISTMIN's hidden chains in earlier batches."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_hdfsmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "HDFSMIN", 18.0)

    def test_hdfsmin_against_hdfs_major(self):
        self._merge_and_build("HDFS", "HDFSMIN")

    def test_wfsmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "WFSMIN", 26.0)

    def test_wfsmin_against_wfs_major(self):
        self._merge_and_build("WFS", "WFSMIN")

    def test_nucemin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "NUCEMIN", 19.0)

    def test_nucemin_against_me_major(self):
        self._merge_and_build("ME", "NUCEMIN")

    def test_wlitmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "WLITMIN", 18.0)

    def test_wlitmin_against_cmlit_major(self):
        self._merge_and_build("CMLIT", "WLITMIN")

    def test_polpolmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "POLPOLMIN", 22.0)

    def test_polpolmin_against_plscba_major(self):
        self._merge_and_build("PLSCBA", "POLPOLMIN")


class TestFourthRealMinorBatch(unittest.TestCase):
    """5 more real minors (61 total), a College of Agricultural Sciences /
    College of Earth and Mineral Sciences batch deliberately picked so every
    minor pairs with an already-built major of the exact same real-world
    program (ERM, ANSC, EBFIN, AGBM, MATSCI all already exist as majors).
    Environmental Resource Management (ERMMIN, Agricultural Sciences) --
    18cr bulletin nominal, computed 29cr: prescribed ABSM 327 + SOILS 101
    (6cr); real hidden-prereq chain, ABSM 327 enforces a concurrent PHYS
    211-or-250 requirement, and PHYS 211 itself enforces a concurrent MATH
    140 -- both added explicitly (8cr); the bulletin's 'any ERM offerings to
    reach 18cr, min 6cr at 400-level' pool filled with ERM 210/402/411/448
    (12cr, 9cr of it 400-level), ERM 402/411 needing ECON 102 (added, 3cr).
    Animal Science (ANSCMIN, Agricultural Sciences) -- 20-21cr bulletin
    range, computed 20cr exactly at the floor, fully clean (every course
    resolves via ANSC 201/301, both prescribed). Energy Business and Finance
    (EBFMIN, Earth and Mineral Sciences) -- 27-29cr bulletin range, computed
    32cr after the real MATH 21 hidden-prereq gap under STAT 200 (the same
    pattern hit repeatedly this session); surfaced a real instance of the
    documented flattened-OR-group scraper quirk in eme_catalog.json (EME
    444's real 'ECON 104 or EGEE 102 or EGEE 120' prerequisite was scraped
    as three separate AND-required groups) -- worked around at the data
    level by picking EBF 483 instead, since catalogs/*.json was out of scope
    for this batch. Agribusiness Management (AGBMMIN, Agricultural Sciences)
    -- 21cr bulletin exact match, fully clean, zero hidden-prereq additions
    (ECON 102 alone unlocks the entire 400-level elective pool used here).
    Materials Science and Engineering (MATSCIMIN, Earth and Mineral
    Sciences) -- 18cr bulletin exact match, the cleanest minor this batch;
    also hit the flattened-OR-group quirk a second time (MATSE 449's real
    'MATSE 201 or MATSE 202' prerequisite), worked around by picking MATSE
    412 (genuinely prereq-free) instead. All 5 verified both against CMPSC
    (this catalog's standard baseline, grad_years=8) and their own real
    matching major (ERM, ANSC, EBFIN, AGBM, MATSCI) -- 0 warnings and
    `goal.met = True` in all 10 pairings. No candidates dropped this batch,
    though Energy Engineering, Environmental Systems Engineering, and Mining
    Engineering minors were each researched and rejected before building:
    all three sit behind a genuinely deep, multi-branch prerequisite cascade
    (MATH 140 -> MATH 141 -> MATH 250/251, MATH 140 -> PHYS 211/212 -> EME
    301/303, CHEM 110 -> CHEM 112, EMCH 210, etc., just to reach any single
    elective in their own required pools) that would add 8+ extra courses
    against the CMPSC baseline -- the same anti-pattern class already
    documented for AIENG's 4-level A-I chain, judged not worth forcing into
    a rushed batch entry. A plain 'Food Science, Minor' and 'Global Business
    Strategies, Minor' were also searched for and confirmed NOT to exist as
    real current University Park undergraduate minors (the college's actual
    minor-program listings show 'Food Systems, Minor' and no Global Business
    Strategies minor at all), so neither was pursued."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_ermmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ERMMIN", 29.0)

    def test_ermmin_against_erm_major(self):
        self._merge_and_build("ERM", "ERMMIN")

    def test_anscmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ANSCMIN", 20.0)

    def test_anscmin_against_ansc_major(self):
        self._merge_and_build("ANSC", "ANSCMIN")

    def test_ebfmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "EBFMIN", 32.0)

    def test_ebfmin_against_ebfin_major(self):
        self._merge_and_build("EBFIN", "EBFMIN")

    def test_agbmmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "AGBMMIN", 21.0)

    def test_agbmmin_against_agbm_major(self):
        self._merge_and_build("AGBM", "AGBMMIN")

    def test_matscimin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "MATSCIMIN", 18.0)

    def test_matscimin_against_matsci_major(self):
        self._merge_and_build("MATSCI", "MATSCIMIN")


class TestFifthRealMinorBatch(unittest.TestCase):
    """5 more real minors (66 total), each deliberately picked to pair with
    an already-built major of the same or closely-related real-world
    program (PLANET, PPHOTO, LARCH, MUSIC/MUSICBM, PLSCI all already exist
    as majors). Planetary Science and Astronomy (PSAMIN, Eberly College of
    Science) -- 19cr bulletin exact match, name-for-name pairing with the
    PLANET major; distinct from the already-built Astronomy and Astrophysics
    Minor (ASTROMIN), a different real program in the same department.
    Prescribed ASTRO 401 + ASTRO 402W (7cr); Additional Courses' 'select
    one' 3cr slot filled with ASTRO 1 (also clears ASTRO 401's own prereq
    group and is the shared prereq for the 'select three' 9cr slot's three
    picks, ASTRO 120/130/140) -- one course clearing every downstream gate
    at once. ASTRO 401's own MATH 140 prereq needed no separate minor
    requirement since both CMPSC and PLANET already require it. Photography
    (PHOTOMIN, College of Arts and Architecture) -- 19cr bulletin, computed
    20cr. Prescribed PHOTO 303 + PHOTO 404; the live course-description
    pages were checked directly since the flattened catalog groups first
    looked like two-course AND requirements -- both are real ORs (PHOTO 303
    needs 'PHOTO 200 or PHOTO 202', PHOTO 404 needs 'PHOTO 300 or PHOTO
    303'), so PHOTO 202 alone (added to the 'select 9cr of PHOTO' pool,
    doing double duty) clears both gates without needing PHOTO 100/200/300
    at all. The bulletin's 'select 3cr of 400-level PHOTO' slot has no
    prereq-clean 3cr option without adding PHOTO 200, so PHOTO 405 (4cr,
    needs only PHOTO 202) was used instead -- a 1cr rounding overage, the
    same PSYCH-style pattern seen repeatedly this session. Landscape
    Architecture (LARCHMIN, College of Arts and Architecture) -- 18cr
    bulletin exact match, name-for-name pairing with the LARCH major, fully
    clean: AA 121 + LARCH 60 + LARCH 125 prescribed (7cr) plus LARCH 424 +
    LARCH 450 (6cr at the 400-level) + LARCH 65 + LARCH 155 (5cr) for the
    11cr Additional Courses requirement, all seven prereq-free, deliberately
    avoiding the bulletin list's other options that chain through multi-level
    design-studio sequences. Music Performance (MUSPERFMIN, College of Arts
    and Architecture) -- 21cr bulletin exact match, pairs with the Music
    B.A./B.M. majors, distinct from the already-built Music Technology minor
    (MUSTECHMIN). The bulletin's audition admission requirement is a
    non-course entrance gate (like PPHOTO's own portfolio review) and isn't
    modeled as a requirement item. 'Select 8cr applied music' and 'select
    4cr ensembles' name no fixed course codes at all (Penn State's
    applied-lesson system is numbered per instrument/level) -- modeled as
    two generic slots, the same convention the MUSIC major's own flowchart
    already uses for its identical line items. Filled the remaining 9cr with
    three prereq-free MUSIC courses (MUSIC 4 elective, MUSIC 423 + MUSIC 469
    at the 400-level). Horticulture (HORTMIN, College of Agricultural
    Sciences) -- 18cr bulletin exact match, the cleanest minor this batch;
    substituted for a plain 'Plant Science, Minor', which doesn't exist at
    University Park (the college's own minor listing has only
    subject-specific minors), picking Horticulture as the closest real
    pairing with the Plant Sciences major (PLSCI). HORT 101 + HORT 202 +
    PLANT 201 prescribed (9cr, PLANT 201 real-cross-listed with AGECO 201)
    plus HORT 131 (3cr) + HORT 407 + HORT 431 (6cr), all six prereq-free
    within the minor's own prescribed set. All 5 verified both against
    CMPSC (this catalog's standard baseline, grad_years=8) and their own
    real matching major (PLANET, PPHOTO, LARCH, MUSIC, PLSCI) -- 0 warnings
    and `goal.met = True` in all 10 pairings, every CMPSC-paired minor
    credit total confirmed exactly via `plan_progress` (19/20/18/21/18cr).
    **Three candidates researched and dropped before building:** Food
    Systems, Minor (College of Agricultural Sciences) is real and confirmed
    at University Park, but its Prescribed Courses mandatorily include
    FDSYS 490 and FDSYS 495 -- the FDSYS prefix has no scraped catalog file
    anywhere in catalogs/*.json, and since that file set is out of scope for
    this batch, the minor could not be modeled without inventing course
    data. Meteorology, Minor (Earth and Mineral Sciences) was revisited per
    instruction to try a non-CMPSC verification major, but the real blocker
    is structural, not major-specific: the minor's own MATH 232 requirement
    carries a real PSU anti-requisite against MATH 230
    (math_catalog.json's own `excludes` data), and CMPSC -- the fixed
    standard baseline every minor in this project is verified against --
    already requires MATH 230 on its own flowchart, so the conflict
    reproduces against CMPSC regardless of which second major is chosen;
    still not built. Turfgrass Science, Minor was rechecked against the
    College of Agricultural Sciences' current minor listing and reconfirmed
    absent -- only a Turfgrass Management *graduate* minor and a Turfgrass
    Science and Management *certificate* exist at University Park, no
    undergraduate minor, matching the prior batch's finding. 10 new tests
    added to a new `TestFifthRealMinorBatch` class (same `_merge_and_build`
    helper pattern as the prior batch's `TestFourthRealMinorBatch`)."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_psamin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "PSAMIN", 19.0)

    def test_psamin_against_planet_major(self):
        self._merge_and_build("PLANET", "PSAMIN")

    def test_photomin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "PHOTOMIN", 20.0)

    def test_photomin_against_pphoto_major(self):
        self._merge_and_build("PPHOTO", "PHOTOMIN")

    def test_larchmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "LARCHMIN", 18.0)

    def test_larchmin_against_larch_major(self):
        self._merge_and_build("LARCH", "LARCHMIN")

    def test_musperfmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "MUSPERFMIN", 21.0)

    def test_musperfmin_against_music_major(self):
        self._merge_and_build("MUSIC", "MUSPERFMIN")

    def test_hortmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "HORTMIN", 18.0)

    def test_hortmin_against_plsci_major(self):
        self._merge_and_build("PLSCI", "HORTMIN")


class TestSixthRealMinorBatch(unittest.TestCase):
    """5 more real minors (71 total), each picked to pair with an
    already-built major of the same or closely-related real-world program
    (MICRB, RPTM, FLMPR, CASBA/CASBS, BE all already exist as majors).
    Microbiology (MICRBMIN, Eberly College of Science) -- 24cr bulletin
    exact match on the nominal course list, computed 30cr: CHEM 110
    enforces a real MATH 22 -> MATH 21 hidden-prereq chain that neither
    CMPSC nor the MICRB major's own flowchart already covers (both build
    straight to MATH 140), added explicitly (6cr) -- the same MATH-21-chain
    pattern seen in EBFMIN and the BE major's own build notes. Every other
    course (MICRB 201/202/251/410, MICRB 421W, MICRB 412, MICRB 411)
    resolves entirely within the minor's own prescribed set, fully clean.
    Recreation, Park, and Tourism Management (RPTMMIN, College of Health
    and Human Development) -- 18cr bulletin exact match, name-for-name
    pairing with the RPTM major; minor code RPTMMIN avoids colliding with
    the major's own code. RPTM 101 + RPTM 120 prescribed (6cr) plus RPTM
    201 + RPTM 210 (6cr, non-400-level) + RPTM 410 + RPTM 433W (6cr,
    400-level) for the 12cr Supporting Courses requirement, all four
    prereq-free, deliberately avoiding the pool's many other RPTM courses
    that chain through RPTM 120/210/236/250/254/325. Film Studies
    (FLMSMIN, Bellisario College of Communications / College of the
    Liberal Arts) -- 18cr bulletin exact match, pairs with the already-built
    Film Production major (FLMPR) as the closest real match, distinct from
    the College's separate Media Studies minor (MEDIAMIN). Prescribed COMM
    150N + COMM 250 (6cr); the bulletin's own 12cr Supporting Courses pool
    points only to a non-bulletin department webpage for its specific list,
    so real film-focused COMM courses were used instead -- COMM 151N + COMM
    242 (6cr, non-400) + COMM 451 + COMM 452 (6cr, 400-level, both needing
    only the already-prescribed COMM 250). Communication and Social Justice
    (CSJMIN, Bellisario College of Communications) -- 18cr bulletin exact
    match, fully clean, pairs with the Communication Arts and Sciences
    majors (CASBA/CASBS). COMM 232 + COMM 432 prescribed (6cr, COMM 432 is
    the minor's own capstone needing COMM 232 AND one of COMM 270/282);
    COMM 270 (3cr) doubles as the Supporting Courses pick and clears COMM
    432's second prereq group; SOC 5 + AFAM 100N + PLSC 451 (9cr, one at
    400-level) picked directly from the bulletin's own published
    cross-department elective list for carrying zero prerequisites of their
    own, unlike most of that list's other 400-level options. Biological
    Engineering (BEMIN, College of Engineering) -- 18-20cr bulletin range,
    computed 28cr, pairs name-for-name with the BE major. The bulletin
    publishes no mandatory Prescribed Courses at all -- every requirement
    comes from four selection pools. HORT 101 (3cr, prereq-free) for the
    Related Science Electives pool; BE 301 + BE 302 (7cr, BE 302 satisfied
    by BE 301 in its own OR-prereq-group) for the 300-Level BE pool, chosen
    over the pool's other options specifically because both resolve through
    a single MATH 251 addition (needs only MATH 141, already required by
    both verification majors) rather than the EMCH structural-mechanics or
    CHEM chemistry chains the other pool members require; BE 465 (needs
    only the already-selected BE 302) + BE 404 (needs the already-selected
    BE 301 AND one of EMCH 210/213) for the 400-Level BE pool, EMCH 210
    (needs only MATH 140, already required by both verification majors)
    added as the second and last hidden-prereq course; the bulletin's own
    3cr Supporting Courses line names no fixed course at all ('in
    consultation with the minor adviser') and was modeled as a generic
    slot, the same convention used for MUSPERFMIN's Applied Music/Ensemble
    lines. All 5 verified both against CMPSC (this catalog's standard
    baseline, grad_years=8) and their own real matching major (MICRB, RPTM,
    FLMPR, CASBA, BE) -- 0 warnings and `goal.met = True` in all 10
    pairings, every CMPSC-paired minor's credit total confirmed exactly via
    `plan_progress` (30/18/18/18/28cr). Candidates researched and NOT
    built this batch: Global Health, Minor (College of Health and Human
    Development) is real (27-28cr) but its Prescribed Courses mandatorily
    include BBH 390A/390B, a 9cr supervised fieldwork placement gated
    behind a written application to the program Director (GPA statement,
    faculty-adviser signature, proposed fieldwork plan) -- a non-course
    admission gate in the same family as PPHOTO's portfolio review and
    MUSPERFMIN's audition, except here the fieldwork courses themselves
    (not just entry to the minor) are non-standard credit-bearing
    placements rather than ordinary scheduled courses, so it was dropped
    rather than modeled. Biochemistry and Molecular Biology, Minor (Eberly
    College of Science, would pair name-for-name with the already-built BMB
    major) was drafted and then dropped: its own Prescribed Courses chain
    six real levels deep from MATH 21 (CHEM 110 -> CHEM 112 -> CHEM 210 ->
    CHEM 212 -> BMB 401 -> BMB 402, the last needing BMB 401 which itself
    needs both CHEM 210 and CHEM 212), a genuinely deep cascade for a
    minor's own required course list rather than an elective pool, so
    Microbiology (a real, shallower, name-for-name Eberly Science sibling)
    was built in its place. 10 new tests added to a new
    `TestSixthRealMinorBatch` class (same `_merge_and_build` helper pattern
    as the prior batch's `TestFifthRealMinorBatch`)."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_micrbmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "MICRBMIN", 30.0)

    def test_micrbmin_against_micrb_major(self):
        self._merge_and_build("MICRB", "MICRBMIN")

    def test_rptmmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "RPTMMIN", 18.0)

    def test_rptmmin_against_rptm_major(self):
        self._merge_and_build("RPTM", "RPTMMIN")

    def test_flmsmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "FLMSMIN", 18.0)

    def test_flmsmin_against_flmpr_major(self):
        self._merge_and_build("FLMPR", "FLMSMIN")

    def test_csjmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "CSJMIN", 18.0)

    def test_csjmin_against_casba_major(self):
        self._merge_and_build("CASBA", "CSJMIN")

    def test_bemin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "BEMIN", 28.0)

    def test_bemin_against_be_major(self):
        self._merge_and_build("BE", "BEMIN")


class TestSeventhRealMinorBatch(unittest.TestCase):
    """5 more real minors (76 total), each picked to pair with an
    already-built major that had no minor yet (RHS, SPLED, FORES, EARTHSCI,
    ABSM all already exist as majors). Surveyed the real college-level minor
    listings directly (Smeal Business, College of Education, College of
    Health and Human Development, College of Agricultural Sciences, College
    of Earth and Mineral Sciences, College of the Liberal Arts) before
    picking, which ruled out several suggested candidates as not real PSU
    programs: Actuarial Science and Real Estate minors do not exist at Smeal
    (both are B.S. majors only, per Smeal's own real minor listing of five
    programs -- Information Systems Management, International Business,
    Legal Environment of Business, and two Supply Chain variants, all
    already built); Middle Level Education, Cognitive Science, Community/
    Environment/Development, Marine Science, Watershed Stewardship (as a
    literal title), and Sport Management (as a literal title) do not exist
    as real minors either -- Middle Level Education is a major only, the
    College of Agricultural Sciences' own minor listing has no Community/
    Environment/Development or Wood Products entry, Earth and Mineral
    Sciences' own listing has no Marine Science entry, and the real titles
    turned out to be "Watersheds and Water Resources, Minor" and "Sport
    Studies, Minor" respectively. Rehabilitation and Human Services
    (RHSMIN, College of Education) -- 18cr exact bulletin match, fully
    clean, name-for-name pairing with the already-built RHS major; minor
    code RHSMIN avoids colliding with the major's own code. Prescribed RHS
    100 + RHS 300 + RHS 403 (9cr) + RHS 401 for the 'one additional
    400-level RHS course' slot (3cr); Supporting Courses (6cr) filled
    entirely within RHS (RHS 402 + RHS 404, both real listed options on the
    bulletin's own verbatim cross-department list) -- every rhs_catalog.json
    course is prereq-free. Special Education (SPLEDMIN, College of
    Education) -- 24cr exact bulletin match, fully clean, name-for-name
    pairing with the already-built SPLED major; minor code SPLEDMIN avoids
    colliding with the major's own code. Prescribed EDPSY 14 + SPLED 400 +
    SPLED 419 + SPLED 461 (12cr) + HDFS 229 + SPLED 403A (6cr, two 'select
    one' slots) + CSD 146 + CSD 218 (6cr, a 'select 6cr' pool) -- every
    course prereq-free, deliberately avoiding the pool's only option with a
    real prereq (CSD 300, which needs CSD 146). Forest Ecosystems (FORMIN,
    College of Agricultural Sciences) -- 18-20cr bulletin range, computed
    18cr at the floor, fully clean, name-for-name pairing with the
    already-built FORES major; minor code FORMIN matches the department's
    own FOR course prefix. Prescribed FOR 203 + FOR 308 (6cr); Additional
    Courses (12cr min, 6cr at 400-level) filled with FOR 255 + FOR 303 (6cr,
    non-400) + FOR 401 + FOR 403 (6cr, 400-level) -- every course
    prereq-free, deliberately avoiding the pool's other FOR courses that
    chain through FOR 203/266/308/421/440 prerequisites. Watersheds and
    Water Resources (WWRMIN, College of Earth and Mineral Sciences) -- 18cr
    exact bulletin match, fully clean, pairs with the already-built Earth
    Sciences major (EARTHSCI), which cites this exact minor by name as one
    of five interdisciplinary-minor options in its own build notes. The
    bulletin publishes no Prescribed Courses at all -- the entire 18cr comes
    from one committee-approved elective pool spanning ASM/BE/CE/CHEM/
    ENVSE/ERM/FOR/GEOG/GEOSC/PLANT/SOILS/WFS, filled with ASM 327 + PLANT
    217 + GEOSC 340 (9cr, non-400) + GEOSC 413W + GEOSC 419 + GEOSC 452
    (9cr, 400-level) -- all six genuinely prereq- AND concurrent-free,
    deliberately avoiding the pool's other options that chain through real
    prerequisites or hidden same-term concurrent requirements not otherwise
    in either verification plan (WFS 410/422 need a real BIOL 110/WFS
    209N/WILDL 101 concurrent group; BE 307 needs a CE 360/ME 320 concurrent
    group; CE/CHEM/ENVSE/ERM/SOILS options all chain through MATH 141, CHEM
    110/212, or BIOL 110). Renewable Bioproducts (REBPMIN, College of
    Agricultural Sciences) -- 18cr bulletin exact match on the nominal
    course list, computed 27cr against CMPSC after a real hidden-prereq
    chain; pairs name-for-name with the already-built Agricultural and
    Biorenewable Systems Management major (ABSM), which had no minor yet.
    Prescribed ABSM 300 + ABSM 350 (needs MATH 110/140, already required by
    both CMPSC and ABSM) + ABSM 411 (needs ABSM 350 [already prescribed] AND
    CHEM 110) = 9cr; Additional Courses filled with ABSM 423 (needs only
    ABSM 300, already prescribed) + MATSE 441 + MATSE 445 (both prereq-free)
    = 9cr. Real hidden-prereq chain: ABSM 411's own CHEM 110 requirement
    isn't satisfied by CMPSC, and CHEM 110 itself enforces MATH 22, which
    itself enforces MATH 21 -- the same MATH-21-chain pattern documented
    repeatedly across this project's earlier batches (MICRBMIN, EBFMIN) --
    added CHEM 110 + MATH 22 + MATH 21 explicitly (9cr) for the CMPSC
    pairing; the ABSM major's own flowchart already includes this entire
    chain on its own semester plan, so the addition collapses to a no-op
    against ABSM itself. All 5 verified both against CMPSC (this catalog's
    standard baseline, grad_years=8) and their own real matching major (RHS,
    SPLED, FORES, EARTHSCI, ABSM) -- 0 warnings and `goal.met = True` in all
    10 pairings, every CMPSC-paired minor's credit total confirmed exactly
    via `plan_progress` (18/24/18/18/27cr). 10 new tests added to this new
    `TestSeventhRealMinorBatch` class (same `_merge_and_build` helper
    pattern as the prior batch's `TestSixthRealMinorBatch`)."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_rhsmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "RHSMIN", 18.0)

    def test_rhsmin_against_rhs_major(self):
        self._merge_and_build("RHS", "RHSMIN")

    def test_spledmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "SPLEDMIN", 24.0)

    def test_spledmin_against_spled_major(self):
        self._merge_and_build("SPLED", "SPLEDMIN")

    def test_formin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "FORMIN", 18.0)

    def test_formin_against_fores_major(self):
        self._merge_and_build("FORES", "FORMIN")

    def test_wwrmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "WWRMIN", 18.0)

    def test_wwrmin_against_earthsci_major(self):
        self._merge_and_build("EARTHSCI", "WWRMIN")

    def test_rebpmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "REBPMIN", 27.0)

    def test_rebpmin_against_absm_major(self):
        self._merge_and_build("ABSM", "REBPMIN")


class TestEighthRealMinorBatch(unittest.TestCase):
    """5 more real minors (81 total), all from the College of Earth and
    Mineral Sciences' own real minor listing, each picked to pair
    name-for-name with an already-built major of the same program (ENGY,
    ENVSYS, MINE, PNG, EARTHSCI all already exist as majors) -- and every
    one needed zero new department scraping, since egee/eme/chem/math/
    envse/mnpr/mng/emch/stat/png/geosc/earth/geog catalogs were all already
    cached by those majors' own builds. Energy Engineering (ENGYMIN) --
    18cr exact bulletin match, computed 34cr against CMPSC after a real
    hidden-prereq chain (EGEE 302/EME 301 -> CHEM 112 -> CHEM 110 -> MATH
    22 -> MATH 21, plus EME 301's own MATH 250/251 branch) -- the ENGY
    major's own flowchart already supplies the entire chain, so the
    addition collapses to a near no-op against ENGY itself. Environmental
    Systems Engineering (ENVSYSMIN) -- 18cr exact bulletin match, computed
    37cr against CMPSC; its prescribed ENVSE 427 is a genuine 5-branch AND
    chain (CHEM 110 AND CHEM 112 AND MATH 141 AND MNPR 301 AND [CE 360 or
    EME 303]) -- picked EME 303 over CE 360 since EME 303's own chain is
    shallower than CE 360's EMCH 212 dependency. Mining Engineering
    (MINEMIN) -- 20cr exact bulletin match, all 7 courses prescribed with
    no elective choice, computed 35cr against CMPSC; every one of its
    prescribed courses is already independently required by the MINE
    major's own flowchart, so the MINE pairing is a near no-op -- flagged
    for human review that this means MINE-major students would satisfy
    essentially none of the bulletin's own stated 'six credits unique from
    the major' rule in real life, a limitation the planner doesn't model
    (same precedent already documented for FORMIN/WWRMIN/REBPMIN).
    Subsurface Energy Engineering (PNGMIN) -- 18cr exact bulletin match,
    the cleanest minor this batch: zero hidden-prereq chain needed against
    either verification target, since its elective pool has several
    genuinely prereq-free real options (EME 460, GEOSC 454) plus PNG 440W
    (whose only prereqs, PNG 305 and EME 200, are already prescribed by
    this same minor). Earth Systems (EASYSMIN) -- 18cr, Prescribed (EARTH
    2) + Additional (select 6cr, filled with EARTH 103N + GEOG 430, both
    prereq-free) are bulletin-exact; Supporting Courses (9cr) has no
    bulletin-published course list ('the Earth Systems Committee's
    approved list of courses') so it's modeled as a generic slot, matching
    the established precedent for unpublished pools (BIOL 400-level
    groups, ESC's Foundational/Technical Electives). Pairs with the
    already-built Earth Sciences major (EARTHSCI), which had already
    modeled its own 18cr 'one of five interdisciplinary minors'
    requirement as six generic 'Minor Course (Earth Systems)' slot items
    at build time, naming this exact minor as its assumed choice -- the
    second minor this session (after WWRMIN) to fulfill a slot EARTHSCI's
    own build notes had already flagged by name; since `merge_plans` only
    widens overlapping `course`-type items (not `slot`-type ones), the
    real EARTH/GEOG courses merge alongside, not in place of, the major's
    own placeholders, with 0 warnings either way. All 5 verified both
    against CMPSC (this catalog's standard baseline, grad_years=8) and
    their own real matching major (ENGY, ENVSYS, MINE, PNG, EARTHSCI) --
    0 warnings and `goal.met = True` in all 10 pairings on the first
    simulation, no data fixes needed after the upfront concurrent_groups/
    anti-requisite check this batch's research phase did before writing
    any JSON (following the accumulated lesson from WWRMIN's own
    concurrent-group miss and MATHMIN's own MATH 230/232 exclusion hit in
    earlier batches). 10 new tests added to this new
    `TestEighthRealMinorBatch` class (same `_merge_and_build` helper
    pattern as the prior batch's `TestSeventhRealMinorBatch`)."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_engymin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ENGYMIN", 34.0)

    def test_engymin_against_engy_major(self):
        self._merge_and_build("ENGY", "ENGYMIN")

    def test_envsysmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ENVSYSMIN", 37.0)

    def test_envsysmin_against_envsys_major(self):
        self._merge_and_build("ENVSYS", "ENVSYSMIN")

    def test_minemin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "MINEMIN", 35.0)

    def test_minemin_against_mine_major(self):
        self._merge_and_build("MINE", "MINEMIN")

    def test_pngmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "PNGMIN", 18.0)

    def test_pngmin_against_png_major(self):
        self._merge_and_build("PNG", "PNGMIN")

    def test_easysmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "EASYSMIN", 18.0)

    def test_easysmin_against_earthsci_major(self):
        self._merge_and_build("EARTHSCI", "EASYSMIN")


class TestNinthRealMinorBatch(unittest.TestCase):
    """5 more real minors (86 total): 4 close out the College of Eberly
    Science's own minor listing (Marine Sciences, Biochemistry and
    Molecular Biology, Quantum Information Science and Engineering,
    Information Sciences and Technology for Mathematics) plus one from the
    College of the Liberal Arts (Applied Linguistics) picked to diversify
    the batch beyond a single college. Marine Sciences (MARSCIMIN) -- 19cr
    exact bulletin match, computed 23cr against CMPSC; confirms a lead from
    an earlier batch that had ruled this program out as fictional -- it's
    real, just filed under Eberly Science rather than Earth and Mineral
    Sciences. Its Core Electives pool's WFS 452/453 share a real single
    BIOL 110 prerequisite (checked directly in wfs_catalog.json), added
    explicitly for the CMPSC pairing; a true no-op against the paired
    Wildlife and Fisheries Science major (WFS), which already requires
    BIOL 110 on its own flowchart. Biochemistry and Molecular Biology
    (BMBMIN) -- bulletin states 33-35cr, computed 35cr (the ceiling) since
    bmb_catalog.json fixes BMB 400 at a real 2cr rather than the bulletin's
    own 2-3cr range; computed 44cr against CMPSC after a real hidden-prereq
    chain (BMB 442's own prereq_groups require MICRB 201 specifically, plus
    the well-established CHEM 110 -> MATH 22 -> MATH 21 placement chain) --
    a near no-op against the paired BMB major, which already independently
    requires MICRB 201, MATH 21, and MATH 22. Quantum Information Science
    and Engineering (QISEMIN) -- bulletin states 21-22cr, computed 21cr (the
    floor), the cleanest minor of this batch: zero hidden-prereq additions
    needed against CMPSC since MATH 140/141 and CMPSC's own intro-
    programming pool already satisfy every prescribed course's real
    prerequisite; verified against the paired Physics major (PHYS), which
    already independently requires MATH 220 and PHYS 211/212/214.
    Information Sciences and Technology for Mathematics (ISMTHMIN) -- 18cr
    exact bulletin match. First draft filled the '3 of 5 MATH courses' slot
    with MATH 465 + MATH 468 (both needing only MATH 311W) and looked clean,
    but a live build against CMPSC surfaced a real 'could not schedule
    MATH 465, MATH 468, MATH 311W' warning: MATH 311W's own course
    description states students who passed CMPSC 360 may not schedule it
    for credit, and CMPSC's own required flowchart already includes CMPSC
    360 -- the same class of catalog-level anti-requisite bug documented for
    MATHMIN's MATH 230/232 exclusion in an earlier batch. Fixed by swapping
    to MATH 467 (whose own prereq is the OR-alternative {CMPSC 360, MATH
    311W}, satisfied for free by CMPSC's existing CMPSC 360 requirement) and
    MATH 451 (whose second branch needs MATH 230 or MATH 231; MATH 230 was
    picked specifically since MATH 230/231 are themselves a real mutual
    anti-requisite pair, and MATH 230 is the exact course the paired
    Mathematics major (MATH) already requires) -- computed 22cr against
    CMPSC (MATH 230 the only real net-new hidden addition), a near no-op
    against MATH itself. Applied Linguistics (APLNGMIN) -- 18cr exact
    bulletin match, fully clean, every APLNG course prereq- and concurrent-
    free; pairs with the already-built Applied Linguistics major (APLNGBA),
    which had no minor yet. All 5 verified both against CMPSC (this
    catalog's standard baseline, grad_years=8) and their own real matching
    major (WFS, BMB, PHYS, MATH, APLNGBA) -- 0 warnings and `goal.met = True`
    in all 10 pairings after the ISMTHMIN fix, with every candidate course's
    concurrent_groups field checked up front alongside prereq_groups per
    this project's accumulated methodology. 10 new tests added to this new
    `TestNinthRealMinorBatch` class (same `_merge_and_build` helper pattern
    as the prior batch's `TestEighthRealMinorBatch`)."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_marscimin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "MARSCIMIN", 23.0)

    def test_marscimin_against_wfs_major(self):
        self._merge_and_build("WFS", "MARSCIMIN")

    def test_bmbmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "BMBMIN", 44.0)

    def test_bmbmin_against_bmb_major(self):
        self._merge_and_build("BMB", "BMBMIN")

    def test_qisemin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "QISEMIN", 21.0)

    def test_qisemin_against_phys_major(self):
        self._merge_and_build("PHYS", "QISEMIN")

    def test_ismthmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ISMTHMIN", 22.0)

    def test_ismthmin_against_math_major(self):
        self._merge_and_build("MATH", "ISMTHMIN")

    def test_aplngmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "APLNGMIN", 18.0)

    def test_aplngmin_against_aplngba_major(self):
        self._merge_and_build("APLNGBA", "APLNGMIN")


class TestTenthRealMinorBatch(unittest.TestCase):
    """5 more real minors (91 total), a cross-college batch: African Studies
    and Middle East Studies (College of the Liberal Arts, from its own
    58-program listing), Russian (Liberal Arts), Biomedical Engineering
    (College of Engineering), and Agronomy (College of Agricultural
    Sciences). African Studies (AFRSTMIN) -- 18cr exact bulletin match,
    fully clean; the bulletin's own Prescribed Courses table lists a bare
    'AFR 110' with no title, which afr_catalog.json has no entry for -- only
    AFR 110N ('Introduction to Contemporary Africa'), used after confirming
    it's the same intro course. Verified against the paired African Studies
    major (AFRSTBA), which already requires AFR 110N/191/192 (a no-op for
    that 9cr) but has only a generic 'AFR 4XX' slot placeholder for its own
    400-level requirement, so the minor's AFR 403/405/202N (9cr) merge in as
    genuinely new. Russian (RUSMIN) -- 20cr exact bulletin match, fully
    clean; every RUS course in rus_catalog.json is prereq- and concurrent-
    free (language-sequence gating is advisement-level, not bulletin-
    encoded, same pattern as JAPNSMIN/KORMIN/CHNSMIN). Verified against the
    paired Russian major (RUSBA), which already requires the full 11cr
    Prescribed block; RUS 145/404/406 were deliberately picked over the
    major's own already-required RUS 402/403/405/141Y/142Y so the minor's
    remaining 9cr stay genuinely new for RUSBA students -- also surfaced a
    real 'credits differ per pairing' case: RUSBA's own flowchart models RUS
    401 as a 3cr OR-pool with RUS 402/403, one credit lower than this
    minor's own 4cr RUS 401 item, so the RUSBA-paired minor bucket reports
    19cr instead of 20cr (not asserted in the RUSBA-paired test, per
    established precedent). Biomedical Engineering (BMEMIN) -- 18cr bulletin
    match at the floor of the stated 18-20cr range, computed 35cr against
    CMPSC after a real hidden-prereq chain (BME 201's own CHEM 112 -> CHEM
    110 -> MATH 22 -> MATH 21 chain, the same MATH-21-placement-gate pattern
    seen repeatedly across this project, plus MATH 251 for BME 409) -- all 6
    hidden-prereq items were encoded as explicit requirements inside this
    minor's own file (same approach as MARSCIMIN's BIOL 110 addition), so
    plan_progress reports the same 35.0cr minor:BMEMIN bucket total against
    BOTH CMPSC and the paired BME major, even though BME already
    independently requires every one of those hidden-prereq courses plus
    BIOL 141/BME 201/BME 401/BME 450W on its own flowchart -- a near-total
    no-op there, with only BME 437 and BME 409 (6cr) being courses a real
    BME-major student wouldn't already be taking, closely matching the
    bulletin's own stated 'at least six credits unique from the major(s)'
    rule. Middle East Studies (MESTMIN) -- 18cr exact bulletin match, fully
    clean; no name-for-name PSU major exists, so verified against CMPSC and
    against the closest real major (INTPOL, International Politics) given
    the minor's PLSC-heavy footprint. Deliberately filled the Elective Pool
    entirely with non-language HIST/PLSC courses to avoid the ARAB/HEBR
    department prefixes, neither of which has a scraped catalog file in
    this project -- the same 'no scraped catalog' drop reason documented for
    other minors in earlier batches, sidestepped here instead of triggered.
    INTPOL requires an entirely different PLSC code set and no HIST courses
    at all, so the pairing is a genuine, substantive 18cr addition, not a
    near-total-overlap one. Agronomy (AGROMIN) -- 18cr exact bulletin match,
    fully clean; Elective Pool filled with AGRO 423/425 (both need only the
    already-prescribed AGRO 28), deliberately avoiding SOILS 402 (needs
    CHEM 110 in addition) and AGRO 438 (needs AGRO 28 AND HORT 101) to keep
    the pool prereq-free. Supporting Courses (6cr, the bulletin's own stated
    top of its 5-6cr range) has no published course list ('select 5-6
    credits in consultation with an adviser'), modeled as two generic 3cr
    slot items matching the established precedent for unpublished pools
    (EASYSMIN's Earth Systems Committee list). Verified against the paired
    Plant Sciences major (PLSCI, Agroecology option), which already
    independently requires AGRO 28 and SOILS 101 (a no-op for the 6cr
    Prescribed block), leaving AGRO 423/425 and the 6cr generic Supporting
    slot as genuinely new. All 5 verified both against CMPSC (this
    catalog's standard baseline, grad_years=8) and their own real matching
    major (AFRSTBA, RUSBA, BME, INTPOL, PLSCI) -- 0 warnings and
    `goal.met = True` in all 10 pairings on the first simulation, no data
    fixes needed after this batch's research phase checked every candidate
    course's `concurrent_groups` field alongside `prereq_groups` and
    cross-checked `excludes` data up front per this project's accumulated
    methodology. 10 new tests added to this new `TestTenthRealMinorBatch`
    class (same `_merge_and_build` helper pattern as the prior batch's
    `TestNinthRealMinorBatch`)."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_afrstmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "AFRSTMIN", 18.0)

    def test_afrstmin_against_afrstba_major(self):
        self._merge_and_build("AFRSTBA", "AFRSTMIN")

    def test_rusmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "RUSMIN", 20.0)

    def test_rusmin_against_rusba_major(self):
        self._merge_and_build("RUSBA", "RUSMIN")

    def test_bmemin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "BMEMIN", 35.0)

    def test_bmemin_against_bme_major(self):
        self._merge_and_build("BME", "BMEMIN")

    def test_mestmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "MESTMIN", 18.0)

    def test_mestmin_against_intpol_major(self):
        self._merge_and_build("INTPOL", "MESTMIN")

    def test_agromin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "AGROMIN", 18.0)

    def test_agromin_against_plsci_major(self):
        self._merge_and_build("PLSCI", "AGROMIN")


class TestEleventhRealMinorBatch(unittest.TestCase):
    """5 more real minors (96 total), a cross-college batch surveying
    colleges not yet fully exhausted: College of Health and Human
    Development (14-program listing), College of Education (7-program
    listing), Bellisario College of Communications (6-program listing), and
    College of Arts and Architecture (14-program listing) were each fetched
    directly before picking; the College of Information Sciences and
    Technology's own 2-program listing (IST, Security and Risk Analysis) was
    also fetched and confirmed already fully built in earlier batches
    (ISTMIN, SRAMIN). Education and Public Policy (EDPPMIN, College of
    Education) -- 18cr exact bulletin match, fully clean. Prescribed EDTHP
    115/200 (both already required by the paired Education and Public Policy
    major, EDPP -- a no-op for that 6cr). Additional Courses (bulletin
    allows any 400-level CIED/EDLDR course, any 200-400 EDTHP course, or any
    400-level HIED course) filled entirely with 400-level EDTHP courses
    (420/426/433/447), deliberately avoiding the CIED/EDLDR/HIED prefixes
    since none has a scraped catalog file in this project -- the same
    'no scraped catalog' drop reason documented in earlier batches, side-
    stepped here by staying within the EDTHP branch the bulletin itself
    already allows. Communication Sciences and Disorders (CSDMIN, College of
    Health and Human Development) -- 18cr exact bulletin match, computed
    21cr against CMPSC after a real hidden-prereq addition and one real bug
    caught by live simulation: the first draft's Interdisciplinary
    Connections pick, HDFS 428, looked clean against its own flattened
    catalog prereq_groups, but a live CMPSC build surfaced a real
    'could not schedule HDFS 428' warning -- its actual Enforced
    Prerequisite, confirmed on the live bulletin course-description page
    rather than trusted from the flattened groups, is '(HDFS 229 or PSYCH
    212) and HDFS 312W', a genuinely deeper chain than the scraped catalog
    suggested (HDFS 312W itself needs EDPSY 101 or STAT 200). Fixed by
    swapping to CSD 451 + CSD 462, both of whose real prereq is only CSD 300
    (itself needing only the already-prescribed CSD 146) -- one hidden-
    prereq course unlocking both electives at once, and all three (CSD
    300/451/462) already independently required by the paired CSD major,
    leaving CSD 111 + HDFS 249N (6cr) as the minor's genuinely unique value
    there. Information Sciences and Technology for Telecommunications
    (ISTTCMIN, Bellisario College of Communications) -- 18cr exact bulletin
    match, fully clean, zero hidden-prereq additions needed; code chosen to
    avoid colliding with the already-built plain IST minor (ISTMIN).
    Deliberately avoided COMM 479 from the Additional Courses pool once its
    live course-description page (not the flattened catalog groups) showed
    a real enforced 'COMM 180 and COMM 380' AND prerequisite, picking COMM
    484 + COMM 491 instead, both satisfied entirely by the already-
    prescribed COMM 180. Verified against the paired Telecommunications
    major (TELE), which has no IST department at all, leaving IST
    110/210/220 and COMM 484/491 (15cr) genuinely new there. Digital Media
    Trends and Analytics (DMTAMIN, Bellisario College of Communications) --
    18cr exact bulletin match, fully clean, zero hidden-prereq additions
    needed -- the single cleanest minor of this batch, since its Additional
    Courses picks (IST 310, COMM 370) directly satisfy the real prereq
    chain its own Prescribed cross-listed courses need (IST 310 unlocks
    COMM/IST 450, which unlocks COMM/IST 450A; COMM 370 unlocks COMM 372) --
    a fully self-contained 18cr set needing no separate hidden-prereq items.
    Verified against the paired Advertising/Public Relations major (ADPR),
    which already requires COMM 370/372 (6cr no-op) but has no IST
    department, leaving COMM 450/450A and IST 110/310 (12cr) genuinely new.
    Sport Studies (SPSTMIN, College of Health and Human Development) --
    18cr exact bulletin match, fully clean, zero hidden-prereq additions
    needed; Additional Courses filled with COMM 170 + KINES 100 (avoiding
    ASIA 101N, whose department prefix has no scraped catalog file), and
    Supporting Courses/Electives filled with KINES 419/426 (400-level,
    each needing only the already-selected KINES 100) plus RPTM 201/210
    (both prereq-free), meeting the bulletin's 'at least 6cr at the 400
    level' floor exactly. Verified against the paired Kinesiology major
    (KINES), which already requires KINES 100 (3cr no-op) but has no COMM
    or RPTM department, leaving 15cr genuinely new there. All 5 verified
    both against CMPSC (this catalog's standard baseline, grad_years=8) and
    their own real matching major (EDPP, CSD, TELE, ADPR, KINES) -- every
    candidate course's concurrent_groups field was confirmed empty
    alongside prereq_groups, and every chosen course's excludes field (plus
    a reverse scan of every catalog file for excludes listing any of this
    batch's chosen codes) was confirmed empty before writing any JSON, per
    this project's accumulated methodology -- 0 warnings and goal.met =
    True in all 10 pairings after the CSDMIN fix. 10 new tests added to
    this new TestEleventhRealMinorBatch class (same _merge_and_build helper
    pattern as the prior batch's TestTenthRealMinorBatch)."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_edppmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "EDPPMIN", 18.0)

    def test_edppmin_against_edpp_major(self):
        self._merge_and_build("EDPP", "EDPPMIN")

    def test_csdmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "CSDMIN", 21.0)

    def test_csdmin_against_csd_major(self):
        self._merge_and_build("CSD", "CSDMIN")

    def test_isttcmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ISTTCMIN", 18.0)

    def test_isttcmin_against_tele_major(self):
        self._merge_and_build("TELE", "ISTTCMIN")

    def test_dmtamin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "DMTAMIN", 18.0)

    def test_dmtamin_against_adpr_major(self):
        self._merge_and_build("ADPR", "DMTAMIN")

    def test_spstmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "SPSTMIN", 18.0)

    def test_spstmin_against_kines_major(self):
        self._merge_and_build("KINES", "SPSTMIN")


class TestTwelfthRealMinorBatch(unittest.TestCase):
    """5 more real minors (101 total), surveying candidates flagged but not
    fully researched by the prior batch: fetched the College of Health and
    Human Development's full 14-program minor listing, the College of
    Education's full 7-program listing, and the College of Arts and
    Architecture's full 14-program listing directly from each college's own
    bulletin page, then cross-referenced every program by TITLE against the
    running 96-minor built list per this project's established methodology.
    American Sign Language (ASLMIN, College of Health and Human Development)
    -- 18cr exact bulletin match, fully clean. Prescribed CSD 218/269/318/
    418/428 (American Sign Language I-IV + Deaf Culture); CSD 428's own real
    prereq is CSD 418, already prescribed one level down. Additional Courses
    (select one of CSD 111/146/230/240) filled with CSD 240, deliberately
    avoiding CSD 146/230 since both are already independently required by
    the paired Communication Sciences and Disorders major (CSD). Verified
    against CSD, which already requires CSD 269 (a real 3cr no-op, since
    Deaf Culture sits on both the major's flowchart and this minor's
    Prescribed table) but not the other 15cr. Beer and Wine Industry
    Management (BWIMIN, College of Health and Human Development) -- bulletin
    states 18-19cr; computed 19cr, the ceiling, since hm_catalog.json fixes
    HM 208/209 at real 1.5cr each. Prescribed HM 208/209/410/446 (HM 410 and
    446's own real prereq_groups are [['HM 208', 'HM 209']], already
    satisfied since both are prescribed); Elective Courses filled with FDSC
    223 + HM 311 + HM 322 + HORT 122, deliberately avoiding HM 101 (already
    required by the paired Hotel, Restaurant, and Institution Management
    major, HM) and HM 407/484 (real hidden prereqs neither major's flowchart
    supplies for free). Verified against HM, which requires none of this
    minor's chosen 19cr at all -- a fully non-overlapping pairing. Addictions
    and Recovery (ADRCMIN, College of Education) -- 18cr exact bulletin
    match, fully clean. Prescribed BBH 143; Additional Courses (select 15cr
    from a broad cross-listed pool) filled with CI 333 + CRIM 424 + CRIM 451
    + EDTHP 420 + RHS 428, all prereq-free, deliberately avoiding the CNED/
    CRIMJ/HIED prefixes (no scraped catalog file for any of the three in
    this project -- the same drop reason documented in earlier batches) and
    avoiding RHS 300/301/302/303/400W/401/402/403 since all are already
    independently required by the paired Rehabilitation and Human Services
    major (RHS). Verified against RHS, whose flowchart requires none of this
    minor's five chosen electives -- the full 18cr is genuinely new there.
    Social Justice in Education (SJEDMIN, College of Education) -- bulletin
    states 18-21cr; computed 18cr, the floor. Prescribed CI 185/285/485;
    Additional Courses filled with AFAM 103 (3cr sub-pool) + CI 280 + CI 385
    (6cr sub-pool, the floor of its 6-9cr range), deliberately avoiding the
    CIVCM/GLIS/SCIED/WFED/WLED prefixes (no scraped catalog file for any).
    Verified against the paired Education and Public Policy major (EDPP),
    whose own flowchart has no CI or AFAM courses at all -- a fully
    non-overlapping pairing. Architecture Studies (ARSTMIN, College of Arts
    and Architecture) -- 18cr exact bulletin match, fully clean. Lower-Level
    Courses (select 12cr from a pool where ARCH 121/122/130A/131/132 are
    restricted to Architecture/Architectural Engineering majors only) filled
    with the pool's four unrestricted options: AA 121 + ARCH 100 + ARCH 170N
    + ARCH 210, keeping the minor buildable for non-Architecture majors too;
    Upper-Level Courses filled with ARCH 410 + ARCH 412, deliberately
    avoiding ARCH 441 (real prereq ARCH 130A) and ARCH 442 (real prereq ARCH
    441). Verified against the paired Architecture major (ARCHBARCH, the
    5-year B.Arch track), which already requires ARCH 210 (a real 3cr no-op)
    but not the other 15cr. All 5 verified both against CMPSC (this
    catalog's standard baseline, grad_years=8) and their own real matching
    major (CSD, HM, RHS, EDPP, ARCHBARCH) -- every candidate course's
    concurrent_groups field was confirmed empty alongside prereq_groups, and
    every chosen course's excludes field (plus a reverse scan of every
    catalog file for excludes listing any of this batch's chosen codes) was
    confirmed empty before writing any JSON. 0 warnings and goal.met = True
    in all 10 pairings on the first simulation, no data fixes needed. 10 new
    tests added to this new TestTwelfthRealMinorBatch class (same
    _merge_and_build helper pattern as the prior batch's
    TestEleventhRealMinorBatch)."""

    def _merge_and_build(self, major_code, minor_code, expected_minor_credits=None):
        import datetime
        major = engine.load_degree_plan(major_code, 2026)
        minor = engine.load_minor_plan(minor_code, 2026)
        self.assertIsNotNone(minor)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(),
            start_year=2026, grad_years=8, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        if expected_minor_credits is not None:
            progress = engine.plan_progress(merged, set())
            bucket = progress["by_category"].get(f"minor:{minor_code}")
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket["total_credits"], expected_minor_credits)

    def test_aslmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ASLMIN", 18.0)

    def test_aslmin_against_csd_major(self):
        self._merge_and_build("CSD", "ASLMIN")

    def test_bwimin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "BWIMIN", 19.0)

    def test_bwimin_against_hm_major(self):
        self._merge_and_build("HM", "BWIMIN")

    def test_adrcmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ADRCMIN", 18.0)

    def test_adrcmin_against_rhs_major(self):
        self._merge_and_build("RHS", "ADRCMIN")

    def test_sjedmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "SJEDMIN", 18.0)

    def test_sjedmin_against_edpp_major(self):
        self._merge_and_build("EDPP", "SJEDMIN")

    def test_arstmin_against_cmpsc(self):
        self._merge_and_build("CMPSC", "ARSTMIN", 18.0)

    def test_arstmin_against_archbarch_major(self):
        self._merge_and_build("ARCHBARCH", "ARSTMIN")


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
        """After the MATH 21 placement-gate fix, only MATH 21 itself
        (unlocking STAT 200/SCM 200's real prereq) is needed — MATH 21 has
        no further real prereq beyond PSU's math placement exam, so the
        MATH 3 -> 4 padding an earlier pass added defensively (before this
        was verified against the live bulletin) was unnecessary and was
        removed; without it the plan fits cleanly in 8 terms."""
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

    def test_full_plan_reaches_graduation_in_five_years(self):
        """BMB's real chemistry/biochemistry sequence (CHEM 110 -> ... ->
        CHEM 210/212/213 -> BMB 251 -> BMB 400/401/402/442/443W/445W/448)
        is genuinely too long for 8 terms even with the MATH 21
        placement-gate fix in place — a real structural minimum, not a
        catalog gap."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 9)

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

    def test_full_plan_reaches_graduation_in_five_years(self):
        """MICRB's real CHEM->BMB->MICRB chemistry/biochemistry sequence
        (CHEM 110->...->CHEM 210/212/213->BMB 251->BMB 400/401/402) is
        long enough that even with the MATH 21 placement-gate fix, the
        major genuinely needs a 5th year — verified this isn't an
        artifact of the plan's own harmless MATH-chain padding by
        confirming it still needs an extra term even with every MATH
        prereq maximally relaxed."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
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

    def test_full_plan_reaches_graduation_in_five_years(self):
        """CHE's real chemistry/chemical-engineering sequence is genuinely
        too long for 8 terms even with the MATH 21 placement-gate fix in
        place — a real structural minimum, not a catalog gap."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
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

    def test_full_plan_reaches_graduation_in_five_years(self):
        """IID's real VBSC/MICRB/BMB course sequence (including the BMB
        400 -> VBSC 448W chain) is genuinely too long for 8 terms even
        with the MATH 21 placement-gate fix in place — a real structural
        minimum, not a catalog gap."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 10)

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

    def test_chem_110_has_no_concurrency_requirement(self):
        """CHEM 110's real, live-bulletin-verified enforced prerequisite is
        'Completion of or placement beyond MATH 22' -- a plain prerequisite,
        with no MATH 140B (or any other) concurrent-enrollment requirement.
        An earlier version of this test asserted the opposite (a stale
        MATH-140B-concurrency assumption from before this was verified
        directly against bulletins.psu.edu); this locks in the corrected,
        bulletin-accurate shape instead."""
        course = self.catalog.get("CHEM 110")
        self.assertIsNotNone(course)
        self.assertEqual(course.concurrent_groups, [])


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
        """Regression test: THEA 120 must accept THEA 106 as one of its
        prerequisite alternatives (verified directly against the live
        bulletin: THEA 106 is a real OR-alternative in THEA 120's enforced
        prerequisite, alongside DANCE 100/THEA 100/THEA 105 — not a
        concurrent/same-term allowance)."""
        course = self.catalog.get("THEA 120")
        self.assertIn("THEA 106", {c for group in course.prereq_groups for c in group})


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


class TestJapaneseBAPlan(unittest.TestCase):
    """Japanese, B.A. New japns_catalog.json (47 courses, all
    prereq-free). JAPNS 450 and JAPNS 433 don't exist in the real
    catalog -- dropped from their option pools, each still leaving
    several real, clean alternatives."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("JAPNSBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a Japanese major"), "JAPNSBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestKoreanBAPlan(unittest.TestCase):
    """Korean, B.A. New kor_catalog.json (42 courses, all prereq-free).
    Nearly identical structure to the Japanese, B.A. build. KOR 121 and
    KOR 450 don't exist in the real catalog -- dropped from their
    pools."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("KORBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a Korean major"), "KORBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestAfricanStudiesBAPlan(unittest.TestCase):
    """African Studies, B.A. New afr_catalog.json (45 courses).
    Bulletin's own table lists 'AFR 110' but the real code is
    'AFR 110N' (Inter-Domain suffix omitted in the bulletin table)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AFRSTBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an African Studies major"), "AFRSTBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestSustainabilitySocietyEnvironmentalGeographyPlan(unittest.TestCase):
    """Sustainability, Society, and Environmental Geography, B.A. Fully
    reuses geog_catalog.json/emsc_catalog.json. Several variable-credit
    bulletin rows resolved to their lower value, matching the bulletin's
    own stated 117-125cr range at its floor."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("SSEVG", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a sustainability society and environmental geography major"), "SSEVG")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestAnthropologicalScienceBSPlan(unittest.TestCase):
    """Anthropological Science, B.S. -- Integrated Option. Distinct
    program from the already-built Anthropology, B.A. Re-scraped
    anth_catalog.json (was partial, now 90 courses). 'ANTH 2N, 45N, or
    21' appears three times across a 3-option pool -- effectively 'take
    all three,' which also guarantees ANTH 21 is completed before
    ANTH 426W/427W need it."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("ANTHSBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an anthropological science major"), "ANTHSBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestLandscapeContractingPlan(unittest.TestCase):
    """Landscape Contracting, B.S. -- Design/Build Option. New
    hort_catalog.json (was 2 courses, now 45) and art_catalog.json (was
    stale, now 92). Real hidden-prereq gap: ACCTG 211 needs MATH 21, but
    the major's own required math course is MATH 26 (Trigonometry) --
    a genuinely different course. Added MATH 21 explicitly rather than
    leaving ACCTG 211 permanently unschedulable."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("LSCPE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a landscape contracting major"), "LSCPE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)

    def test_acctg_211_prereq_unlocked_by_explicit_math_21(self):
        # Regression test for the real gap this build caught: ACCTG 211
        # needs MATH 21/110/140, but the major's own required MATH
        # course (MATH 26, Trigonometry) doesn't satisfy it.
        acctg = self.catalog["ACCTG 211"]
        self.assertTrue(engine.prereqs_satisfied(acctg, {"MATH 21"}))
        self.assertFalse(engine.prereqs_satisfied(acctg, {"MATH 26"}))


class TestGeographyBAPlan(unittest.TestCase):
    """Geography, B.A. -- companion to the already-built Geography, B.S.,
    fully reuses geog_catalog.json. Four gateway items (GEOG
    210/220/230/260) all share the same 4-course option pool -- since a
    student can only take each course once, this is effectively 'take
    all four.' Bulletin's own advising note says STAT 200 is a real
    prereq for GEOG 364 even though the catalog's own GEOG 364 entry
    shows no prereq -- resolved by deliberately picking STAT 200 (not a
    generic slot) for the GQ Foundation item."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("GEOBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a geography BA major"), "GEOBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestMathematicsBAPlan(unittest.TestCase):
    """Mathematics, B.A. -- companion to the already-built Mathematics,
    B.S., fully reuses math_catalog.json/cmpsc_catalog.json/
    stat_catalog.json. Two variable-credit bulletin rows (MATH 250 or
    251 '3-4cr', a Supporting Course '3-4cr') resolved to their lower
    value, matching the bulletin's own stated 119-121cr range at its
    floor."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("MATHBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a mathematics BA major"), "MATHBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestOrganizationalLeadershipBSPlan(unittest.TestCase):
    """Organizational Leadership, B.S. -- companion to the already-built
    Organizational Leadership, B.A. (major code OLEAD). Re-scraped
    olead_catalog.json (was 5 courses, now 15) to pick up OLEAD
    220/410/411, needed for a pool item the B.A. build never needed.
    Cross-listed pool picks all made for zero additional prereq burden."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("OLEADBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an organizational leadership BS major"), "OLEADBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestWomensGenderSexualityStudiesBSPlan(unittest.TestCase):
    """Women's, Gender, and Sexuality Studies, B.S. -- companion to the
    already-built WGSS, B.A. (major code WMNSTBA). Fully reuses
    wmnst_catalog.json -- all 9 real courses this plan needs were already
    cataloged from the B.A. build, no re-scrape required. Same 'WMNST
    83S' data artifact already documented in the B.A. build (doesn't
    exist in the real catalog) -- dropped from its option pool."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("WMNSTBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a women's, gender, and sexuality studies BS major"), "WMNSTBS")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestAppliedLinguisticsBAPlan(unittest.TestCase):
    """Applied Linguistics, B.A. -- distinct program from the
    already-built Linguistics, B.A. (major code LING), own department.
    Re-scraped aplng_catalog.json (was 2 courses, now 25) to pick up
    APLNG 290N/320N/450/494, all real and prereq-free."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("APLNGBA", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an applied linguistics major"), "APLNGBA")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertLessEqual(len(fp["terms"]), 9)


class TestArchitecturalEngineeringPlan(unittest.TestCase):
    """Architectural Engineering, B.A.E. -- one of PSU's few 5-year (10
    semester) professional bachelor's programs. Built the Mechanical
    option's 'Direct Entry from ENGAE to AE' path. Two real bugs caught
    while building: a truncated first fetch dropped 'ENGL 15, 30H, or
    ESL 15' from First Year Fall entirely, which made the Fifth Year's
    ENGL 202C permanently unschedulable; and the bulletin's own table
    states MATH 220 is 3cr while the real MATH 220 catalog entry is 2cr
    -- trusted the catalog (used at simulation time) over the stale
    bulletin annotation."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I want to study architectural engineering"), "AE")

    def test_full_plan_reaches_graduation_in_five_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 10)


class TestArtificialIntelligenceEngineeringPlan(unittest.TestCase):
    """Artificial Intelligence Engineering, B.S. Real hidden-prereq/
    concurrent gaps found, none present anywhere in the bulletin's own
    plan: A-I 305 needs MATH 220 (hard) and MATH 231/230; A-I 370 needs
    STAT 200/DS 200 as a prereq AND one of CMPSC 465/DS 305/CMPSC 462
    concurrently -- the concurrent gap only surfaced via
    concurrent_satisfied returning False despite prereqs_satisfied
    passing, not from reading prereq_groups alone. Scraped a new AIE
    department catalog (AIE 355, AIE 489W)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AIE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("my major is AI engineering"), "AIE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestDataSciencesEngineeringPlan(unittest.TestCase):
    """Data Sciences, B.S. (Engineering) -- a separate program from the
    already-built Eberly Science DS major and from IST's DATSC, with its
    own plan code (DTSCE_BS) and suggested academic plan. Built the
    'Computational Data Sciences Option'. Two List A and two List B
    picks (CMPSC 450 + MATH 484, DS 441 + EE 456) chosen for zero
    additional prereq burden beyond what's already in this plan."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("DTSCE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I'm doing data sciences engineering"), "DTSCE")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestDataSciencesISTPlan(unittest.TestCase):
    """Data Sciences, B.S. (Information Sciences and Technology) --
    third distinct PSU Data Sciences B.S. program (Science, Engineering,
    IST each have their own). Built the 'Applied Data Sciences Option'.
    Real hidden-prereq gap: DS 200 needs MATH 21, not present anywhere
    in the bulletin's own plan -- added explicitly. 'DS 440W' in the
    bulletin table doesn't exist as a real course code; the real code is
    'DS 440' (same typo pattern caught building the Engineering DS
    major)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("DATSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("applied data sciences is my major"), "DATSC")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestCommunicationArtsSciencesBSPlan(unittest.TestCase):
    """Communication Arts and Sciences, B.S. -- sibling of the
    already-built CASBA (B.A.), no world-language requirement, more
    Supporting Course / GQ credits instead. Reused the B.A. build's
    conventions for the same literal courses and adviser-driven slots.
    Computed 8-semester total (123cr) matches the bulletin's own stated
    total exactly."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("CASBS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("communication arts and sciences BS"), "CASBS")

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
                    # MATH 21, not MATH 140/141 — a real, prereq-free math
                    # course (its own enforced prereq is just PSU's math
                    # placement exam) that still leaves CMPSC 101 blocked.
                    {"type": "course", "options": ["MATH 21"], "credits": 4},
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

    def test_full_plan_reaches_graduation_in_five_years(self):
        """PREMED's real chemistry/biochemistry sequence (including
        CHEM 110, gated behind the real MATH 22 -> MATH 21 chain, and the
        BMB 401 -> 402 biochemistry sequence) is genuinely too long for
        8 terms even with the MATH 21 placement-gate fix in place — a
        real structural minimum, not a catalog gap. (PSU's own bulletin
        plan compresses this into 8 semesters by assuming AP/transfer
        credit for the early math sequence, which this simulation, run
        from zero completed courses, does not have.)"""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 10)

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
        pairing — must not be pushed a full term later than necessary.
        CHEM 110 itself now lands in term 5, not term 1, because its real
        enforced prerequisite ('Completion of or placement beyond MATH 22')
        genuinely requires the MATH 3 -> 4 -> 21 -> 22 remedial chain first
        (see test_full_plan_reaches_graduation_in_five_years) — this test
        only cares that the lab isn't deferred a further term beyond its
        lecture, wherever that lecture ends up landing."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        codes_by_term = [
            {p["code"] for p in t["courses"] if p["code"]} for t in fp["terms"]
        ]
        chem_110_term = next(i for i, codes in enumerate(codes_by_term) if "CHEM 110" in codes)
        self.assertIn("CHEM 111", codes_by_term[chem_110_term])

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

    def test_settings_only_request_preserves_bulk_completed_slots(self):
        # Regression: "I'm a junior" marks generic slots (GEN ED, etc.) done
        # via consumed_slot_ids, computed fresh from the prompt each request
        # — NOT part of completed[]. A later settings-only change (e.g.
        # toggling "Allow Summer Courses") sends an empty prompt, so without
        # echoing consumed_slot_ids back and re-sending it, those slots would
        # silently look unmet again even though nothing the student actually
        # completed changed. This proved a real bug: toggling summer dropped
        # requirements-done from 20/42 to 16/42 for the exact same student.
        first = self.client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "I'm a junior", "completed": [], "start_year": 2026,
        }).get_json()
        progress_before = first["coursePlan"]["progress"]
        self.assertGreater(progress_before["doneItems"], 0)
        state = first["state"]
        self.assertIn("consumedSlotIds", state)

        second = self.client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": state["completed"],
            "start_year": 2026, "allow_summer": True,
            "consumed_slot_ids": state["consumedSlotIds"],
        }).get_json()
        progress_after = second["coursePlan"]["progress"]
        self.assertEqual(progress_after["doneItems"], progress_before["doneItems"])
        self.assertEqual(progress_after["creditsDone"], progress_before["creditsDone"])

        # And the actual bug reproduction: omitting consumed_slot_ids (as the
        # pre-fix frontend always did) must NOT silently claim it was carried
        # forward — this documents the failure mode the fix closes, not just
        # the happy path.
        without_ids = self.client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": state["completed"],
            "start_year": 2026, "allow_summer": True,
        }).get_json()
        progress_without = without_ids["coursePlan"]["progress"]
        self.assertLess(progress_without["doneItems"], progress_before["doneItems"])

    def test_consumed_slot_ids_from_a_different_plan_shape_are_dropped_not_misapplied(self):
        # Slot ids are only meaningful against the exact plan they were
        # computed for — merge_plans renumbers ids whenever majors/minors
        # change. Feeding back ids from a since-changed plan must be a
        # harmless no-op, never silently mark the wrong item done.
        junior = self.client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "I'm a junior", "completed": [], "start_year": 2026,
        }).get_json()
        stale_ids = junior["state"]["consumedSlotIds"]
        self.assertTrue(stale_ids)

        r = self.client.post("/api/plan", json={
            "major": "MATH", "prompt": "", "completed": [], "start_year": 2026,
            "consumed_slot_ids": stale_ids,
        })
        self.assertEqual(r.status_code, 200)
        # No crash, and nothing from CMPSC's plan bleeds into MATH's progress.
        self.assertEqual(r.get_json()["coursePlan"]["dept"], "MATH")

    def test_chat_detected_major_switch_drops_stale_slot_ids_even_when_payload_major_is_unchanged(self):
        # The riskier variant of the above: the client's `major` field can
        # lag a real major switch by one request, since the switch is only
        # detected from THIS request's own prompt text (e.g. the student
        # types "actually I'm a NURS major" while the UI's dropdown/payload
        # still says CMPSC). If consumed_slot_ids from the old CMPSC session
        # rode along, they'd be validated against NURS's real item ids —
        # which, being small sequential integers, could coincidentally
        # collide with genuine NURS requirements and mark them wrongly done.
        junior = self.client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "I'm a junior", "completed": [], "start_year": 2026,
        }).get_json()
        stale_ids = junior["state"]["consumedSlotIds"]
        self.assertTrue(stale_ids)

        switched = self.client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "Actually I'm a NURS major",
            "completed": [], "start_year": 2026, "consumed_slot_ids": stale_ids,
        }).get_json()
        self.assertEqual(switched["coursePlan"]["dept"], "NURS")
        # The stale ids must not have been silently applied against NURS's
        # plan — with nothing actually completed, nothing should read done.
        self.assertEqual(switched["state"]["consumedSlotIds"], [])
        self.assertEqual(switched["coursePlan"]["progress"]["doneItems"], 0)

    def test_campuses_endpoint(self):
        r = self.client.get("/api/campuses")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["default"], "University Park")
        self.assertIn("University Park", d["campuses"])
        self.assertIn("Erie", d["campuses"])

    def test_degree_plans_default_mostly_university_park(self):
        # Almost every plan defaults to UP (no "campus" field) — but a real,
        # deliberate few (BUSINESS, ESUS, SUR — see docs/BRANCH_CAMPUS_FINDINGS.md)
        # are bulletin-verified to NOT be offered at UP at all, so this can't
        # be a blanket "every plan" assertion without baking in a falsehood.
        # Only the -2026 file of each was updated with real campus data —
        # BUSINESS/MGMT also have untouched 2022-2025 files (still default
        # UP, not yet backfilled — that's future work, not this pass), so
        # every lookup below is pinned to catalog_year 2026 to avoid
        # accidentally matching one of those.
        r = self.client.get("/api/degree-plans")
        plans = {(p["major"], p["catalog_year"]): p for p in r.get_json()["plans"]}
        self.assertTrue(plans)
        self.assertEqual(plans[("CMPSC", 2026)]["campus"], "University Park")
        self.assertNotEqual(plans[("BUSINESS", 2026)]["campus"], "University Park")
        self.assertNotEqual(plans[("ESUS", 2026)]["campus"], "University Park")
        self.assertNotEqual(plans[("SUR", 2026)]["campus"], "University Park")

    def test_degree_plans_filtered_by_erie_returns_real_multi_campus_major(self):
        # MGMT-2026 is real, bulletin-verified data at Erie (one of 13
        # non-closing campuses it's offered at) — the pre-multi-campus
        # assumption that any non-UP campus filter returns nothing no
        # longer holds.
        r = self.client.get("/api/degree-plans?campus=Erie")
        plans = {(p["major"], p["catalog_year"]): p for p in r.get_json()["plans"]}
        self.assertIn(("MGMT", 2026), plans)
        self.assertEqual(plans[("MGMT", 2026)]["campus"], "Erie")
        # CMPSC is UP-only (Engineering-college program, not in MGMT's
        # shared-curriculum "2+2" pattern) — must not leak into Erie's list.
        self.assertNotIn(("CMPSC", 2026), plans)

    def test_degree_plans_filtered_by_university_park_excludes_non_up_majors(self):
        unfiltered = {
            (p["major"], p["catalog_year"]) for p in self.client.get("/api/degree-plans").get_json()["plans"]
        }
        up = {
            (p["major"], p["catalog_year"])
            for p in self.client.get("/api/degree-plans?campus=University Park").get_json()["plans"]
        }
        # A real, deliberate few plans (BUSINESS-2026, ESUS-2026, SUR-2026)
        # are verified to have no University Park offering at all —
        # everything else should still show up, since UP is still every
        # other plan's default (including BUSINESS/MGMT's own untouched
        # historical years, which is expected — only 2026 was updated).
        self.assertLess(up, unfiltered)
        self.assertNotIn(("BUSINESS", 2026), up)
        self.assertNotIn(("ESUS", 2026), up)
        self.assertNotIn(("SUR", 2026), up)
        self.assertIn(("CMPSC", 2026), up)

    def test_degree_plans_filtered_by_world_campus_includes_real_world_campus_only_majors(self):
        r = self.client.get("/api/degree-plans?campus=World Campus")
        majors = {(p["major"], p["catalog_year"]) for p in r.get_json()["plans"]}
        self.assertIn(("BUSINESS", 2026), majors)
        self.assertIn(("ESUS", 2026), majors)
        self.assertNotIn(("CMPSC", 2026), majors)  # UP-only, no World Campus offering

    def test_degree_plan_campuses_list_present_for_multi_campus_major(self):
        r = self.client.get("/api/degree-plans")
        mgmt = next(
            p for p in r.get_json()["plans"] if p["major"] == "MGMT" and p["catalog_year"] == 2026
        )
        self.assertIn("Erie", mgmt["campuses"])
        self.assertIn("University Park", mgmt["campuses"])
        self.assertGreater(len(mgmt["campuses"]), 1)

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


class TestAndOrPrereqParsing(unittest.TestCase):
    """Real bug found by the user: 'A and (B or C) and (D or E)' was being
    flattened into ONE merged OR-group over all courses the instant any
    parenthesis appeared, instead of three separate AND-required groups
    with OR-alternatives inside two of them. CMPSC 489's real prerequisite
    -- 'MATH 141 and (MATH 220 or MATH 430 or MATH 436) and (STAT 318 or
    STAT 319 or STAT 414 or STAT 415 or STAT 418 or EE 465)' -- used to
    scrape as a single 10-course OR-group, meaning a student who'd taken
    only EE 465 (and nothing else) would incorrectly show as eligible.
    Fixture HTML below is the real courseblockextra markup for CMPSC 489,
    captured directly from bulletins.psu.edu, so this test fails the same
    way the live scrape did before the fix if the parser regresses."""

    def _groups_from_html(self, html):
        import Courseplanner as cp
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        strong = soup.find("strong")
        return cp._and_or_groups_from_scope(cp._label_scope_nodes(strong))

    def test_and_of_or_groups_real_cmpsc_489_prereq(self):
        html = """
        <p class="noindent">
          <strong>Enforced Prerequisite at Enrollment:</strong>
          <a class="bubblelink code" title="MATH 141">MATH 141</a>
          and (
          <a class="bubblelink code" title="MATH 220">MATH 220</a>
          or
          <a class="bubblelink code" title="MATH 430">MATH 430</a>
          or
          <a class="bubblelink code" title="MATH 436">MATH 436</a>
          ) and (
          <a class="bubblelink code" title="STAT 318">STAT 318</a>
          or
          <a class="bubblelink code" title="STAT 319">STAT 319</a>
          or
          <a class="bubblelink code" title="STAT 414">STAT 414</a>
          or
          <a class="bubblelink code" title="STAT 415">STAT 415</a>
          or
          <a class="bubblelink code" title="STAT 418">STAT 418</a>
          or
          <a class="bubblelink code" title="EE 465">EE 465</a>
          ) Recommended Preparations: Machine learning
        </p>
        """
        groups = self._groups_from_html(html)
        self.assertEqual(len(groups), 3, f"expected 3 AND-groups, got {groups}")
        self.assertEqual(groups[0], {"MATH 141"})
        self.assertEqual(groups[1], {"MATH 220", "MATH 430", "MATH 436"})
        self.assertEqual(
            groups[2], {"STAT 318", "STAT 319", "STAT 414", "STAT 415", "STAT 418", "EE 465"},
        )

        # Live semantics check: the user's own worked example.
        course = engine.Course(
            code="CMPSC 489", name="Deep Learning for Computer Vision", credits=3.0,
            prereq_groups=[set(g) for g in groups], concurrent_groups=[],
        )
        self.assertTrue(engine.prereqs_satisfied(course, {"MATH 141", "MATH 220", "STAT 318"}))
        self.assertFalse(engine.prereqs_satisfied(course, {"EE 465"}))  # old buggy behavior
        self.assertFalse(engine.prereqs_satisfied(course, {"MATH 141", "STAT 318"}))  # missing group 2

    def test_simple_and_chain_without_parens_still_splits(self):
        # No parens at all — plain "A and B" must still split into two
        # separate required groups, unchanged from before this fix.
        html = """
        <p><strong>Enforced Prerequisite at Enrollment:</strong>
        <a title="MATH 140">MATH 140</a> and <a title="MATH 141">MATH 141</a></p>
        """
        groups = self._groups_from_html(html)
        self.assertEqual(groups, [{"MATH 140"}, {"MATH 141"}])

    def test_simple_or_chain_without_parens_stays_one_group(self):
        html = """
        <p><strong>Enforced Prerequisite at Enrollment:</strong>
        <a title="CMPSC 121">CMPSC 121</a> or <a title="CMPSC 131">CMPSC 131</a></p>
        """
        groups = self._groups_from_html(html)
        self.assertEqual(groups, [{"CMPSC 121", "CMPSC 131"}])

    def test_nested_and_inside_parens_falls_back_to_one_merged_group(self):
        # Genuinely ambiguous case ('X or (Y and Z)') — permissive fallback,
        # same as before this fix, since PSU's real intent can't be inferred.
        html = """
        <p><strong>Enforced Prerequisite at Enrollment:</strong>
        <a title="CMPSC 465">CMPSC 465</a> or (<a title="CMPSC 360">CMPSC 360</a>
        and <a title="MATH 220">MATH 220</a>)</p>
        """
        groups = self._groups_from_html(html)
        self.assertEqual(groups, [{"CMPSC 465", "CMPSC 360", "MATH 220"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
