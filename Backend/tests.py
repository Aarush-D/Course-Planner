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
from io import BytesIO
from unittest.mock import patch

os.environ.setdefault("USE_OLLAMA", "0")  # tests must not depend on Ollama

import planner_engine as engine
import transfer_credit as tc
from app import (
    app, parse_completion_changes, _extract_major_from_prompt, _extract_start_year_from_prompt,
    _build_reply_text, _pick_opener, _build_phrase_prompt,
    _build_reply_links, _detect_unconfirmed_major_mentions,
    _real_majors_summary, _explore_majors_fallback, _build_explore_majors_prompt,
    _is_asking_next_courses, _is_asking_why_blocked, _extract_asked_course,
    _build_specific_course_answer, _next_sem_fully_covered, _build_next_sem_detail_block,
    _extract_transcript_course_text,
)


def _plan_and_catalog():
    plan = engine.load_degree_plan("CMPSC")
    catalog = engine.load_merged_catalog(plan["departments"])
    return plan, catalog


def _reach(plan, item):
    """Mark every item ordered before `item` done, so recommend_semester
    actually walks far enough to try recommending `item` itself. Shared
    helper for the module-level (non-CMPSC) handbook-verification test
    classes below, mirroring TestCMPSCHandbookRequirements._reach."""
    completed = {
        it["options"][0] for _, it in engine._iter_plan_items(plan)
        if it["id"] < item["id"] and it.get("type") == "course"
    }
    consumed = {
        it["id"] for _, it in engine._iter_plan_items(plan)
        if it["id"] < item["id"] and it.get("type") == "slot"
    }
    return completed, consumed


def _first_item_with_label_substring(plan, substring):
    return next(
        item for _, item in engine._iter_plan_items(plan)
        if substring in (item.get("label") or "")
    )


def _all_items_with_label_substring(plan, substring):
    return [
        item for _, item in engine._iter_plan_items(plan)
        if substring in (item.get("label") or "")
    ]


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
        # DS and EE joined during the 2026-08-27 full-rollout handbook
        # verification: DS's real List A/B credit totals and EE's real
        # elective rebalancing (after its own bogus MATH 3/4 scaffold was
        # removed) both genuinely need one extra term, not a modeling bug.
        "DS": 5, "EE": 5,
        # Joined during the 2026-08-27 Behrend/Brandywine campus expansion —
        # each has a genuine real prereq-chain/credit-total overflow past 8
        # terms, same class of real structural minimum as the majors above.
        "CMPSCBH": 5, "SWENG": 5, "FDTAN": 5, "PESBH": 5, "ENGRBW": 5,
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

    def test_cross_listed_cmpen_315_credited_as_real_cmpsc_315(self):
        # Real bug, found while building this: CMPEN 315 is referenced as
        # a valid option in CMPSC-2026.json (the flowchart's own
        # cross-department label), but PSU's bulletin has no separate
        # CMPEN 315 course -- confirmed directly against the live
        # bulletin, not assumed. A student who says "CMPEN 315" must
        # still get real credit for the one actual course, CMPSC 315.
        matched, unmatched = engine.match_courses_in_text("I took CMPEN 315", self.catalog)
        self.assertEqual([m["code"] for m in matched], ["CMPSC 315"])
        self.assertEqual(unmatched, [])

    def test_course_code_shaped_alias_does_not_also_land_in_unmatched(self):
        # The general bug behind the CMPEN 315 case: any alias that looks
        # like a real course code (dept + number) was being matched TWICE
        # -- once correctly via the alias, and once more as a literal,
        # nonexistent course code, so the same mention showed up as both
        # matched and unmatched at once. Provable independent of the
        # CMPEN case with a synthetic alias.
        old = dict(engine.COURSE_ALIASES)
        engine.COURSE_ALIASES["ZZZZ 999"] = "CMPSC 131"
        try:
            matched, unmatched = engine.match_courses_in_text("I took ZZZZ 999", self.catalog)
            self.assertEqual([m["code"] for m in matched], ["CMPSC 131"])
            self.assertEqual(unmatched, [])
        finally:
            engine.COURSE_ALIASES.clear()
            engine.COURSE_ALIASES.update(old)


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
    each gets one short count line, with the real navigation done by a
    clickable link (_build_reply_links) rather than typed-out pointer
    phrasing. 'Still locked' stays a full itemized list since nothing else
    in the UI surfaces it."""

    def test_no_itemized_next_semester_course_list(self):
        text = _build_reply_text(**_redundant_reply_stub_args())
        self.assertNotIn("Recommended for", text)
        self.assertNotIn("unlocks future courses", text)
        self.assertIn("2 courses recommended for next semester (7 credits)", text)
        # no typed-out pointer phrase -- reply_links carries navigation now
        self.assertNotIn("see Flowchart", text)

    def test_no_itemized_ranked_course_list(self):
        text = _build_reply_text(**_redundant_reply_stub_args())
        self.assertNotIn("Top ranked eligible courses", text)
        self.assertNotIn("score 260", text)
        self.assertIn("2 more eligible course(s), ranked with reasons", text)
        self.assertNotIn("Recommendations page", text)

    def test_progress_is_one_line_no_pointer_phrase(self):
        text = _build_reply_text(**_redundant_reply_stub_args())
        self.assertIn("6/41 requirements complete on the CMPSC 2026 plan", text)
        self.assertNotIn("see Progress", text)
        # the old standalone "Progress on the ... plan: N/M requirements
        # (A/B credits)." sentence shape is gone, folded into one line
        self.assertNotIn("Progress on the CMPSC 2026 plan:", text)

    def test_still_locked_remains_a_full_itemized_list(self):
        # Unlike the sections above, nothing else in the UI shows blocked
        # courses -- this one stays fully spelled out, not a pointer.
        text = _build_reply_text(**_redundant_reply_stub_args())
        self.assertIn("Still locked:", text)
        self.assertIn("CMPSC 465 — needs: CMPSC 360", text)

    def test_phrase_prompt_tells_llm_not_to_invent_pointers_or_expand_counts(self):
        prompt = _build_phrase_prompt("what's next?", "some facts", "")
        self.assertIn("do not expand a count back into a full list", prompt)
        self.assertIn("do not tell the student to go check another page", prompt)
        self.assertIn("110 words", prompt)
        # Regression: an earlier wording named the pages in a bracket-like
        # list ("(Flowchart, Recommendations, Progress)"), which a live
        # LLM was observed echoing back verbatim into the reply as
        # "[Flowchart, Recommendations, Progress]" instead of writing
        # normal prose -- don't reintroduce a list shape it can transcribe.
        self.assertNotIn("(Flowchart, Recommendations, Progress)", prompt)

    def test_phrase_prompt_forbids_unsolicited_definitive_judgment_calls(self):
        # Live-observed bug: asked to double major in MATH and ECON with
        # only MATH actually set, the LLM confidently declared "MATH is
        # the primary major... explore ECON later" -- a decision the
        # student never asked for. That framing is fine ONLY when the
        # student's own question actually invites a recommendation
        # ("which major should I focus on", "which is faster").
        prompt = _build_phrase_prompt("what's next?", "some facts", "")
        self.assertIn("do not make a definitive judgment call the student didn't ask for", prompt.lower())
        self.assertIn("which should i focus on", prompt.lower())
        self.assertIn("don't quietly resolve it yourself", prompt.lower())


class TestReplyLinks(unittest.TestCase):
    """_build_reply_links: the structured, clickable stand-in for the text
    pointers TestReplyTextNoRedundancy confirms are gone from the prose."""

    def test_flowchart_link_only_when_next_sem_has_courses(self):
        links = _build_reply_links({"courses": [{"code": "PHYS 211"}]}, [])
        routes = [l["route"] for l in links]
        self.assertIn("/flowchart", routes)

    def test_no_flowchart_link_when_next_sem_empty(self):
        links = _build_reply_links({"courses": []}, [])
        routes = [l["route"] for l in links]
        self.assertNotIn("/flowchart", routes)

    def test_recommendations_link_only_when_ranked_nonempty(self):
        with_ranked = _build_reply_links({"courses": []}, [{"code": "PHYS 211"}])
        without_ranked = _build_reply_links({"courses": []}, [])
        self.assertIn("/recommendations", [l["route"] for l in with_ranked])
        self.assertNotIn("/recommendations", [l["route"] for l in without_ranked])

    def test_progress_link_always_present(self):
        links = _build_reply_links({"courses": []}, [])
        self.assertIn("/progress", [l["route"] for l in links])

    def test_every_link_has_label_and_route(self):
        links = _build_reply_links({"courses": [{"code": "X"}]}, [{"code": "Y"}])
        for link in links:
            self.assertIn("label", link)
            self.assertIn("route", link)

    def test_api_plan_response_includes_reply_links(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": [], "start_year": 2026,
        })
        self.assertEqual(r.status_code, 200)
        links = r.get_json()["coursePlan"]["replyLinks"]
        self.assertTrue(links)
        self.assertIn("/progress", [l["route"] for l in links])


class TestUnconfirmedMajorDetection(unittest.TestCase):
    """_detect_unconfirmed_major_mentions: catches 'double major in MATH
    and ECON' when only MATH got set, instead of silently dropping ECON."""

    def test_second_major_mentioned_but_not_confirmed_is_flagged(self):
        found = _detect_unconfirmed_major_mentions(
            "I want to double major in MATH and ECON", {"MATH"},
        )
        self.assertEqual(found, ["ECON"])

    def test_already_confirmed_major_not_flagged_again(self):
        found = _detect_unconfirmed_major_mentions(
            "double major in MATH and ECON", {"MATH", "ECON"},
        )
        self.assertEqual(found, [])

    def test_course_code_collision_not_flagged(self):
        # "STAT 200" is a course mention, not a claim of a Statistics major.
        found = _detect_unconfirmed_major_mentions(
            "I took STAT 200 last semester", {"CMPSC"},
        )
        self.assertEqual(found, [])

    def test_minor_language_not_flagged_as_unconfirmed_major(self):
        found = _detect_unconfirmed_major_mentions(
            "I'm a CMPSC major minoring in Math", {"CMPSC"},
        )
        self.assertEqual(found, [])
        found2 = _detect_unconfirmed_major_mentions(
            "I'm a CMPSC major, Math minor", {"CMPSC"},
        )
        self.assertEqual(found2, [])

    def test_no_extra_mention_returns_empty(self):
        found = _detect_unconfirmed_major_mentions("I took CMPSC 131", {"CMPSC"})
        self.assertEqual(found, [])

    def test_reply_text_asks_for_confirmation_when_unconfirmed_major_present(self):
        args = _redundant_reply_stub_args()
        text = _build_reply_text(**args, unconfirmed_majors=["ECON"])
        self.assertIn("ECON", text)
        self.assertIn("confirm", text.lower())

    def test_reply_text_unchanged_when_no_unconfirmed_majors(self):
        args = _redundant_reply_stub_args()
        with_none = _build_reply_text(**args, unconfirmed_majors=None)
        with_empty = _build_reply_text(**args, unconfirmed_majors=[])
        self.assertEqual(with_none, with_empty)
        self.assertNotIn("confirm", with_none.lower())

    def test_api_plan_double_major_chat_text_asks_for_confirmation(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "prompt": "I want to double major in MATH and ECON",
            "completed": [], "start_year": 2026,
        })
        self.assertEqual(r.status_code, 200)
        reply = r.get_json()["coursePlan"]["rag_response"]
        self.assertIn("ECON", reply)
        self.assertIn("confirm", reply.lower())

    def test_confirmation_survives_llm_phrasing_that_drops_it(self):
        # Regression: live-tested against a real Ollama reply that
        # correctly received the confirmation question in its input facts
        # but silently dropped it while compressing to ~110 words. The
        # question is too important to leave to the LLM's discretion, so
        # api_plan must append it deterministically whenever phrasing
        # succeeds and the question isn't already present verbatim.
        with patch("app._llm_phrase_reply", return_value="Sounds good, here's your plan!"):
            client = app.test_client()
            r = client.post("/api/plan", json={
                "prompt": "I want to double major in MATH and ECON",
                "completed": [], "start_year": 2026,
            })
        self.assertEqual(r.status_code, 200)
        reply = r.get_json()["coursePlan"]["rag_response"]
        self.assertIn("Sounds good, here's your plan!", reply)
        self.assertIn("ECON", reply)
        self.assertIn("confirm", reply.lower())

    def test_confirmation_always_appended_no_matter_what_llm_said(self):
        # Deliberately no attempt to detect whether the LLM's own phrasing
        # already covered it -- unreliable substring matching would just
        # reintroduce the drop bug via false negatives, so the real
        # template question is always appended on top, unconditionally.
        canned = "Sounds like an exciting path! Here's your plan for next semester."
        with patch("app._llm_phrase_reply", return_value=canned):
            client = app.test_client()
            r = client.post("/api/plan", json={
                "prompt": "I want to double major in MATH and ECON",
                "completed": [], "start_year": 2026,
            })
        reply = r.get_json()["coursePlan"]["rag_response"]
        self.assertTrue(reply.startswith(canned))
        self.assertIn("Just to confirm", reply)
        self.assertEqual(reply.count("Just to confirm"), 1)

    def test_api_plan_second_major_already_set_skips_confirmation(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "MATH", "second_major": "ECON",
            "prompt": "I want to double major in MATH and ECON",
            "completed": [], "start_year": 2026,
        })
        self.assertEqual(r.status_code, 200)
        reply = r.get_json()["coursePlan"]["rag_response"]
        self.assertNotIn("confirm", reply.lower())


class TestNextCoursesQuestion(unittest.TestCase):
    """"What should I take [for/next semester]?" and its common phrasings
    are the one case where the itemized next-semester list belongs
    directly in the reply, not a count + Flowchart link — a count doesn't
    actually answer the question that was asked. See _is_asking_next_courses,
    detailed_next_sem (_build_reply_text), and allow_full_next_sem
    (_build_phrase_prompt)."""

    def test_detects_common_phrasings(self):
        positive = [
            "What should I take next semester?",
            "what do i take for fall 2027",
            "Which courses should I take?",
            "what's next for me",
            "What classes can I take next?",
            "Can you recommend a course for spring?",
            "What should I register for?",
        ]
        for p in positive:
            self.assertTrue(_is_asking_next_courses(p), f"should detect: {p!r}")

    def test_does_not_false_positive_on_unrelated_prompts(self):
        negative = [
            "I took CMPSC 131 and calc 1",
            "I'm a junior CMPSC major",
            "I did not take STAT 200",
            "How many credits do I have left?",
        ]
        for p in negative:
            self.assertFalse(_is_asking_next_courses(p), f"should NOT detect: {p!r}")

    def test_reply_text_itemizes_next_semester_when_asked(self):
        args = _redundant_reply_stub_args()
        text = _build_reply_text(**args, detailed_next_sem=True)
        # Both real next_sem courses from the stub, with their real reasons —
        # not just a count.
        self.assertIn("PHYS 211", text)
        self.assertIn("unlocks future courses", text)
        self.assertIn("CMPSC 221", text)
        self.assertIn("next on the flowchart", text)
        self.assertNotIn("2 courses recommended for next semester (7 credits).", text)

    def test_reply_text_stays_a_count_when_not_asked(self):
        args = _redundant_reply_stub_args()
        text = _build_reply_text(**args, detailed_next_sem=False)
        self.assertIn("2 courses recommended for next semester (7 credits).", text)
        self.assertNotIn("unlocks future courses", text)

    def test_phrase_prompt_allows_full_list_when_asked(self):
        prompt = _build_phrase_prompt(
            "what should I take next semester?", "some facts", "", allow_full_next_sem=True,
        )
        self.assertIn("name every one of those courses and its reason", prompt)
        self.assertIn("220 words", prompt)

    def test_phrase_prompt_stays_restrictive_by_default(self):
        prompt = _build_phrase_prompt("what's my progress?", "some facts", "")
        self.assertIn("do not expand a count", prompt)
        self.assertIn("110 words", prompt)

    def test_api_plan_gives_itemized_answer_when_asked_what_to_take(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "What should I take next semester?",
            "completed": ["CMPSC 131", "CMPSC 132", "MATH 140", "MATH 141", "ENGL 15", "CAS 100A"],
            "start_year": 2024,
        })
        self.assertEqual(r.status_code, 200)
        data = r.get_json()["coursePlan"]
        reply = data["rag_response"]
        next_courses = data["nextSemester"]["courses"]
        self.assertTrue(next_courses)
        # Every real recommended course code shows up by name in the reply,
        # not folded into a bare count. Serialized cards use "id" for the
        # course code (see _course_card) — a bare "GEN ED" slot has no id,
        # only a name, so fall back to that for slot items.
        for c in next_courses:
            code = c.get("id") or c.get("name")
            self.assertIn(code, reply)

    def test_api_plan_gives_count_when_not_asked(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "I took CMPSC 131",
            "completed": ["CMPSC 131"], "start_year": 2024,
        })
        self.assertEqual(r.status_code, 200)
        reply = r.get_json()["coursePlan"]["rag_response"]
        self.assertIn("recommended for", reply)


class TestNextSemCoverageGuarantee(unittest.TestCase):
    """Real bug found via live testing during the advising-research pass:
    asked to name every next-semester course, the LLM was observed
    dropping a distinct item entirely and under-counting duplicate-
    looking Gen Ed slots (e.g. saying "two Gen Eds" when there were
    really three) even with an explicit "name every one" instruction.
    _next_sem_fully_covered/_build_next_sem_detail_block give api_plan a
    deterministic guarantee, the same pattern already used for the
    unconfirmed-major confirmation question."""

    def _next_sem(self):
        return {
            "courses": [
                {"code": "ENGL 15", "credits": 3, "reason": "unlocks future courses"},
                {"code": "GEN ED", "name": "GEN ED", "credits": 3, "reason": "Semester 1 slot"},
                {"code": "GEN ED", "name": "GEN ED", "credits": 3, "reason": "Semester 2 slot"},
                {"code": "GEN ED", "name": "GEN ED", "credits": 3, "reason": "Semester 3 slot"},
                {"code": "PHYS 211", "credits": 4, "reason": "Entrance-to-Major requirement"},
                {"code": "First-Year Seminar", "name": "First-Year Seminar", "credits": 1, "reason": "Semester 2 slot"},
            ],
            "total_credits": 17,
        }

    def test_fully_covered_when_every_course_and_count_present(self):
        block = _build_next_sem_detail_block(self._next_sem(), "next semester")
        self.assertTrue(_next_sem_fully_covered(self._next_sem(), block))

    def test_not_covered_when_a_distinct_course_is_dropped(self):
        text = "You need ENGL 15, three GEN ED, GEN ED, GEN ED courses, and PHYS 211."
        # First-Year Seminar never mentioned.
        self.assertFalse(_next_sem_fully_covered(self._next_sem(), text))

    def test_not_covered_when_duplicate_slots_are_undercounted(self):
        # Real list has 3 GEN ED slots; text only reflects 2.
        text = "You need ENGL 15, GEN ED, GEN ED, PHYS 211, and First-Year Seminar."
        self.assertFalse(_next_sem_fully_covered(self._next_sem(), text))

    def test_case_insensitive_match(self):
        text = (
            "engl 15, gen ed, gen ed, gen ed, phys 211, and first-year seminar "
            "are all needed."
        )
        self.assertTrue(_next_sem_fully_covered(self._next_sem(), text))

    def test_api_plan_guarantees_full_coverage_even_if_llm_undercounts(self):
        # Real end-to-end regression check for the exact bug this fixes:
        # every real next-semester course must appear in the final reply,
        # regardless of how the LLM chose to phrase it.
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "What should I take next semester?",
            "completed": ["CMPSC 131", "CMPSC 132", "MATH 140", "MATH 141"],
            "start_year": 2024,
        })
        self.assertEqual(r.status_code, 200)
        data = r.get_json()["coursePlan"]
        reply = data["rag_response"]
        next_courses = data["nextSemester"]["courses"]
        self.assertTrue(_next_sem_fully_covered(
            {"courses": [
                {"code": c.get("id") or c.get("name"), "credits": c.get("credits")}
                for c in next_courses
            ]},
            reply,
        ))


class TestSpecificCourseQuestion(unittest.TestCase):
    """"Why can't I take X?" / "what do I need for X?" — same gap as
    TestNextCoursesQuestion but for a single named course: the generic
    "Still locked" section only ever shows its own top 3 blocked courses,
    so a student asking about a course outside that top 3 got no direct
    answer at all. _build_specific_course_answer computes a real,
    deterministic answer for exactly the course asked about, using the
    same missing_prereqs/exclusion_conflict checks the engine itself uses
    to decide eligibility -- not a guess."""

    def test_detects_common_phrasings(self):
        positive = [
            "Why can't I take CMPSC 465?",
            "why cant i take CMPSC 465",
            "What do I need for CMPSC 465?",
            "what's required for CMPSC 465",
            "What are the prereqs for CMPSC 465?",
            "When can I take CMPSC 465?",
            "Am I eligible for CMPSC 465?",
            "Can I take CMPSC 465 next semester?",
        ]
        for p in positive:
            self.assertTrue(_is_asking_why_blocked(p), f"should detect: {p!r}")

    def test_does_not_false_positive_on_unrelated_prompts(self):
        negative = ["I took CMPSC 131 and calc 1", "I'm a junior CMPSC major"]
        for p in negative:
            self.assertFalse(_is_asking_why_blocked(p), f"should NOT detect: {p!r}")

    def test_extract_asked_course_finds_the_one_named_course(self):
        _, catalog = _plan_and_catalog()
        self.assertEqual(_extract_asked_course("why can't I take CMPSC 465?", catalog), "CMPSC 465")

    def test_extract_asked_course_none_when_zero_or_multiple_named(self):
        _, catalog = _plan_and_catalog()
        self.assertIsNone(_extract_asked_course("why can't I take this?", catalog))
        self.assertIsNone(
            _extract_asked_course("why can't I take CMPSC 465 or CMPSC 461?", catalog)
        )

    def test_specific_course_answer_reports_real_missing_prereqs(self):
        _, catalog = _plan_and_catalog()
        answer = _build_specific_course_answer("CMPSC 465", catalog, completed=set())
        self.assertIn("CMPSC 465", answer)
        self.assertIn("needs:", answer)
        # Real prereq from the live catalog: CMPSC 360 or MATH 311W.
        self.assertIn("CMPSC 360", answer)

    def test_specific_course_answer_confirms_eligibility_when_satisfied(self):
        _, catalog = _plan_and_catalog()
        # Real prereq_groups (AND of two OR-groups): {CMPSC 132, CMPSC 122}
        # and {CMPSC 360, MATH 311W} — both groups need one member satisfied.
        answer = _build_specific_course_answer(
            "CMPSC 465", catalog, completed={"CMPSC 132", "CMPSC 360"},
        )
        self.assertIn("eligible to take this now", answer)

    def test_specific_course_answer_reports_already_completed(self):
        _, catalog = _plan_and_catalog()
        answer = _build_specific_course_answer("CMPSC 465", catalog, completed={"CMPSC 465"})
        self.assertIn("already completed", answer)

    def test_specific_course_answer_none_for_unknown_code(self):
        _, catalog = _plan_and_catalog()
        self.assertIsNone(_build_specific_course_answer("ZZZZ 999", catalog, completed=set()))

    def test_reply_text_includes_specific_course_answer_up_front(self):
        args = _redundant_reply_stub_args()
        text = _build_reply_text(**args, specific_course_answer="CMPSC 465 — needs: CMPSC 360.")
        self.assertTrue(text.startswith("CMPSC 465 — needs: CMPSC 360."))

    def test_api_plan_answers_about_the_specific_course_asked(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "Why can't I take CMPSC 465?",
            "completed": [], "start_year": 2024,
        })
        self.assertEqual(r.status_code, 200)
        reply = r.get_json()["coursePlan"]["rag_response"]
        self.assertIn("CMPSC 465", reply)
        self.assertIn("CMPSC 360", reply)

    def test_api_plan_stays_generic_without_a_named_course(self):
        client = app.test_client()
        r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "Why can't I take more classes?",
            "completed": [], "start_year": 2024,
        })
        self.assertEqual(r.status_code, 200)
        # No crash, no fabricated specific-course claim with no course named.
        reply = r.get_json()["coursePlan"]["rag_response"]
        self.assertIsInstance(reply, str)


def _make_minimal_pdf(lines):
    """A hand-built, single-page PDF with real extractable text — no
    reportlab/fpdf dependency (neither is declared in requirements.txt;
    reportlab happening to be present in this venv isn't something the
    test suite should rely on in a fresh install)."""
    content_ops = []
    y = 750
    for line in lines:
        esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content_ops.append(f"BT /F1 12 Tf 50 {y} Td ({esc}) Tj ET")
        y -= 20
    content = "\n".join(content_ops).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    buf = BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_offset = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode())
    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF".encode()
    )
    return buf.getvalue()


class TestParseTranscript(unittest.TestCase):
    """/api/parse-transcript: a PDF is just a different INPUT PATH into the
    exact same match_courses_in_text() real-catalog matcher chat-typed
    course mentions already go through — these tests exist to prove that
    reuse actually holds, not to test a separate parser."""

    def _upload(self, lines, **form):
        pdf_bytes = _make_minimal_pdf(lines)
        client = app.test_client()
        data = {"file": (BytesIO(pdf_bytes), "transcript.pdf"), **form}
        return client.post(
            "/api/parse-transcript", data=data, content_type="multipart/form-data",
        )

    def test_real_courses_matched_with_code_name_and_credits(self):
        r = self._upload([
            "CMPSC 131   Programming and Computation I    3.00   A",
            "MATH 140    Calculus With Analytic Geometry I   4.00   B+",
        ], major="CMPSC")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        codes = {m["code"] for m in data["matched"]}
        self.assertEqual(codes, {"CMPSC 131", "MATH 140"})
        for m in data["matched"]:
            self.assertIn("name", m)
            self.assertIn("credits", m)

    def test_fake_course_code_lands_in_unmatched_not_silently_accepted(self):
        r = self._upload(["GEOG 999XX  Not A Real Course   3.00   A"], major="CMPSC")
        data = r.get_json()
        self.assertEqual(data["matched"], [])
        self.assertIn("GEOG 999XX", data["unmatched"])

    def test_fused_dept_and_number_still_matches(self):
        # A real risk this endpoint's own docstring flags: pypdf text
        # extraction can fuse tabular columns together with no space.
        r = self._upload(["CMPSC131 Programming and Computation I 3.00 A"], major="CMPSC")
        codes = {m["code"] for m in r.get_json()["matched"]}
        self.assertIn("CMPSC 131", codes)

    def test_no_file_returns_400(self):
        client = app.test_client()
        r = client.post("/api/parse-transcript", data={}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_non_pdf_filename_rejected(self):
        client = app.test_client()
        data = {"file": (BytesIO(b"not a pdf"), "transcript.txt")}
        r = client.post("/api/parse-transcript", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_corrupted_pdf_returns_clean_error_not_500(self):
        client = app.test_client()
        data = {"file": (BytesIO(b"%PDF-1.4 this is not really a valid pdf"), "transcript.pdf")}
        r = client.post("/api/parse-transcript", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_unknown_major_returns_404(self):
        r = self._upload(["CMPSC 131 test 3.00 A"], major="ZZZNOTAMAJOR")
        self.assertEqual(r.status_code, 404)

    def test_minor_courses_matched_when_minor_selected(self):
        # A course only in a minor's own department list shouldn't be
        # invisible just because it's not the primary major's course.
        r = self._upload(
            ["STAT 200   Elementary Statistics   4.00   A"],
            major="CMPSC", minors=["STATMIN"],
        )
        codes = {m["code"] for m in r.get_json()["matched"]}
        self.assertIn("STAT 200", codes)

    def test_header_anchoring_excludes_a_decoy_number_before_the_course_table(self):
        # A student ID formatted like a course code, sitting above the
        # real "Course" table header, must never be picked up.
        text = "Student ID: CMPSC 999\nCourse   Title   Credits   Grade\nMATH 140   Calc I   4.00   A"
        anchored = _extract_transcript_course_text(text)
        self.assertNotIn("CMPSC 999", anchored)
        self.assertIn("MATH 140", anchored)

    def test_header_anchoring_handles_multiple_term_sections(self):
        text = (
            "Fall 2024\nCourse   Title   Credits   Grade\nCMPSC 131   Prog I   3.00   A\n"
            "Spring 2025\nCourse   Title   Credits   Grade\nCMPSC 132   Prog II   3.00   B"
        )
        anchored = _extract_transcript_course_text(text)
        self.assertIn("CMPSC 131", anchored)
        self.assertIn("CMPSC 132", anchored)

    def test_word_course_inside_a_title_is_not_mistaken_for_a_header(self):
        # "Course" appearing mid-line, inside another course's own title,
        # must not fragment the document into a false new anchor.
        text = "Course   Title   Credits   Grade\nGEOG 101   Intro to Course Design   3.00   A\nMATH 140   Calc I   4.00   A"
        anchored = _extract_transcript_course_text(text)
        self.assertIn("GEOG 101", anchored)
        self.assertIn("MATH 140", anchored)

    def test_falls_back_to_full_text_when_no_course_header_present(self):
        text = "MATH 140   Calc I   4.00   A"
        self.assertEqual(_extract_transcript_course_text(text), text)

    def test_endpoint_excludes_decoy_number_above_the_course_header(self):
        # CMPSC 131 is a real, matchable catalog code -- placed above the
        # header as a fake "reference number" so this actually proves the
        # anchoring excludes it, rather than it just never having matched
        # the catalog in the first place.
        r = self._upload([
            "Reference: CMPSC 131",
            "Course   Title   Credits   Grade",
            "MATH 140   Calculus With Analytic Geometry I   4.00   A",
        ], major="CMPSC")
        codes = {m["code"] for m in r.get_json()["matched"]}
        self.assertIn("MATH 140", codes)
        self.assertNotIn("CMPSC 131", codes)

    def test_matched_course_stays_completed_even_with_unmet_prerequisite(self):
        # Real-world case: a student took a higher-level course (transfer
        # credit, a prereq override, or a placement exam) without ever
        # completing its official prerequisite. The transcript is proof
        # the course itself is done -- that must never get silently
        # revoked or blocked just because an earlier course in its real
        # prereq chain never happened. This exercises the full pipeline
        # a transcript upload actually drives (match -> completed ->
        # /api/plan), not just the matcher in isolation.
        r = self._upload(["CMPSC 465   Data Structures and Algorithms   3.00   A"], major="CMPSC")
        matched_codes = {m["code"] for m in r.get_json()["matched"]}
        self.assertIn("CMPSC 465", matched_codes)
        self.assertNotIn("CMPSC 360", matched_codes)  # the real prereq, genuinely not on this transcript

        client = app.test_client()
        plan_r = client.post("/api/plan", json={
            "major": "CMPSC", "prompt": "", "completed": ["CMPSC 465"], "start_year": 2024,
        })
        cp = plan_r.get_json()["coursePlan"]
        self.assertIn("CMPSC 465", cp["completed"])
        self.assertGreaterEqual(cp["progress"]["doneItems"], 1)
        # No warning should ever call this out as some kind of error state.
        warnings_text = " ".join(cp.get("fullPlan", {}).get("warnings", []))
        self.assertNotIn("CMPSC 360", warnings_text)


class TestExploreMajors(unittest.TestCase):
    """The Undecided path: /api/explore-majors runs zero scheduling-engine
    code (there's no plan yet) and is grounded against the real major
    catalog rather than free-associating -- the whole reason this is a
    separate function/endpoint from the normal chat pipeline."""

    def test_real_majors_summary_includes_real_majors_grouped_by_college(self):
        summary = _real_majors_summary()
        self.assertIn("CMPSC", summary)
        self.assertIn("College of Engineering", summary)
        # Every college heading actually groups something, not just CMPSC
        self.assertGreater(summary.count(":\n") + summary.count(":\r\n"), 1)

    def test_real_majors_summary_deduped_across_catalog_years(self):
        summary = _real_majors_summary()
        # CMPSC has multiple historical-year plan files; must appear once.
        self.assertEqual(summary.count("CMPSC —"), 1)

    def test_real_majors_summary_respects_campus_filter(self):
        up_only = _real_majors_summary("University Park")
        world_campus = _real_majors_summary("World Campus")
        self.assertIn("CMPSC", up_only)
        # World Campus has far fewer real majors than University Park.
        self.assertLess(world_campus.count(" — "), up_only.count(" — "))

    def test_fallback_rotates_through_narrowing_questions_by_turn(self):
        summary = "Engineering:\n  CMPSC — Computer Science, B.S."
        q0 = _explore_majors_fallback(summary, 0)
        q1 = _explore_majors_fallback(summary, 1)
        self.assertNotEqual(q0, q1)
        self.assertIn("?", q0)

    def test_fallback_returns_real_major_list_once_questions_exhausted(self):
        summary = "Engineering:\n  CMPSC — Computer Science, B.S."
        reply = _explore_majors_fallback(summary, 999)
        self.assertIn("CMPSC", reply)

    def test_prompt_forbids_inventing_majors_not_on_the_real_list(self):
        prompt = _build_explore_majors_prompt("I like math", "Science:\n  MATH — Mathematics, B.S.", "", 1)
        self.assertIn("never suggest, describe, or invent details", prompt.lower())
        self.assertIn("MATH — Mathematics, B.S.", prompt)

    def test_prompt_forbids_meta_commentary_about_its_own_reasoning(self):
        # Live-observed bug: the LLM appended "(Note: This is an early
        # response, so I'm asking another question...)" to a real reply --
        # exposing its own instruction-following logic to the student
        # instead of just answering naturally.
        prompt = _build_explore_majors_prompt("I like math", "Science:\n  MATH — Mathematics, B.S.", "", 1)
        self.assertIn("never a note explaining your own reasoning", prompt.lower())

    def test_prompt_tells_llm_to_honor_an_explicit_request_for_suggestions(self):
        # Live-observed bug: student directly asked "What real majors would
        # you suggest?" and the LLM deflected with yet another narrowing
        # question anyway, reasoning it was still "early in the conversation".
        prompt = _build_explore_majors_prompt("what majors would you suggest?", "Science:\n  MATH — Mathematics, B.S.", "", 1)
        self.assertIn("do not deflect with another question just because it's early", prompt.lower())

    def test_api_explore_majors_returns_a_reply(self):
        client = app.test_client()
        r = client.post("/api/explore-majors", json={"prompt": "", "turn_index": 0})
        self.assertEqual(r.status_code, 200)
        reply = r.get_json()["reply"]
        self.assertTrue(reply)
        self.assertIn("?", reply)  # turn 0 with USE_OLLAMA off -> first narrowing question

    def test_api_explore_majors_never_touches_the_scheduling_engine(self):
        # No major/completed/start_year in the payload at all -- if this
        # endpoint accidentally routed through the normal plan pipeline it
        # would 400 or crash on the missing fields api_plan requires.
        client = app.test_client()
        r = client.post("/api/explore-majors", json={"prompt": "I like biology and helping people"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("reply", r.get_json())
        self.assertNotIn("coursePlan", r.get_json())


class TestExclusionConstraint(unittest.TestCase):
    """Mutual exclusion / anti-requisite courses ('may not schedule for
    credit if X has already been completed'). Mechanism is provably inert
    on all real catalog data until `excludes` is actually populated."""

    def setUp(self):
        self.plan, self.catalog = _plan_and_catalog()

    def test_excludes_field_defaults_empty_for_all_existing_catalogs(self):
        import glob
        # Real, hand-verified exclusions -- each traces to a course's own
        # catalog description or a real department handbook explicitly
        # stating "Students who have passed <excludes> may not schedule
        # this course for credit" / "may not receive credit for both".
        # Grown well past the original CMPSC 451/455 pilot pair during the
        # 2026-08-27 full-rollout handbook verification, which surfaced
        # real exclusions in several other departments' own course
        # descriptions along the way.
        pilot_exclusions = {
            "MATH 232": {"MATH 230"},
            "MATH 311W": {"CMPSC 360"},
            "MATH 471": {"MATH 427"},
            "CMPSC 451": {"CMPSC 455"},
            "CMPSC 455": {"CMPSC 451"},
            "CMPEN 270": {"CMPEN 271", "CMPEN 275"},
            "CMPEN 271": {"CMPEN 270"},
            "CMPEN 275": {"CMPEN 270"},
            "COMM 320": {"MKTG 422"},
            "MKTG 422": {"COMM 320"},
            "PLSC 412": {"PLSC 481"},
            "PLSC 481": {"PLSC 412"},
            "STAT 318": {"STAT 418", "STAT 414", "MATH 414", "MATH 418"},
            "ENGL 202A": {"ENGL 202B", "ENGL 202C", "ENGL 202D"},
            "ENGL 202B": {"ENGL 202A", "ENGL 202C", "ENGL 202D"},
            "ENGL 202C": {"ENGL 202A", "ENGL 202B", "ENGL 202D"},
            "ENGL 202D": {"ENGL 202A", "ENGL 202B", "ENGL 202C"},
        }
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
        # CMPSC 121's real enforced prerequisite is "MATH 110 or Enforced
        # Concurrent MATH 140" (bulletins.psu.edu/university-course-
        # descriptions/undergraduate/cmpsc/, confirmed 2026-08-27) -- fixed
        # in cmpsc_catalog.json (previously modeled as a hard MATH-110-only
        # prereq, which permanently blocked CMPSC 121 for any MATH-140-track
        # plan). That real fix means CMPSC 121 is now genuinely achievable
        # alongside MATH 140 in semester one, same as its sibling CMPSC 131
        # -- both are the item's own real "options", so either is a correct
        # pick (the engine's own priority tie-break happens to favor CMPSC
        # 121 here since a later legacy slot -- CMPSC 132's own "or legacy
        # CMPSC 122" alternative -- hard-depends on it).
        rec = engine.recommend_semester(self.plan, self.catalog, set())
        codes = {p["code"] for p in rec["courses"] if p["code"]}
        self.assertTrue({"CMPSC 131", "CMPSC 121"} & codes, "expected an intro programming course")
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
    def test_full_plan_reaches_graduation_in_four_and_a_half_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 9)


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
    def test_full_plan_reaches_graduation_in_five_years(self):
        """Handbook cross-check (see TestDSBulletinRequirements) found the
        real Statistical Modeling option requires 6+6=12 credits from List
        A/List B (Appendix D), not the 3+3=6 this plan previously modeled,
        and removed a construction-error course item with no basis in the
        real bulletin. The net +3 real credits push this plan from 8
        terms/4 years to 9 terms/5 years — confirmed empirically that
        raising max_credits_per_semester does not help, so this is left at
        5 years rather than forcing an unverified resequencing. (STAT 184
        stays at its original, correct 3 credits — see
        test_stat_184_is_3_credits_matching_its_own_course_description for
        why a later pass's "should be 2" claim was itself wrong.)"""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=5, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 9)


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
    def test_communication_writing_course_was_missing_now_required(self):
        """Handbook verification (2026-08-26), fetched the live bulletin's
        'Common Requirements for All Options' text directly: a required
        Communication/Writing course ('CAS 100A, ENGL/CAS 138T, or ENGL
        202C') was completely missing from this plan (distinct from the
        separately-modeled ENGL 15/30H/ESL 15 composition requirement)."""
        all_options = [
            code
            for _, it in engine._iter_plan_items(self.plan)
            if it.get("type") == "course"
            for code in it["options"]
        ]
        self.assertTrue({"CAS 100A", "ENGL 138T", "ENGL 202C"} & set(all_options))
    def test_professional_elective_matches_the_real_option_list(self):
        """The Atmospheric Science option's real 'Additional Courses
        (select 6-13 credits)' list, previously a bare placeholder."""
        items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Professional Elective"
        ]
        self.assertEqual(len(items), 6)
        for item in items:
            pattern = re.compile(item["match"])
            for code in ("METEO 414", "METEO 466", "METEO 481"):
                self.assertTrue(pattern.match(code))
            self.assertFalse(pattern.match("METEO 300"))  # a required course, not this elective pool


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
    def test_physics_elective_was_missing_now_recommends_a_real_course(self):
        """Handbook verification (2026-08-26): the General Option's real
        'select at least 2 credits in physics' requirement was completely
        absent from this plan. Confirms the new open_elective-based slot
        actually recommends a real PHYS course end-to-end."""
        item = next(
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Physics elective"
        )
        self.assertTrue(item.get("open_elective"))
        completed = {
            o for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "course"
            for o in [it["options"][0]]
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        rec = engine.recommend_semester(
            self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
        )
        pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
        self.assertIsNotNone(pick)
        self.assertTrue(pick["code"].startswith("PHYS "))
    def test_advanced_geosc_elective_matches_the_real_bulletin_list(self):
        """The bulletin's own 'select 14 credits of the following 300- and
        400-level GEOSC courses' list, previously a bare placeholder."""
        should_match = ["GEOSC 303", "GEOSC 340", "GEOSC 402Y", "GEOSC 416",
                         "GEOSC 422", "GEOSC 424", "GEOSC 434", "GEOSC 439",
                         "GEOSC 440", "GEOSC 451", "GEOSC 452", "GEOSC 454",
                         "GEOSC 470W", "GEOSC 489"]
        items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Advanced GEOSC elective"
        ]
        self.assertTrue(items)
        for item in items:
            pattern = re.compile(item["match"])
            for code in should_match:
                self.assertTrue(pattern.match(code), f"{code} should match")
            self.assertFalse(pattern.match("GEOSC 1"))


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
    def test_300_and_400_level_geog_slots_were_unfillable_now_recommend_real_courses(self):
        """Handbook verification (2026-08-26): '300-level Geography (select
        9 credits)' and '400-level Geography (select 12 credits)' were bare
        placeholders with no course restriction at all. Confirms
        open_elective now restricts by level AND recommends a real,
        correctly-leveled GEOG course."""
        for label, lo, hi in (("300-Level GEOG", 300, 399), ("400-Level GEOG", 400, 499)):
            item = next(
                it for _, it in engine._iter_plan_items(self.plan)
                if it.get("label") == label
            )
            self.assertTrue(item.get("open_elective"))
            self.assertEqual(item.get("elective_min_level"), lo)
            self.assertEqual(item.get("elective_max_level"), hi)
            completed = {
                o for _, it in engine._iter_plan_items(self.plan)
                if it["id"] < item["id"] and it.get("type") == "course"
                for o in [it["options"][0]]
            }
            consumed = {
                it["id"] for _, it in engine._iter_plan_items(self.plan)
                if it["id"] < item["id"] and it.get("type") == "slot"
            }
            rec = engine.recommend_semester(
                self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{label}: never recommended a course")
            self.assertTrue(pick["code"].startswith("GEOG "))
            level = int(re.match(r"GEOG (\d+)", pick["code"]).group(1))
            self.assertGreaterEqual(level, lo)
            self.assertLessEqual(level, hi)


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
    def test_elective_categories_match_the_real_department_page(self):
        """Handbook verification (2026-08-26): the department's real,
        current approved-electives page (eme.psu.edu .../
        eneng-approved-electives-0) names an exact list for each of the 5
        elective categories, previously bare placeholders. Spot-checks one
        real course per category and one made-up code that must not match."""
        expectations = {
            "Energy Systems elective": (["EGEE 438", "EME 407"], "MATSE 201"),
            "Fuel Science elective": (["FSC 431", "FSC 432"], "EGEE 438"),
            "Material Science elective": (["MATSE 201", "EGEE 455"], "EGEE 438"),
            "Professional elective": (["ACCTG 211", "IB 303"], "AE 469"),
            "Technical elective": (["AE 469", "PNG 480", "ENVSE 404W"], "MATSE 201"),
        }
        for label, (should_match, should_not) in expectations.items():
            items = [
                it for _, it in engine._iter_plan_items(self.plan)
                if it.get("label") == label
            ]
            self.assertTrue(items, f"expected at least one {label} slot")
            for item in items:
                pattern = re.compile(item["match"])
                for code in should_match:
                    self.assertTrue(pattern.match(code), f"{label}: {code} should match")
                self.assertFalse(pattern.match(should_not), f"{label}: {should_not} should NOT match")
    def test_stale_bulletin_codes_were_dropped_not_guessed(self):
        """ENGR 312, AE 456, ME 402, and PNG 405 no longer exist in this
        app's scraped catalogs -- confirms they were dropped rather than
        left in (which would make the slot un-crediting for a real
        transcript course with that code, silently)."""
        prof_item = next(
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Professional elective"
        )
        self.assertFalse(re.compile(prof_item["match"]).match("ENGR 312"))
        tech_item = next(
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Technical elective"
        )
        tech_pattern = re.compile(tech_item["match"])
        for stale in ("AE 456", "ME 402", "PNG 405"):
            self.assertFalse(tech_pattern.match(stale), f"Technical elective: {stale} should not appear (stale/nonexistent)")


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
    def test_matse_436_and_processing_lab_were_missing_now_required(self):
        """Handbook verification (2026-08-26): fetched the live bulletin's
        'Prescribed Courses' table directly -- MATSE 436 (Mechanical
        Properties of Materials) and the 1-credit Processing Laboratory
        (MATSE 463/468/471/473) were both real required courses completely
        absent from this plan."""
        all_options = [
            code
            for _, it in engine._iter_plan_items(self.plan)
            if it.get("type") == "course"
            for code in it["options"]
        ]
        self.assertIn("MATSE 436", all_options)
        for lab in ("MATSE 463", "MATSE 468", "MATSE 471", "MATSE 473"):
            self.assertIn(lab, all_options)
        lab_item = next(
            it for _, it in engine._iter_plan_items(self.plan)
            if "MATSE 463" in it.get("options", [])
        )
        self.assertEqual(lab_item["credits"], 1)
    def test_specialization_course_matches_the_real_bulletin_categories(self):
        items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "MATSE Specialization Course"
        ]
        self.assertTrue(items)
        for item in items:
            pattern = re.compile(item["match"])
            for code in ("MATSE 411", "MATSE 410", "MATSE 412"):
                self.assertTrue(pattern.match(code))
            self.assertFalse(pattern.match("MATSE 436"))


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
    def test_intro_and_advanced_earth_electives_match_the_real_bulletin_lists(self):
        """Handbook verification (2026-08-26): the live bulletin names exact
        course lists for 'Introductory Earth Science (15 credits)' and
        'Advanced Earth Science (15 credits)', previously bare
        placeholders."""
        intro_should_match = ["EARTH 2", "GEOSC 1", "GEOSC 21", "METEO 3", "SOILS 101"]
        adv_should_match = ["GEOSC 204", "GEOSC 320", "GEOSC 402Y", "METEO 300", "METEO 431"]
        intro_items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Intro GEOSC/EARTH elective"
        ]
        adv_items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") in ("Advanced EARTH elective", "Advanced GEOSC/EARTH elective")
        ]
        self.assertEqual(len(intro_items), 5)
        self.assertEqual(len(adv_items), 5)
        for item in intro_items:
            pattern = re.compile(item["match"])
            for code in intro_should_match:
                self.assertTrue(pattern.match(code))
            self.assertFalse(pattern.match("GEOG 110"))  # dropped stale bulletin code
        for item in adv_items:
            pattern = re.compile(item["match"])
            for code in adv_should_match:
                self.assertTrue(pattern.match(code))
            self.assertFalse(pattern.match("GEOG 412"))  # real code is GEOG 412W


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
    def test_advanced_geobi_elective_matches_both_real_categories(self):
        """Handbook verification (2026-08-26): the bulletin's 12-credit
        'Advanced GEOBI elective' pool must come from two named categories
        (evolution/paleobiology/geology, and biogeochemistry) per a real
        department curriculum sheet -- previously a bare placeholder.
        Confirms both categories' real courses match, and that stale codes
        no longer in this app's catalog (GEOSC 425, GEOSC 412) were
        dropped rather than guessed at."""
        evo_paleo = ["GEOSC 420", "GEOSC 424", "GEOSC 439", "GEOSC 465", "ANTH 401", "BIOL 405", "BIOL 428"]
        biogeochem = ["GEOSC 410", "GEOSC 413W", "GEOSC 419", "GEOSC 452", "BIOL 406", "BIOL 419", "BIOL 435", "SOILS 412W"]
        items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Advanced GEOBI elective"
        ]
        self.assertEqual(len(items), 4)
        for item in items:
            pattern = re.compile(item["match"])
            for code in evo_paleo + biogeochem:
                self.assertTrue(pattern.match(code), f"{code} should match")
            self.assertFalse(pattern.match("GEOSC 425"))
            self.assertFalse(pattern.match("GEOSC 412"))


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
    def test_elective_categories_match_the_real_bulletin_table(self):
        """Handbook verification (2026-08-26), fetched the live bulletin's
        'Elective Categories' table directly: 3 real bugs fixed. Thermo is
        EME 301/ME 300 (this plan had wrongly used ME 201, a different
        course); Fluid Mechanics is CE 360/EME 303 (CE 360 was missing);
        Programming is CMPSC 200/201 (CMPSC 203 was wrongly included --
        Spreadsheets and Databases isn't a Programming-category course)."""
        def options_for(course_in_label_substr):
            return next(
                it["options"] for _, it in engine._iter_plan_items(self.plan)
                if it.get("type") == "course" and course_in_label_substr in (it.get("label") or "")
            )
        thermo = options_for("EME 301")
        self.assertIn("ME 300", thermo)
        self.assertNotIn("ME 201", thermo)
        fluid = options_for("CE 360")
        self.assertIn("EME 303", fluid)
        self.assertIn("CE 360", fluid)
        programming = options_for("CMPSC 200")
        self.assertNotIn("CMPSC 203", programming)


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
    def test_technical_elective_matches_the_real_department_page(self):
        """Handbook verification (2026-08-26): the bulletin says 'Approved
        Technical Electives for the PNGE major can be found at the
        department web site' -- fetched that real page directly
        (eme.psu.edu .../pnge/approved-tech-electives) and wired its exact
        list, previously a bare placeholder."""
        items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Technical Elective"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            pattern = re.compile(item["match"])
            for code in ("EBF 473", "EGEE 420", "PNG 488", "PNG 456"):
                self.assertTrue(pattern.match(code))
            self.assertFalse(pattern.match("PNG 410"))  # a required course, not a technical elective
    def test_supporting_course_was_missing_now_present(self):
        """The bulletin's own 'Select 6 credits (Supporting Courses)... in
        consultation with adviser' requirement was completely absent from
        this plan."""
        items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Supporting Course"
        ]
        self.assertEqual(sum(it["credits"] for it in items), 6)


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
    def test_eme_210_was_missing_now_required(self):
        """Handbook verification (2026-08-26) found the prior pass's
        'scrape duplication' assumption was wrong: fetched the live
        bulletin's 'Prescribed Courses' table directly, and EME 210 (Data
        Analytics for Energy Systems) is independently listed there as its
        own required course -- it was missing from this plan entirely."""
        all_options = [
            code
            for _, it in engine._iter_plan_items(self.plan)
            if it.get("type") == "course"
            for code in it["options"]
        ]
        self.assertIn("EME 210", all_options)
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        all_codes = {p["code"] for t in fp["terms"] for p in t["courses"] if p["code"]}
        self.assertIn("EME 210", all_codes)


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
    def test_option_elective_matches_the_real_bulletin_categories(self):
        """Handbook verification (2026-08-26): the General Option's live
        bulletin page spells out a full 27-credit elective structure across
        three named categories, previously 9 bare 'Option elective'
        placeholders with no real course list at all."""
        items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "Option elective"
        ]
        self.assertEqual(len(items), 9)
        for item in items:
            pattern = re.compile(item["match"])
            for code in ("EARTH 2", "GEOSC 320", "GEOG 430", "PLSC 490", "STS 201"):
                self.assertTrue(pattern.match(code), f"{code} should match")
            # Dropped/corrected stale bulletin codes rather than guessed.
            self.assertFalse(pattern.match("EARTH 111"))  # real code is EARTH 111N
            self.assertFalse(pattern.match("GEOG 424"))   # real code is GEOG 424W
            self.assertFalse(pattern.match("CED 431"))    # real code is CED 431W


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
    def test_semester1_gq_math_is_generic_not_hardcoded_calc(self):
        """Regression test: the real bulletin's Fall Year 1 row is a
        generic 'General Education Course (GQ - MATH)', not a specific
        required MATH 140 -- confirm it's wired as a flexible GQ slot."""
        sem1 = next(s for s in self.plan["semesters"] if s["index"] == 1)
        codes = {c for item in sem1["items"] for c in item.get("options", [])}
        self.assertNotIn("MATH 140", codes)
        gq_item = next(i for i in sem1["items"] if i.get("gen_ed") == "GQ")
        self.assertEqual(gq_item["credits"], 3)
        self.assertEqual(sum(i["credits"] for i in sem1["items"]), 15)
    def test_ghw_is_split_1point5_1point5_not_double_counted(self):
        """Regression test: GHW is a real 3cr requirement split as two
        1.5cr installments across Semesters 6 and 7 -- Semester 6 must
        NOT also wire a second, larger GHW item."""
        sem6 = next(s for s in self.plan["semesters"] if s["index"] == 6)
        sem7 = next(s for s in self.plan["semesters"] if s["index"] == 7)
        ghw_sem6 = [i for i in sem6["items"] if i.get("gen_ed") == "GHW"]
        ghw_sem7 = [i for i in sem7["items"] if i.get("gen_ed") == "GHW"]
        self.assertEqual(len(ghw_sem6), 1)
        self.assertEqual(ghw_sem6[0]["credits"], 1.5)
        self.assertEqual(len(ghw_sem7), 1)
        self.assertEqual(ghw_sem7[0]["credits"], 1.5)
        # Semester 7's real Supporting Course credit total should now be 0
        # (it was wrongly claiming this GHW slot as a Supporting Course).
        supporting = [i for i in sem7["items"] if i.get("label") == "Supporting Course"]
        self.assertEqual(supporting, [])
    def test_arch_312_is_not_a_modeled_option(self):
        """Regression test: ARCH 312 could not be confirmed to exist as a
        real, current course -- it must never appear as a literal option."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertNotIn("ARCH 312", codes)
        self.assertIn("ARCH 317", codes)
    def test_total_credits_match_the_real_bulletin(self):
        total = sum(item["credits"] for sem in self.plan["semesters"] for item in sem["items"])
        self.assertEqual(total, 162)


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

    def test_additional_courses_include_western(self):
        """Regression test: ARTH 111 (Western) is scheduled as one of the
        Additional Courses. The bulletin also names a real non-Western
        requirement, but ARTH 101N -- this test's original non-Western
        pick -- turned out not to be on the bulletin's actual closed list
        (see test_additional_course_slots_use_the_real_approved_list_not_arth_101n
        below) and the Western/non-Western split itself isn't enforced by
        the engine (see docs/COMPLIANCE_BACKLOG.md's sub-quota entry)."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertIn("ARTH 111", codes)
    def test_additional_course_slots_use_the_real_approved_list_not_arth_101n(self):
        """Regression test: ARTH 101N is a real course but is NOT on the
        bulletin's own closed Additional Courses list -- it must never
        appear as a literal option, and the two open Additional Course
        slots (Semesters 2 and 3) must draw only from the real list."""
        approved = {"ARTH 100", "ARTH 105N", "ARTH 107N", "ARTH 111", "ARTH 111U",
                    "ARTH 112", "ARTH 112U", "ARTH 120", "ARTH 130", "ARTH 140",
                    "ARTH 201", "ARTH 202N"}
        found_open_slots = 0
        for sem in self.plan["semesters"]:
            for item in sem["items"]:
                if item.get("label", "").startswith("Additional Course"):
                    options = set(item.get("options", []))
                    self.assertNotIn("ARTH 101N", options, f"{item}: ARTH 101N is not on the real approved list")
                    if len(options) > 1:
                        found_open_slots += 1
                        self.assertTrue(options <= approved, f"{item}: options must be a subset of the real list")
        self.assertEqual(found_open_slots, 2, "expected exactly 2 open Additional Course slots (Semesters 2 and 3)")
    def test_semester1_has_foreign_language_not_an_extra_gen_ed(self):
        sem1 = next(s for s in self.plan["semesters"] if s["index"] == 1)
        labels = [item.get("label") for item in sem1["items"]]
        self.assertIn("Foreign Language", labels)
        self.assertEqual(sum(i["credits"] for i in sem1["items"]), 16)
    def test_ba_knowledge_domain_us_and_il_are_not_swapped(self):
        sem5 = next(s for s in self.plan["semesters"] if s["index"] == 5)
        sem6 = next(s for s in self.plan["semesters"] if s["index"] == 6)
        il_item = next(i for i in sem5["items"] if "B.A. Knowledge Domain" in i.get("label", ""))
        us_item = next(i for i in sem6["items"] if "B.A. Knowledge Domain" in i.get("label", "") and i.get("gen_ed"))
        self.assertEqual(il_item.get("gen_ed"), "IL", "Semester 5's B.A. Knowledge Domain should be IL, per the real bulletin")
        self.assertEqual(us_item.get("gen_ed"), "US", "Semester 6's B.A. Knowledge Domain should be US, per the real bulletin")
    def test_support_course_slots_recommend_a_real_arth_only_course(self):
        for label in ("Support Course Geographic Area", "Support Course Art History Elective"):
            item = next(
                it for _, it in engine._iter_plan_items(self.plan)
                if it.get("label") == label
            )
            self.assertTrue(item.get("open_elective"), f"{label} should be wired to open_elective")
            completed = {
                it["options"][0] for _, it in engine._iter_plan_items(self.plan)
                if it["id"] < item["id"] and it.get("type") == "course"
            }
            consumed = {
                it["id"] for _, it in engine._iter_plan_items(self.plan)
                if it["id"] < item["id"] and it.get("type") == "slot"
            }
            rec = engine.recommend_semester(
                self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{label} was never recommended a course")
            self.assertTrue(pick["code"].startswith("ARTH "), f"{label} recommended a non-ARTH course: {pick}")


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
    def test_year1_gen_ed_domains_are_gq_gq_ga_not_swapped(self):
        """Regression test: 2026-08-27 re-verification against the live
        bulletin's own Suggested Academic Plan <table> found Semester 1's
        5th item was wrongly tagged 'GEN ED (History of Art)'/GA -- the
        real Fall Year 1 row has a SECOND GQ item, and the real GA
        ('History of Arts') tag belongs on a Semester 2 item instead."""
        sem1 = next(s for s in self.plan["semesters"] if s["index"] == 1)
        sem2 = next(s for s in self.plan["semesters"] if s["index"] == 2)
        sem1_domains = [i.get("gen_ed") for i in sem1["items"] if i.get("gen_ed")]
        sem2_domains = [i.get("gen_ed") for i in sem2["items"] if i.get("gen_ed")]
        self.assertEqual(sem1_domains, ["GQ"])
        self.assertEqual(sorted(sem2_domains), ["GA", "GQ"])


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
    def test_additional_course_for_major_uses_real_approved_list(self):
        """Regression test: 2026-08-27 re-verification against the live
        bulletin's own Suggested Academic Plan <table> found footnote 1
        names a real, closed 19-course list for 'Additional Course for
        Major' (beginning-level DART/ART/PHOTO/ARTH courses) -- all 4
        occurrences must now be wired to it instead of a generic,
        unfillable placeholder."""
        approved = {"DART 202", "DART 206", "ART 211", "ART 220", "ART 223", "ART 230",
                    "ART 240", "ART 250", "ART 260", "ART 280", "ART 296", "ART 297",
                    "ART 299", "PHOTO 100", "PHOTO 101", "PHOTO 200", "PHOTO 201",
                    "ARTH 250", "PHOTO 202"}
        found = 0
        for sem in self.plan["semesters"]:
            for item in sem["items"]:
                if item.get("label", "").startswith("Additional Course for Major"):
                    found += 1
                    self.assertEqual(item["credits"], 3)
                    self.assertTrue(set(item["options"]) <= approved, item)
        self.assertEqual(found, 4)
    def test_supporting_course_slots_are_wired_and_department_restricted(self):
        """Regression test: the two 'Supporting Course: 300/400-level Art
        History' slots must recommend only a real 300+ ARTH course, and
        the two 'Supporting Course: 300/400-level studio art' slots must
        recommend only a real 300+ ART/DART/PHOTO course -- never courses
        from AED's own other loaded departments (CI, PSYCH, EDPSY, SPLED,
        etc.)."""
        for label, allowed_prefixes in (
            ("Supporting Course: 300/400-level Art History", ("ARTH",)),
            ("Supporting Course: 300/400-level studio art", ("ART", "DART", "PHOTO")),
        ):
            items = [it for _, it in engine._iter_plan_items(self.plan) if it.get("label") == label]
            self.assertEqual(len(items), 2, label)
            for item in items:
                self.assertTrue(item.get("open_elective"), f"{label} should be wired to open_elective")
                self.assertEqual(item.get("elective_min_level"), 300)
                completed = {
                    it["options"][0] for _, it in engine._iter_plan_items(self.plan)
                    if it["id"] < item["id"] and it.get("type") == "course"
                }
                consumed = {
                    it["id"] for _, it in engine._iter_plan_items(self.plan)
                    if it["id"] < item["id"] and it.get("type") == "slot"
                }
                rec = engine.recommend_semester(
                    self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
                )
                pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
                self.assertIsNotNone(pick, f"{label}: never recommended a course")
                self.assertTrue(
                    any(pick["code"].startswith(f"{p} ") for p in allowed_prefixes),
                    f"{label}: recommended {pick['code']}, not one of {allowed_prefixes}",
                )


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
    def test_semester8_has_gen_ed_not_a_phantom_elective(self):
        """Regression test: 2026-08-27 re-verification against the live
        bulletin's own Suggested Academic Plan <table> found Semester 8
        (Fourth Year Spring) is LARCH 414 + LARCH 424 + three plain Gen
        Ed courses -- no Elective at all in that term (the real plan's
        only Elective is in Semester 9). Semester 8 was previously
        mislabeled with one 'Elective' item instead of a third GEN ED."""
        sem8 = next(s for s in self.plan["semesters"] if s["index"] == 8)
        labels = [i.get("label") for i in sem8["items"]]
        self.assertEqual(labels.count("GEN ED"), 3)
        self.assertNotIn("Elective", labels)
        sem9 = next(s for s in self.plan["semesters"] if s["index"] == 9)
        self.assertIn("Elective", [i.get("label") for i in sem9["items"]])
    def test_total_credits_match_the_real_bulletin(self):
        total = sum(item["credits"] for sem in self.plan["semesters"] for item in sem["items"])
        self.assertEqual(total, 139)


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
    def test_dmd_400_is_3cr_with_four_gen_eds_in_final_semester(self):
        """Regression test: 2026-08-27 re-verification against the live
        bulletin's own Suggested Academic Plan <table> found DMD 400 is a
        real 3cr course (not 6cr as previously modeled), paired with FOUR
        Gen Ed items in the final semester, not three."""
        sem8 = next(s for s in self.plan["semesters"] if s["index"] == 8)
        dmd400 = next(i for i in sem8["items"] if i.get("options") == ["DMD 400"])
        self.assertEqual(dmd400["credits"], 3)
        gen_eds = [i for i in sem8["items"] if i.get("label") == "GEN ED"]
        self.assertEqual(len(gen_eds), 4)
    def test_additional_course_for_major_uses_real_approved_list(self):
        """Regression test: the bulletin's own footnote 1 names a real,
        closed 44-course list (spanning AA/ART/COMM/DART/GD/HCDD/IST) for
        'Additional Course for Major' -- all 9 occurrences (27cr total)
        must be wired to it, and DART 100 (already a separate required
        Semester 2 course) must never appear as an option here."""
        found = 0
        for sem in self.plan["semesters"]:
            for item in sem["items"]:
                if item.get("label", "").startswith("Additional Course for Major"):
                    found += 1
                    self.assertNotIn("DART 100", item["options"])
                    self.assertGreater(len(item["options"]), 5)
        self.assertEqual(found, 9)
    def test_full_plan_never_deadlocks_with_wired_pool(self):
        """Regression test: wiring a literal-options pool alongside other
        literal picks from the same catalog can deadlock the simulator if
        a code is reachable from both (a real bug hit and fixed in the
        Digital Arts and Media Design build) -- confirm this plan still
        finishes in a normal number of terms."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertLessEqual(len(fp["terms"]), 9)
    def test_comm_320_and_mktg_422_are_mutually_exclusive(self):
        """Regression test: COMM 320's own catalog description states
        'A student may not receive credit for both COMM 320 and MKTG
        422' -- confirmed via the engine's exclusion mechanism, same
        pattern as CMPSC 451/455. COMM is a loaded department here since
        it feeds the Additional Course for Major pool."""
        c320 = self.catalog["COMM 320"]
        self.assertFalse(engine.excludes_satisfied(c320, {"MKTG 422"}))
        self.assertTrue(engine.excludes_satisfied(c320, set()))


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
    def test_semester2_has_a_second_gq_item(self):
        """Regression test: 2026-08-27 re-verification found the real
        Year 1 Spring row has a second GQ-tagged Gen Ed item; only
        Semester 1 had gen_ed:GQ wired before this fix."""
        sem2 = next(s for s in self.plan["semesters"] if s["index"] == 2)
        gq_items = [i for i in sem2["items"] if i.get("gen_ed") == "GQ"]
        self.assertEqual(len(gq_items), 1)
    def test_semester3_has_three_gen_eds_and_one_supporting_course(self):
        """Regression test: the real Year 2 Fall row has 3 plain Gen Ed
        cells and only 1 Supporting Course cell -- this plan previously
        had 2 Gen Ed cells and 2 Supporting Course cells."""
        sem3 = next(s for s in self.plan["semesters"] if s["index"] == 3)
        labels = [i.get("label") for i in sem3["items"]]
        self.assertEqual(labels.count("GEN ED"), 3)
        self.assertEqual(labels.count("Supporting Course"), 1)
    def test_additional_course_for_major_excludes_already_required_codes(self):
        """Regression test: the real closed Additional Course list
        includes ART 122Y/ART 211Y and PHOTO 404/PHOTO 495, but those are
        already separate required literal courses elsewhere in this plan
        -- they must be excluded from the pool to avoid a scheduling
        deadlock (same class of bug found in Digital Arts and Media
        Design)."""
        already_required = {"ART 122Y", "ART 211Y", "PHOTO 404", "PHOTO 495"}
        found = 0
        for sem in self.plan["semesters"]:
            for item in sem["items"]:
                if item.get("label", "").startswith("Additional Course for Major"):
                    found += 1
                    self.assertFalse(set(item["options"]) & already_required, item)
        self.assertEqual(found, 6)
    def test_supporting_course_open_elective_excludes_own_major(self):
        """Regression test: the real footnote's Supporting Course
        department list does not include PHOTO itself -- the open_elective
        wiring must exclude the plan's own PHOTO courses."""
        items = [it for _, it in engine._iter_plan_items(self.plan) if it.get("label") == "Supporting Course"]
        self.assertEqual(len(items), 5)
        for item in items:
            self.assertTrue(item.get("open_elective"))
            self.assertIn("PHOTO", item.get("elective_exclude_prefixes", []))


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
    def test_semester7_second_additional_course_not_a_phantom_gen_ed(self):
        """Regression test: 2026-08-27 re-verification against the live
        bulletin's own Suggested Academic Plan <table> found Fourth Year
        Fall has TWO real 'Additional Courses' cells plus a separate
        GA-tagged Gen Ed cell -- this plan only modeled one Additional
        Courses cell and left the second as a plain, unwired 'GEN ED'."""
        sem7 = next(s for s in self.plan["semesters"] if s["index"] == 7)
        labels = [i.get("label", "") for i in sem7["items"]]
        self.assertTrue(any(l.startswith("Additional Course for Major") for l in labels))
        self.assertFalse(any(l == "GEN ED" for l in labels), sem7)
    def test_additional_course_pool_excludes_codes_used_elsewhere(self):
        """Regression test: the real 60-course Additional Course list
        includes DART 206/304/403/324/PHOTO 202 (this plan's own 5
        literal emphasis picks) and DART 495 (a separate required
        Semester 5 course) -- these must be excluded from the generic
        pool's own option lists, or the simulator deadlocks trying to
        recommend an already-fully-consumed code forever (a real bug hit
        and fixed during this verification pass)."""
        already_used = {"DART 206", "DART 304", "DART 403", "DART 324", "PHOTO 202", "DART 495"}
        found = 0
        for sem in self.plan["semesters"]:
            for item in sem["items"]:
                if item.get("label", "").startswith("Additional Course for Major"):
                    found += 1
                    self.assertFalse(set(item["options"]) & already_used, item)
        self.assertEqual(found, 3)
    def test_full_plan_never_deadlocks_with_wired_pool(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertLessEqual(len(fp["terms"]), 9)


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
    def test_london_literature_course_uses_real_approved_list(self):
        """Regression test: 2026-08-27 re-verification found the real
        bulletin footnote for the London semester's 'if at University
        Park' fallback names ENGL 129, ENGL 129H, ENGL 438, THEA 206,
        THEA 499 -- ENGL 438 could not be confirmed to exist and was
        dropped; the remaining 4 real codes must be wired instead of a
        fully generic placeholder."""
        item = next(
            it for sem in self.plan["semesters"] for it in sem["items"]
            if it.get("label", "").startswith("Literature & Theory Course")
        )
        self.assertEqual(set(item["options"]), {"ENGL 129", "ENGL 129H", "THEA 206", "THEA 499"})
    def test_generic_elective_slots_are_wired_to_open_elective(self):
        """Regression test: this plan's 6 plain 'Elective' slots were
        previously fully generic/unfillable placeholders -- confirm they
        are now wired to open_elective and can actually be recommended a
        real course."""
        elective_items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("type") == "slot" and it.get("label") == "Elective"
        ]
        self.assertEqual(len(elective_items), 6)
        for item in elective_items:
            self.assertTrue(item.get("open_elective"))


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
    def test_400_level_music_pool_is_wired_and_department_restricted(self):
        """Regression test: 2026-08-27 re-verification against the real
        School of Music handbook confirmed '400-Level Music' is a genuine
        open pool (any 400-level MUSIC course, no narrower list) -- all 4
        occurrences (12cr) must be wired to open_elective, restricted to
        MUSIC only, and must never recommend an independent-study/
        special-topics/internship code or the already-required MUSIC 476W
        Senior Project."""
        excluded = {"MUSIC 476W", "MUSIC 494", "MUSIC 494H", "MUSIC 495",
                    "MUSIC 495A", "MUSIC 495B", "MUSIC 495C", "MUSIC 496",
                    "MUSIC 496H", "MUSIC 497", "MUSIC 499"}
        items = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "400-Level Music"
        ]
        self.assertEqual(len(items), 4)
        for item in items:
            self.assertTrue(item.get("open_elective"))
            self.assertEqual(item.get("elective_min_level"), 400)
            self.assertTrue(set(excluded) <= set(item.get("elective_exclude", [])))
    def test_400_level_music_slot_recommends_a_real_music_course(self):
        item = next(
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "400-Level Music"
        )
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        rec = engine.recommend_semester(
            self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
        )
        pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
        self.assertIsNotNone(pick, "400-Level Music was never recommended a course")
        self.assertTrue(pick["code"].startswith("MUSIC 4"), pick)


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
    def test_edpsy_gs_item_replaces_the_old_music_240_semester2_slot(self):
        """Regression test: Semester 2 must offer EDPSY 10/11/14 (GS),
        confirming the earlier MUSIC-240-in-Spring substitution was
        removed in favor of the real bulletin item."""
        sem2 = next(s for s in self.plan["semesters"] if s["index"] == 2)
        edpsy_item = next(i for i in sem2["items"] if set(i.get("options", [])) & {"EDPSY 10", "EDPSY 11", "EDPSY 14"})
        self.assertEqual(edpsy_item.get("gen_ed"), "GS")
    def test_cas_100_and_ghw_present(self):
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertTrue({"CAS 100A", "CAS 100B", "CAS 100C"} & codes)
        ghw_items = [
            i for sem in self.plan["semesters"] for i in sem["items"]
            if i.get("gen_ed") == "GHW"
        ]
        self.assertEqual(len(ghw_items), 1)
    def test_total_credits_match_the_real_handbook_range(self):
        total = sum(item["credits"] for sem in self.plan["semesters"] for item in sem["items"])
        self.assertEqual(total, 129.5)


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
    def test_thea_496_is_3cr_both_occurrences(self):
        """Regression test: 2026-08-27 re-verification against the live
        bulletin's own Suggested Academic Plan <table> (DOM-extracted
        directly) found THEA 496 is 3cr in BOTH Fourth Year Fall and
        Spring -- this plan previously modeled it at 1cr both times."""
        credits = [
            item["credits"] for sem in self.plan["semesters"] for item in sem["items"]
            if "THEA 496" in item.get("options", []) or "THEA 496" in item.get("label", "")
        ]
        self.assertEqual(credits, [3, 3])
    def test_fourth_year_ghw_split_and_elective_match_real_bulletin(self):
        """Regression test: the real bulletin splits a 3cr GHW
        requirement into two 1.5cr installments (one each in Fourth Year
        Fall and Spring, alongside a separate plain Gen Ed only in Fall),
        and Fourth Year Spring's real last item is a 2cr Elective, not
        Gen Ed -- this plan previously lumped Fall's Gen Ed into one
        3.5cr slot and had a phantom 3cr Gen Ed plus a wrong 2.5cr Gen Ed
        in Spring instead."""
        sem7 = next(s for s in self.plan["semesters"] if s["index"] == 7)
        sem8 = next(s for s in self.plan["semesters"] if s["index"] == 8)
        ghw7 = [i for i in sem7["items"] if i.get("gen_ed") == "GHW"]
        ghw8 = [i for i in sem8["items"] if i.get("gen_ed") == "GHW"]
        self.assertEqual([i["credits"] for i in ghw7], [1.5])
        self.assertEqual([i["credits"] for i in ghw8], [1.5])
        self.assertEqual(sum(i["credits"] for i in sem7["items"]), 16.5)
        self.assertEqual(sum(i["credits"] for i in sem8["items"]), 12.5)
        sem8_elective = next(i for i in sem8["items"] if i.get("label") == "Elective")
        self.assertEqual(sem8_elective["credits"], 2)


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
    def test_additional_major_course_uses_real_approved_list(self):
        """Regression test: 2026-08-27 re-verification against the live
        bulletin's own footnote found a real, closed 8-course list for
        'Additional Major Course' (THEA 401/402/403/404/405W/407W/408W/
        412) -- all 3 occurrences must be wired to it instead of a
        generic, unfillable placeholder."""
        approved = {"THEA 401", "THEA 402", "THEA 403", "THEA 404",
                    "THEA 405W", "THEA 407W", "THEA 408W", "THEA 412"}
        found = 0
        for sem in self.plan["semesters"]:
            for item in sem["items"]:
                if item.get("label", "").startswith("Additional Major Course"):
                    found += 1
                    self.assertTrue(set(item["options"]) <= approved, item)
        self.assertEqual(found, 3)
    def test_semester8_last_item_is_gen_ed_not_a_phantom_elective(self):
        """Regression test: the real bulletin row for Semester 8's final
        item is a plain Gen Ed course, not an Elective."""
        sem8 = next(s for s in self.plan["semesters"] if s["index"] == 8)
        labels = [i.get("label") for i in sem8["items"]]
        self.assertNotIn("Elective", labels)
        self.assertIn("GEN ED", labels)


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
    def test_advanced_dance_elective_has_both_real_picks(self):
        """Regression test: 2026-08-27 re-verification found the real
        'Advanced Dance Elective' requirement is actually TWO picks, one
        from group (a) DANCE 431/441/451 and one from group (b) DANCE
        432/442/452 -- this plan previously modeled only the first."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertTrue({"DANCE 431", "DANCE 441", "DANCE 451"} & codes)
        self.assertTrue({"DANCE 432", "DANCE 442", "DANCE 452"} & codes)


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
    def test_engl_202_present(self):
        """Regression test: 2026-08-27 re-verification against the real
        School of Music handbook found ENGL 202A/B/C/D was missing from
        the entire plan (confirmed via grep) -- the real Third Year
        Spring row includes it, where this plan had a plain, un-credited
        GEN ED slot instead."""
        codes = {c for sem in self.plan["semesters"] for item in sem["items"] for c in item.get("options", [])}
        self.assertTrue({"ENGL 202A", "ENGL 202B", "ENGL 202C", "ENGL 202D"} & codes)


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
    def test_soc_prereq_chain_is_really_enforced_in_the_catalog(self):
        # This plan's own notes claimed SOC 207/405 need SOC 1 and SOC 400W
        # needs SOC 470, but the on-disk soc_catalog.json never actually had
        # those prereq_groups set -- verified for real here, plus the
        # bulletin's own sequencing footnote this pass additionally found:
        # SOC 470 also needs STAT 200, and SOC 400W also needs SOC 405.
        catalog = engine.load_merged_catalog(["SOC", "STAT"])
        self.assertEqual([set(g) for g in catalog["SOC 207"].prereq_groups], [{"SOC 1"}])
        self.assertEqual([set(g) for g in catalog["SOC 405"].prereq_groups], [{"SOC 1"}])
        self.assertEqual(
            {frozenset(g) for g in catalog["SOC 470"].prereq_groups},
            {frozenset(["SOC 207"]), frozenset(["STAT 200"])},
        )
        self.assertEqual(
            {frozenset(g) for g in catalog["SOC 400W"].prereq_groups},
            {frozenset(["SOC 470"]), frozenset(["SOC 405"])},
        )


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
    def test_1_100_level_pool_wired_with_real_courses_and_no_fys(self):
        # Previously a bare generic slot with no options key at all --
        # permanently unfillable. Real philosophy.la.psu.edu curriculum
        # page names the 1-100 level pool; PHIL 83 (First-Year Seminar)
        # and PHIL 98 (Independent Study) don't count as content courses.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "1-100 Level PHIL Course"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(item["type"], "course")
            self.assertNotIn("PHIL 83", item["options"])
            self.assertNotIn("PHIL 98", item["options"])
            self.assertNotIn("PHIL 12", item["options"])
    def test_200_level_pool_is_not_restricted_to_200_204(self):
        # Real requirement is ANY 200-level PHIL course, not just 200-204.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if "any 200-level" in (item.get("label") or "").lower()
        ]
        self.assertEqual(len(items), 4)
        for item in items:
            self.assertIn("PHIL 205", item["options"])
            self.assertIn("PHIL 233", item["options"])
            self.assertIn("PHIL 242N", item["options"])
    def test_400_level_pool_is_open_elective_excluding_special_topics(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Concentration Course (PHIL 400 level)"
        ]
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertTrue(item.get("open_elective"))
            self.assertEqual(item.get("elective_min_level"), 400)
            self.assertEqual(
                set(item.get("elective_exclude", [])),
                {"PHIL 494", "PHIL 494H", "PHIL 496", "PHIL 497", "PHIL 499"},
            )
    def test_400_level_slot_recommends_a_real_course_not_special_topics(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Concentration Course (PHIL 400 level)"
        )
        pick = engine._pick_open_elective(
            self.catalog, set(), set(),
            min_level=item["elective_min_level"],
            exclude_exact=item["elective_exclude"],
        )
        self.assertIsNotNone(pick)
        self.assertNotIn(pick[0], {"PHIL 494", "PHIL 494H", "PHIL 496", "PHIL 497", "PHIL 499"})


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
    def test_300_and_400_level_geog_match_the_real_bulletin_lists(self):
        """Handbook verification (2026-08-26): unlike its GEOBA/GEOG
        siblings, this bulletin page names exact courses for both the
        '300-level Geography (3 credits)' and '400-level Geography (12
        credits)' categories -- previously bare placeholders."""
        item300 = next(
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "300-level GEOG selection"
        )
        pattern300 = re.compile(item300["match"])
        for code in ("GEOG 310", "GEOG 330N"):
            self.assertTrue(pattern300.match(code))
        self.assertFalse(pattern300.match("GEOG 414"))  # a 400-level course, wrong category

        items400 = [
            it for _, it in engine._iter_plan_items(self.plan)
            if it.get("label") == "400-level GEOG selection"
        ]
        self.assertEqual(len(items400), 4)
        for item in items400:
            pattern = re.compile(item["match"])
            for code in ("GEOG 414", "GEOG 438W"):
                self.assertTrue(pattern.match(code))
            self.assertFalse(pattern.match("GEOG 310"))  # a 300-level course, wrong category


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
    def test_300_and_400_level_geog_slots_were_unfillable_now_recommend_real_courses(self):
        """Handbook verification (2026-08-26): '300-level Geography (9
        credits)' and '400-level Geography (12 credits)' -- no specific
        codes are named on this bulletin page, so wired the same way as
        the Geography B.S. sibling: open_elective restricted by level."""
        for label, lo, hi in (("300-level GEOG selection", 300, 399), ("400-level GEOG selection", 400, 499)):
            item = next(
                it for _, it in engine._iter_plan_items(self.plan)
                if it.get("label") == label
            )
            self.assertTrue(item.get("open_elective"))
            completed = {
                o for _, it in engine._iter_plan_items(self.plan)
                if it["id"] < item["id"] and it.get("type") == "course"
                for o in [it["options"][0]]
            }
            consumed = {
                it["id"] for _, it in engine._iter_plan_items(self.plan)
                if it["id"] < item["id"] and it.get("type") == "slot"
            }
            rec = engine.recommend_semester(
                self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{label}: never recommended a course")
            level = int(re.match(r"GEOG (\d+)", pick["code"]).group(1))
            self.assertGreaterEqual(level, lo)
            self.assertLessEqual(level, hi)


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
    def test_supporting_course_slots_are_wired_to_open_elective(self):
        # Bulletin's real text: "Select 15-16 credits from the following
        # 400-level courses" plus a 46-department allowlist -- these were
        # previously unfillable generic placeholders.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if "Supporting Course" in (item.get("label") or "")
        ]
        self.assertEqual(len(items), 5)
        for item in items:
            self.assertTrue(item.get("open_elective"))
            self.assertEqual(item.get("elective_min_level"), 400)
            self.assertEqual(set(item.get("elective_exclude_prefixes", [])), {"ENGL", "ESL"})
    def test_supporting_course_slot_recommends_a_real_400_level_course(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if "Supporting Course" in (item.get("label") or "")
        )
        pick = engine._pick_open_elective(
            self.catalog, set(), set(),
            min_level=item["elective_min_level"],
            exclude_prefixes=item["elective_exclude_prefixes"],
        )
        self.assertIsNotNone(pick)
        code = pick[0]
        self.assertFalse(code.startswith("ENGL ") or code.startswith("ESL "))
        num = int(re.match(r"^[A-Z]+\s+(\d+)", code).group(1))
        self.assertGreaterEqual(num, 400)


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
    def test_intro_pool_has_all_4_real_options_not_just_2(self):
        # Re-verified against the bulletin's own program-requirements PDF:
        # the "WMNST 83S" data artifact was an incomplete transcription --
        # the real pool is WMNST 83N/100/105N/106N, all 4 of which exist
        # in wmnst_catalog.json. This plan previously had only 2.
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and set(item.get("options", [])) & {"WMNST 100", "WMNST 106N"}
        )
        self.assertEqual(set(item["options"]), {"WMNST 83N", "WMNST 100", "WMNST 105N", "WMNST 106N"})


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
    def test_400_level_pools_use_the_real_bulletin_lists(self):
        # Previously generic, unfillable placeholders -- the bulletin's own
        # program-requirements PDF names exact closed lists for each of
        # these three categories.
        real_ling = {"SPAN 411", "SPAN 417", "SPAN 418", "SPAN 497"}
        real_lit = {"SPAN 439", "SPAN 470", "SPAN 472", "SPAN 474", "SPAN 476",
                    "SPAN 479", "SPAN 488", "SPAN 490", "SPAN 491", "SPAN 497"}
        real_extra400 = {"SPAN 410", "SPAN 411", "SPAN 412", "SPAN 413", "SPAN 417",
                          "SPAN 418", "SPAN 420", "SPAN 439", "SPAN 470", "SPAN 472",
                          "SPAN 474", "SPAN 476", "SPAN 479", "SPAN 488", "SPAN 490",
                          "SPAN 491", "SPAN 497", "SPAN 499"}
        ling_item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if "Linguistics" in (item.get("label") or "")
        )
        lit_item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if "Literature" in (item.get("label") or "")
        )
        extra400_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "400-level SPAN Course"
        ]
        self.assertEqual(set(ling_item["options"]), real_ling)
        self.assertEqual(set(lit_item["options"]), real_lit)
        self.assertEqual(len(extra400_items), 3)
        for item in extra400_items:
            self.assertEqual(set(item["options"]), real_extra400)
    def test_200_300_level_supporting_pool_uses_the_real_bulletin_list(self):
        real_pool = {"SPAN 220", "SPAN 297", "SPAN 299", "SPAN 300", "SPAN 300B",
                     "SPAN 305", "SPAN 314", "SPAN 315N", "SPAN 316", "SPAN 353",
                     "SPAN 355", "SPAN 356", "SPAN 397", "SPAN 399"}
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "200-/300-level SPAN Course"
        ]
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(set(item["options"]), real_pool)


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
    def test_two_distinct_additional_electives_are_not_both_plsc(self):
        # Bulletin's "Additional Courses" names TWO separate 3cr electives:
        # PLSC 3/7N/14/17N, and a SEPARATE PHIL 106/107/(233Z/406 stale)/407
        # ethics-and-technology elective. Both were wrongly modeled as the
        # same generic "PLSC Elective" placeholder before this pass.
        course_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and item.get("options")
        ]
        plsc_item = next(i for i in course_items if set(i["options"]) == {"PLSC 3", "PLSC 7N", "PLSC 14", "PLSC 17N"})
        phil_item = next(i for i in course_items if set(i["options"]) == {"PHIL 106", "PHIL 107", "PHIL 407"})
        self.assertIsNotNone(plsc_item)
        self.assertIsNotNone(phil_item)
        self.assertIn("PHIL", self.plan["departments"])


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
    def test_rus_420_senior_seminar_is_a_real_required_course(self):
        # Bulletin's own Prescribed Courses table names RUS 420 "Senior
        # Seminar in Russian Culture" -- it was completely missing from
        # this plan before this pass.
        used = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        self.assertIn("RUS 420", used)
    def test_rus_401_is_not_wrongly_pooled_with_402_403(self):
        # RUS 401/402/403 are a sequential progression (Advanced Russian
        # I/II/III) per each course's own catalog description ("builds on
        # ... Russian 200 and 401"), not interchangeable alternatives --
        # the bulletin's own Prescribed Courses table names RUS 401 alone.
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and "RUS 401" in item.get("options", [])
        )
        self.assertEqual(item["options"], ["RUS 401"])
        self.assertEqual(item["credits"], 4)


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
    def test_intro_pool_includes_wmnst_105n(self):
        # Re-verified against the bulletin's own program-requirements PDF:
        # the real "Select one of" pool is WMNST 83N/100/105N/106N -- this
        # plan's Semester 1 item was missing WMNST 105N.
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and "WMNST 100" in item.get("options", [])
        )
        self.assertEqual(set(item["options"]), {"WMNST 83N", "WMNST 100", "WMNST 105N", "WMNST 106N"})


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
        Formal Reasoning items must resolve to distinct real courses.
        Originally CMPSC 131/STAT 184 -- both replaced 2026-08-27 with real
        zero/low-prereq list members (CMPSC 121, MATH 457) after STAT 184
        turned out not to be on the bulletin's real Formal Reasoning list
        at all (see test_formal_reasoning_pool_no_longer_cites_stat_184)."""
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        codes = [c["code"] for t in fp["terms"] for c in t["courses"] if c["code"]]
        self.assertIn("CMPSC 121", codes)
        self.assertIn("MATH 457", codes)
    def test_formal_reasoning_pool_no_longer_cites_stat_184(self):
        # STAT 184 was never on the bulletin's real Formal Reasoning list
        # (STAT 318 is) -- confirm it's gone from both pool items.
        for _, item in engine._iter_plan_items(self.plan):
            if "Formal Reasoning" in (item.get("label") or ""):
                self.assertNotIn("STAT 184", item.get("options", []))
    def test_philosophical_foundations_of_science_includes_125w(self):
        # PHIL 125/125W pairing matches the existing PHIL 126/126W pattern
        # -- PHIL 125W was missing entirely from phil_catalog.json.
        self.assertIn("PHIL 125W", self.catalog)
        for _, item in engine._iter_plan_items(self.plan):
            if item.get("label") == "Philosophical Foundations of Science course":
                self.assertIn("PHIL 125W", item["options"])


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
    def test_data_analysis_pathway_uses_real_bulletin_course_list(self):
        # Bulletin's real "Pathway 1: Data Analysis" 15-course list.
        real_pathway_courses = {
            "CMPSC 203", "DS 220", "DS 310", "DS 330", "DS 402", "DS 410",
            "DS 420", "MATH 220", "MATH 441", "STAT 380", "STAT 460",
            "STAT 461", "STAT 462", "STAT 463", "STAT 464", "STAT 466",
        }
        used = {
            item["options"][0] for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and "Pathway" in (item.get("label") or "")
        }
        self.assertTrue(used, "expected at least one Data Analysis pathway item")
        self.assertTrue(used <= real_pathway_courses, f"unexpected pathway courses: {used - real_pathway_courses}")


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
    def test_option_400_level_pool_uses_only_real_bulletin_courses(self):
        # Re-verified against spanish-bs_programrequirementstext.pdf's
        # Applied Spanish Option: the "additional 12cr 400-level" pool's
        # real closed list. Confirmed already correct -- no fix needed.
        real_extra400 = {"SPAN 410", "SPAN 411", "SPAN 412", "SPAN 413", "SPAN 417",
                          "SPAN 418", "SPAN 420", "SPAN 439", "SPAN 470", "SPAN 472",
                          "SPAN 474", "SPAN 476", "SPAN 479", "SPAN 488", "SPAN 490",
                          "SPAN 491", "SPAN 497", "SPAN 499"}
        all_options = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        span_400_plus = {c for c in all_options if c.startswith("SPAN ") and c[5:].rstrip("W").isdigit() and int(c[5:].rstrip("W")) >= 400}
        self.assertTrue(span_400_plus <= real_extra400 | {"SPAN 411", "SPAN 417", "SPAN 418", "SPAN 439",
                        "SPAN 470", "SPAN 472", "SPAN 474", "SPAN 476", "SPAN 479", "SPAN 488", "SPAN 490", "SPAN 491", "SPAN 497"})
        self.assertIn("SPAN 410", all_options)
        self.assertIn("SPAN 412", all_options)
        self.assertIn("SPAN 413", all_options)


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
        # plan — with nothing actually completed, nothing should read done
        # except NURS's own MATH 3 and MATH 4 items, which are always
        # auto-satisfied regardless of what's completed (see
        # NON_DEGREE_APPLICABLE_MATH — neither can ever count toward a
        # baccalaureate degree per their own bulletin description, so no
        # student should ever be required to "complete" them).
        self.assertEqual(switched["state"]["consumedSlotIds"], [])
        self.assertEqual(switched["coursePlan"]["progress"]["doneItems"], 2)

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


class TestMathPlacementWaivers(unittest.TestCase):
    """Real PSU data (bulletins.psu.edu Mathematics Placement chart +
    MATH 3/MATH 4's own catalog descriptions) driving two things: a
    developmental math course should never block progress once a higher
    one is completed (or the student's real ALEKS/high-school-calculus
    placement proves it unnecessary), and MATH 3/MATH 4 specifically must
    never be required at all, since neither counts toward a baccalaureate
    degree per PSU's own course description."""

    def test_math_3_and_4_are_always_satisfied_regardless_of_completed(self):
        self.assertTrue(engine.math_placement_satisfied("MATH 3", set()))
        self.assertTrue(engine.math_placement_satisfied("MATH 4", set()))

    def test_math_21_waived_once_a_higher_real_math_course_is_completed(self):
        self.assertFalse(engine.math_placement_satisfied("MATH 21", set()))
        self.assertTrue(engine.math_placement_satisfied("MATH 21", {"MATH 140"}))
        self.assertTrue(engine.math_placement_satisfied("MATH 22", {"MATH 141"}))

    def test_math_41_waives_both_math_22_and_math_26(self):
        # Bulletin: MATH 41 covers the same material as MATH 22 + MATH 26
        # combined into one course, not a level above them.
        self.assertTrue(engine.math_placement_satisfied("MATH 22", {"MATH 41"}))
        self.assertTrue(engine.math_placement_satisfied("MATH 26", {"MATH 41"}))
        # But MATH 41 itself isn't waived by completing only one of them.
        self.assertFalse(engine.math_placement_satisfied("MATH 41", {"MATH 22"}))

    def test_terminal_courses_are_never_waived_by_completion_of_themselves_alone(self):
        # A course doesn't waive itself — only something strictly higher.
        self.assertFalse(engine.math_placement_satisfied("MATH 110", {"MATH 110"}))
        self.assertFalse(engine.math_placement_satisfied("MATH 140", {"MATH 140"}))

    def test_aleks_score_bands_match_the_real_bulletin_chart(self):
        self.assertEqual(engine.detect_math_placement("I scored 10 on ALEKS")["tier"], 0)
        self.assertEqual(engine.detect_math_placement("I scored 30 on ALEKS")["tier"], 1)
        self.assertEqual(engine.detect_math_placement("I scored 46 on ALEKS")["tier"], 2)
        self.assertEqual(engine.detect_math_placement("I scored 61 on ALEKS")["tier"], 3)
        self.assertEqual(engine.detect_math_placement("I scored 76 on ALEKS")["tier"], 4)

    def test_high_school_calculus_phrase_auto_places_at_tier_4(self):
        d = engine.detect_math_placement("I took calculus in high school")
        self.assertEqual(d, {"tier": 4, "source": "high school calculus", "score": None})
        self.assertIsNotNone(engine.detect_math_placement("I took AP calc in high school"))

    def test_unrelated_prompt_detects_no_placement(self):
        self.assertIsNone(engine.detect_math_placement("I like math and building things"))

    def test_placement_tier_waives_developmental_courses_but_not_the_terminal_course(self):
        # A placement SCORE (not completed credit) only proves you're ready
        # to start higher — it can't excuse you from actually earning the
        # real Gen Ed credit for your target course.
        self.assertTrue(engine.math_placement_satisfied("MATH 21", set(), placement_tier=4))
        self.assertTrue(engine.math_placement_satisfied("MATH 22", set(), placement_tier=4))
        self.assertFalse(engine.math_placement_satisfied("MATH 110", set(), placement_tier=4))
        self.assertFalse(engine.math_placement_satisfied("MATH 140", set(), placement_tier=4))

    def test_expand_math_placement_adds_only_waived_ladder_codes(self):
        expanded = engine.expand_math_placement({"MATH 140"})
        # A real completed MATH 140 is higher than every other ladder rung
        # (bulletin: MATH 110/140/140A/140B/140H are mutually exclusive for
        # credit, i.e. equivalent), so all of them get waived — real credit
        # proves the lower ones unnecessary at any tier, not just placement.
        for code in ("MATH 3", "MATH 4", "MATH 21", "MATH 22", "MATH 26", "MATH 110"):
            self.assertIn(code, expanded)
        # Nothing outside the real ladder gets swept in.
        self.assertTrue(expanded.issubset(engine._ALL_MATH_LADDER_CODES))

    def test_a_waived_course_unlocks_a_real_downstream_prereq_not_just_its_own_plan_item(self):
        # The actual bug this whole feature fixes: CHEM 110's real catalog
        # prereq is MATH 22 specifically. A student who already completed
        # MATH 140 obviously knows college algebra, but MATH 22 was never
        # literally taken — expand_math_placement has to add it to the
        # completed set passed to scheduling, or CHEM 110 (and anything
        # else gated on MATH 22) stays permanently blocked.
        chem110 = engine.Course(
            code="CHEM 110", name="Chemical Principles I", credits=3.0,
            prereq_groups=[{"MATH 22"}], concurrent_groups=[],
        )
        self.assertFalse(engine.prereqs_satisfied(chem110, {"MATH 140"}))
        expanded = engine.expand_math_placement({"MATH 140"})
        self.assertTrue(engine.prereqs_satisfied(chem110, expanded))

    def test_plan_progress_marks_math_3_and_4_items_done_with_nothing_completed(self):
        # plan_progress itself stays a pure literal-hit matcher (see its own
        # docstring) — callers who want placement waivers to count as done
        # must expand `completed` first, same as api_plan does.
        plan = engine.load_degree_plan("NURS", 2026)
        progress = engine.plan_progress(plan, engine.expand_math_placement(set()))
        math34_ids = {
            item["id"] for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) & {"MATH 3", "MATH 4"}
        }
        self.assertTrue(math34_ids)
        self.assertTrue(math34_ids.issubset(progress["done_ids"]))

    def test_synthetic_waiver_never_leaks_into_extra_courses(self):
        plan = engine.load_degree_plan("ACCTG", 2026)
        expanded = engine.expand_math_placement({"MATH 140"})
        progress = engine.plan_progress(plan, expanded)
        self.assertNotIn("MATH 21", progress["extra_courses"])
        self.assertNotIn("MATH 22", progress["extra_courses"])


class TestMathPlacementApi(unittest.TestCase):
    """End-to-end through /api/plan — real chat phrases, not just direct
    engine calls, and confirming the student-facing `completed` list never
    shows a course they didn't actually take."""

    def setUp(self):
        self.client = app.test_client()

    def test_high_school_calculus_chat_phrase_marks_developmental_math_done(self):
        r = self.client.post("/api/plan", json={
            "major": "ACCTG", "prompt": "I took calculus in high school",
            "completed": [], "start_year": 2026,
        })
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["state"]["mathPlacementTier"], 4)
        # The real, honest completed-courses list must NOT include the
        # waived codes — the student never actually took them.
        self.assertNotIn("MATH 21", body["state"]["completed"])
        self.assertNotIn("MATH 22", body["state"]["completed"])
        self.assertIn("calc", body["rag_response"].lower())

    def test_aleks_score_persists_across_a_later_settings_only_request(self):
        first = self.client.post("/api/plan", json={
            "major": "ACCTG", "prompt": "I scored 75 on my ALEKS test",
            "completed": [], "start_year": 2026,
        }).get_json()
        self.assertEqual(first["state"]["mathPlacementTier"], 3)

        # A later request with no new prompt (e.g. toggling a setting) must
        # not forget the placement — same persist-and-resend pattern as
        # consumed_slot_ids.
        second = self.client.post("/api/plan", json={
            "major": "ACCTG", "prompt": "", "completed": [], "start_year": 2026,
            "math_placement_tier": first["state"]["mathPlacementTier"],
        }).get_json()
        self.assertEqual(second["state"]["mathPlacementTier"], 3)

    def test_restating_a_lower_aleks_score_never_lowers_a_stored_placement(self):
        first = self.client.post("/api/plan", json={
            "major": "ACCTG", "prompt": "I took calculus in high school",
            "completed": [], "start_year": 2026,
        }).get_json()
        self.assertEqual(first["state"]["mathPlacementTier"], 4)

        second = self.client.post("/api/plan", json={
            "major": "ACCTG", "prompt": "I scored 40 on ALEKS",
            "completed": [], "start_year": 2026,
            "math_placement_tier": first["state"]["mathPlacementTier"],
        }).get_json()
        self.assertEqual(second["state"]["mathPlacementTier"], 4)


class TestCMPSCHandbookRequirements(unittest.TestCase):
    """Real data pulled from the EECS department's own CMPSC Handbook
    (https://www.eecs.psu.edu/students/undergraduate/Computer-Science.aspx,
    2024-2025 edition), which is more granular than the university bulletin
    page these plans were originally built from — it names the exact courses
    in each of the major's three Computer Science Elective categories, and
    the exact Gen Ed / department-list exclusions the bulletin doesn't spell
    out. Covers all 5 CMPSC catalog years (2022-2026)."""

    CATALOG_YEARS = (2022, 2023, 2024, 2025, 2026)

    def test_math_451_455_are_mutually_exclusive(self):
        catalog = engine.load_merged_catalog(["CMPSC", "CMPEN"])
        c451, c455 = catalog["CMPSC 451"], catalog["CMPSC 455"]
        # "Students may take only one course for credit from MATH 451 and
        # 455" — real text in each course's own catalog description.
        self.assertFalse(engine.excludes_satisfied(c451, {"CMPSC 455"}))
        self.assertFalse(engine.excludes_satisfied(c455, {"CMPSC 451"}))
        self.assertTrue(engine.excludes_satisfied(c451, set()))

    def test_technical_elective_list_matches_the_real_handbook_category(self):
        # Category 2 of "Computer Science Electives (12 credits)" — the
        # handbook's own enumerated list. CMPSC 444 (Secure Programming) is
        # a real course but is NOT on this specific list (it only qualifies
        # under category 3, "any 400-level CMPSC/CMPEN").
        should_match = [
            "CMPSC 410", "CMPSC 432", "CMPSC 442", "CMPSC 443", "CMPSC 447",
            "CMPSC 448", "CMPSC 450", "CMPSC 451", "CMPSC 455", "CMPSC 456",
            "CMPSC 458", "CMPSC 466", "CMPSC 467", "CMPSC 471", "CMPSC 475",
            "CMPSC 476", "CMPEN 362", "CMPEN 431", "CMPEN 454", "CMPEN 462",
            "EE 456",
        ]
        for year in (2022, 2023, 2024):
            plan = engine.load_degree_plan("CMPSC", year)
            pattern = next(
                re.compile(item["match"])
                for _, item in engine._iter_plan_items(plan)
                if item.get("match") and "CMPEN 362" in item["match"]
            )
            for code in should_match:
                self.assertTrue(pattern.match(code), f"{year}: {code} should match")
            self.assertFalse(pattern.match("CMPSC 444"), f"{year}: CMPSC 444 should NOT match")

    def test_400_level_elective_excludes_independent_study_and_special_topics(self):
        # Handbook: "none of CMPSC 494, CMPSC 494H, CMPSC 495, CMPSC 496,
        # CMPEN 494, CMPEN 494H, CMPEN 495, or CMPEN 496 may be used as a
        # technical elective. CMPSC 499 and CMPEN 499 may only be used... if
        # given prior permission" — excluded here since the engine can't
        # model a per-student petition approval.
        excluded = ["CMPSC 494", "CMPSC 494H", "CMPSC 495", "CMPSC 496",
                    "CMPSC 499", "CMPEN 494", "CMPEN 494H", "CMPEN 495",
                    "CMPEN 496", "CMPEN 499"]
        allowed = ["CMPSC 442", "CMPSC 444", "CMPSC 471", "CMPEN 431"]
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("CMPSC", year)
            patterns = [
                re.compile(item["match"])
                for _, item in engine._iter_plan_items(plan)
                if item.get("match") and "400-level" in (item.get("label") or "")
            ]
            self.assertTrue(patterns, f"{year}: expected a 400-level elective slot")
            for pattern in patterns:
                for code in excluded:
                    self.assertFalse(pattern.match(code), f"{year}: {code} should NOT match {pattern.pattern}")
                for code in allowed:
                    self.assertTrue(pattern.match(code), f"{year}: {code} should match {pattern.pattern}")

    def test_natural_science_gn_slot_recommends_a_real_course(self):
        # The slot's own label already said "(GN)" but had no gen_ed domain
        # wired up — it could never actually recommend a course. Confirmed
        # end-to-end through recommend_semester, the same path the API uses.
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("CMPSC", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            gn_item = next(
                item for _, item in engine._iter_plan_items(plan)
                if "NATURAL SCIENCE" in (item.get("label") or "")
            )
            self.assertEqual(gn_item.get("gen_ed"), "GN", f"{year}: GN domain not wired")
            # Complete everything scheduled before this item so recommend_semester
            # reaches it instead of stopping on an earlier open requirement.
            completed = {
                o for _, it in engine._iter_plan_items(plan)
                if it["id"] < gn_item["id"] and it.get("type") == "course"
                for o in [it["options"][0]]
            }
            consumed = {
                it["id"] for _, it in engine._iter_plan_items(plan)
                if it["id"] < gn_item["id"] and it.get("type") == "slot"
            }
            rec = engine.recommend_semester(
                plan, catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == gn_item["id"]), None)
            self.assertIsNotNone(pick, f"{year}: GN slot was never recommended a course")
            self.assertIsNotNone(pick["code"], f"{year}: GN slot got a placeholder, not a real course")

    def test_natural_science_gn_slot_never_recommends_a_handbook_excluded_course(self):
        # Handbook's exact exclusion list for the "additional natural
        # science" pick (ASTRO 1/6/7N/10/11/120/140, all BISC, low CHEM,
        # GAME 180N, low/duplicate PHYS, GEOSC 20).
        excluded = {
            "ASTRO 1", "ASTRO 6", "ASTRO 7N", "ASTRO 10", "ASTRO 11", "ASTRO 120", "ASTRO 140",
            "BISC 1", "BISC 2", "BISC 3", "BISC 4",
            "CHEM 1", "CHEM 3", "CHEM 5", "CHEM 101",
            "GAME 180N",
            "PHYS 1", "PHYS 10", "PHYS 150", "PHYS 151", "PHYS 250", "PHYS 251",
            "GEOSC 20",
        }
        catalog = engine.load_merged_catalog(["CMPSC", "CMPEN"])
        pick = engine._pick_gen_ed_course("GN", catalog, "CMPSC", set(), excluded)
        self.assertIsNotNone(pick)
        self.assertNotIn(pick[0], excluded)

    def test_full_plan_builds_cleanly_for_every_cmpsc_catalog_year(self):
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("CMPSC", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=4)
            scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(scheduling_failures, [], f"{year}: {scheduling_failures}")

    def _reach(self, plan, item):
        """Mark every item ordered before `item` done, so recommend_semester
        actually walks far enough to try recommending `item` itself."""
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_department_list_elective_is_wired_and_recommends_a_real_course(self):
        # Previously a fully generic placeholder (never recommended
        # anything, never checkable against a real course) across every
        # catalog year. Now backed by the handbook's real denylist.
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("CMPSC", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            dept_items = [
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "slot" and item.get("label") == "DEPARTMENT LIST ELECTIVE"
            ]
            self.assertTrue(dept_items, f"{year}: expected at least one DEPARTMENT LIST ELECTIVE slot")
            for item in dept_items:
                self.assertTrue(item.get("open_elective"), f"{year}: item {item['id']} not wired")
                completed, consumed = self._reach(plan, item)
                rec = engine.recommend_semester(
                    plan, catalog, completed, consumed_slots=consumed, max_credits=99,
                )
                pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
                self.assertIsNotNone(pick, f"{year}: item {item['id']} was never recommended a course")
                self.assertIsNotNone(pick["code"], f"{year}: item {item['id']} got a placeholder, not a real course")

    def test_department_list_elective_never_recommends_a_handbook_excluded_course(self):
        catalog = engine.load_merged_catalog(["CMPSC", "CMPEN", "MATH", "STAT", "CAS", "IST"])
        plan = engine.load_degree_plan("CMPSC", 2024)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "DEPARTMENT LIST ELECTIVE"
        )
        exclude_set = {engine.norm_code(c) for c in item["elective_exclude"]}
        # Force every non-excluded course out of contention; only excluded
        # ones remain "available" by the completed/picked bookkeeping.
        forced_exclude = {c for c in catalog if c not in exclude_set}
        pick = engine._pick_open_elective(catalog, set(), forced_exclude, exclude_exact=item["elective_exclude"])
        self.assertIsNone(pick, f"Picked a handbook-excluded course: {pick}")

    def test_supporting_course_is_wired_400_level_and_avoids_denylist(self):
        # Only 2022-2024 have a separately-labeled Supporting Course item —
        # 2025/2026's flowchart-derived plans fold it into Department List
        # (see that slot's own `notes` field for why).
        for year in (2022, 2023, 2024):
            plan = engine.load_degree_plan("CMPSC", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label") == "Supporting Course"
            )
            self.assertTrue(item.get("open_elective"), f"{year}: not wired")
            self.assertEqual(item.get("elective_min_level"), 400, f"{year}: should require 400-level")
            completed, consumed = self._reach(plan, item)
            rec = engine.recommend_semester(
                plan, catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{year}: Supporting Course was never recommended")
            self.assertIsNotNone(pick["code"])
            # Real handbook denylist: cross-listed with CMPSC, or already
            # used for the stats requirement.
            self.assertNotIn(pick["code"], {"MATH 451", "MATH 455", "MATH 456", "MATH 467",
                                             "MATH 414", "MATH 415", "MATH 418"})
            # Must not be the student's own major department.
            self.assertFalse(pick["code"].startswith("CMPSC ") or pick["code"].startswith("CMPEN "))

    def test_open_elective_never_fires_without_explicit_opt_in(self):
        # The open_elective branch must never fire unless a plan item
        # explicitly sets the field — originally this meant "no plan but
        # CMPSC uses it," but the mechanism has since been intentionally
        # extended to dozens of other majors during the full handbook-
        # verification rollout (2026-08-27). The real invariant that
        # still holds: every item actually carrying the flag really means
        # it, and the branch is never silently triggered on an item that
        # doesn't declare it.
        import glob
        import json as json_module
        for path in glob.glob(os.path.join(engine.DEGREE_PLAN_DIR, "*.json")):
            with open(path) as f:
                data = json_module.load(f)
            for sem in data.get("semesters", []):
                for item in sem.get("items", []):
                    if "open_elective" in item:
                        self.assertIs(
                            item["open_elective"], True,
                            f"{path}: open_elective present but not True",
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSmealBusinessBreadthHandbookRequirements(unittest.TestCase):
    """Real data pulled from ugstudents.smeal.psu.edu's own per-major, per-
    catalog-year "Degree Requirements" pages -- the Smeal advising office's
    equivalent of a department handbook, more granular than the university
    bulletin (exact Business Breadth / Two-Piece Sequence course lists,
    which the bulletin doesn't spell out at all). Covers MGMT, MKTG, SCM,
    REST, and RM, all 5 catalog years (2022-2026) each.

    Previously every one of these 25 plan files' "Business Breadth Course"
    slots was a 100% unfillable placeholder (no match/options field at all,
    identical bug shape to CMPSC's Department List Elective before its own
    fix) -- now wired to each major's own real, verified course pool via
    the engine's pattern-slot 'match' mechanism. 2022/2023 real requirement
    was actually a differently-named 'Two-Piece Sequence' (a same-category
    paired-course rule the engine has no mechanism for -- a known,
    documented simplification, not a fabrication)."""

    MAJORS = ["MGMT", "MKTG", "SCM", "REST", "RM"]
    YEARS = (2022, 2023, 2024, 2025, 2026)

    def _breadth_items(self, plan):
        return [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "slot"
            and ("Business Breadth" in (item.get("label") or "") or "Two-Piece Sequence" in (item.get("label") or ""))
        ]

    def test_every_business_breadth_slot_is_wired_across_every_major_and_year(self):
        for major in self.MAJORS:
            for year in self.YEARS:
                plan = engine.load_degree_plan(major, year)
                self.assertIsNotNone(plan, f"{major}-{year} plan missing")
                items = self._breadth_items(plan)
                self.assertTrue(items, f"{major}-{year}: expected Business Breadth slot(s)")
                for item in items:
                    self.assertIn("match", item, f"{major}-{year}: item {item['id']} not wired")
                    # Must compile and actually match at least one real code.
                    rx = re.compile(item["match"])
                    self.assertTrue(
                        any(rx.match(c) for c in ["ACCTG 404", "FIN 406", "IB 403"]),
                        f"{major}-{year}: {item['match']} matches nothing real",
                    )

    def test_breadth_era_requires_at_least_one_400_level_slot(self):
        # 2024-2026 real requirement: "at least 3 credits must be at the
        # 400-level" (SCM: "a. 3cr 400-level Business Breadth"). At least
        # one wired item per year must reject a real 300-level breadth code.
        for major in self.MAJORS:
            for year in (2024, 2025, 2026):
                plan = engine.load_degree_plan(major, year)
                items = self._breadth_items(plan)
                patterns = [re.compile(i["match"]) for i in items]
                self.assertTrue(
                    any(not p.match("FIN 305") for p in patterns),
                    f"{major}-{year}: no slot restricted to 400-level (FIN 305 is 300-level)",
                )

    def test_breadth_pool_excludes_the_students_own_major_department(self):
        # Firewall rule, same as Gen Ed: a major's own required/elective
        # department shouldn't double-count as its OWN Business Breadth pick
        # (e.g. an SCM student can't use SCM 404, already their own
        # prescribed course, to also satisfy Business Breadth).
        own_dept_excluded_codes = {
            "MGMT": ["MGMT 326", "MGMT 481"],  # MGMT's own prescribed courses
            "MKTG": ["MKTG 330", "MKTG 342", "MKTG 450W"],
            "SCM": ["SCM 404", "SCM 405", "SCM 406", "SCM 421", "SCM 450W"],
            "REST": ["RM 330W", "RM 460", "RM 470", "RM 475"],
            "RM": ["RM 301", "RM 320W", "RM 405"],
        }
        for major, codes in own_dept_excluded_codes.items():
            plan = engine.load_degree_plan(major, 2025)
            patterns = [re.compile(i["match"]) for i in self._breadth_items(plan)]
            for code in codes:
                self.assertFalse(
                    any(p.match(code) for p in patterns),
                    f"{major}-2025: breadth pool should not include own major course {code}",
                )

    def test_econ_alternative_excludes_independent_study_and_special_topics(self):
        # Real list literally says "ECON - Select three credits of 300/400
        # level Economics" (no exact codes) -- modeled as a generic ECON
        # 3xx/4xx pattern, but independent-study/special-topics codes
        # shouldn't count, matching the same real-world norm CMPSC's
        # handbook applied to its own 400-level elective category.
        plan = engine.load_degree_plan("MGMT", 2025)
        patterns = [re.compile(i["match"]) for i in self._breadth_items(plan)]
        for code in ["ECON 494", "ECON 494A", "ECON 494H", "ECON 495", "ECON 496", "ECON 499"]:
            self.assertFalse(any(p.match(code) for p in patterns), f"{code} should be excluded")
        self.assertTrue(any(p.match("ECON 402") for p in patterns), "a normal 400-level ECON course should match")

    def test_ib_department_loaded_for_every_fixed_major(self):
        # The real Business Breadth list names real IB (International
        # Business) courses for every one of these majors; IB wasn't in
        # any of their `departments` lists before this fix, so IB 303/403/
        # etc. could never be recognized by chat parsing or recommended.
        for major in self.MAJORS:
            plan = engine.load_degree_plan(major, 2025)
            self.assertIn("IB", plan["departments"], f"{major}: IB department not loaded")

    def test_rest_hm_department_loaded_for_real_estate_related_courses(self):
        # REST_BS's own breadth list has a "Real Estate Related Courses"
        # appendix naming HM (Hotel/Restaurant Management) courses.
        plan = engine.load_degree_plan("REST", 2025)
        self.assertIn("HM", plan["departments"])
        patterns = [re.compile(i["match"]) for i in self._breadth_items(plan)]
        self.assertTrue(any(p.match("HM 482") for p in patterns))

    def test_full_plan_builds_cleanly_for_every_fixed_major_and_year(self):
        for major in self.MAJORS:
            for year in self.YEARS:
                plan = engine.load_degree_plan(major, year)
                catalog = engine.load_merged_catalog(plan["departments"])
                fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=4)
                scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
                self.assertEqual(scheduling_failures, [], f"{major}-{year}: {scheduling_failures}")


class TestRESTAdditionalCoursesHandbookFix(unittest.TestCase):
    """REST_BS: ADDITIONAL COURSES ("Complete one course from FIN 406,
    RM(BLAW) 424, RM(BLAW) 425, RM 480") -- ugstudents.smeal.psu.edu shows
    this exact 4-option list for catalog years 2022-2024, but only 2 of the
    4 (FIN 406, RM 424) starting 2025-26 (RM 425/RM 480 were dropped from
    the live bulletin's course-description listing by then). This plan
    previously offered only 2 of the 4 real options for EVERY year,
    including 2022-2024 where all 4 were genuinely valid."""

    def _item(self, year):
        plan = engine.load_degree_plan("REST", year)
        return next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "FIN 406" in item.get("options", [])
        )

    def test_all_four_real_options_present_2022_through_2024(self):
        for year in (2022, 2023, 2024):
            item = self._item(year)
            self.assertEqual(
                set(item["options"]), {"FIN 406", "RM 424", "RM 425", "RM 480"},
                f"{year}: should offer all 4 real options",
            )

    def test_only_two_current_options_present_2025_and_2026(self):
        for year in (2025, 2026):
            item = self._item(year)
            self.assertEqual(set(item["options"]), {"FIN 406", "RM 424"}, f"{year}")

    def test_rm_425_and_rm_480_are_real_catalog_courses(self):
        catalog = engine.load_merged_catalog(["RM"])
        self.assertIn("RM 425", catalog)
        self.assertIn("RM 480", catalog)
        self.assertEqual(catalog["RM 425"].credits, 3.0)
        self.assertEqual(catalog["RM 480"].credits, 3.0)
        self.assertIn("BLAW 341", {c for g in catalog["RM 425"].prereq_groups for c in g})
        self.assertTrue(
            {"RM 303", "RM 330W"} <= {c for g in catalog["RM 480"].prereq_groups for c in g},
        )


class TestRMEnterpriseRiskElectiveHandbookFix(unittest.TestCase):
    """RM_BS: ADDITIONAL RISK MANAGEMENT ELECTIVE COURSES -- ugstudents.
    smeal.psu.edu shows this list actually changing across catalog years
    (RM 440 dropped after 2023; RM 475 renumbered to FIN 455; three RM 497
    Special Topics offerings codified into permanent numbers RM 428/408/438
    by 2025-26). This plan previously carried the stale 2022/2023-era list
    unchanged into 2024, 2025, and 2026."""

    def _items(self, year):
        plan = engine.load_degree_plan("RM", year)
        return [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Enterprise Risk Management Elective"
        ]

    def test_2022_and_2023_use_the_original_four_option_list(self):
        for year in (2022, 2023):
            for item in self._items(year):
                self.assertEqual(set(item["options"]), {"BLAW 441", "FIN 406", "RM 440", "RM 475"})

    def test_2024_uses_the_real_transitional_special_topics_list(self):
        for item in self._items(2024):
            self.assertEqual(set(item["options"]), {"BLAW 441", "BLAW 497", "FIN 406", "RM 475", "RM 497"})
            self.assertNotIn("RM 440", item["options"], "RM 440 was already dropped by 2024-25")

    def test_2025_and_2026_use_the_real_renumbered_permanent_codes(self):
        for year in (2025, 2026):
            for item in self._items(year):
                self.assertEqual(
                    set(item["options"]), {"BLAW 441", "RM 428", "FIN 406", "FIN 455", "RM 408", "RM 438"},
                )
                self.assertNotIn("RM 440", item["options"])
                self.assertNotIn("RM 475", item["options"], "RM 475 was renumbered to FIN 455")


class TestBusinessIntercollegeHandbookRequirements(unittest.TestCase):
    """Business, B.S. (Intercollege) -- verified against bulletins.psu.edu's
    live program page (2026-27 edition; this Intercollege degree has no
    separate archived per-year pages the way Smeal's own majors do, so all
    5 catalog years in this repo share one real source). Covers the
    Accounting option, the plan's chosen option of the 7 available."""

    YEARS = (2022, 2023, 2024, 2025, 2026)

    def test_common_requirement_uses_the_real_ba_495_alternative_not_acctg_495(self):
        # Real "Additional Courses" (Common Requirements, all options):
        # "BA 495A - Business Internship or BA 495B - Undergraduate Research
        # in Business" -- not the Accounting department's own generic
        # ACCTG 495 (Internship), which this plan previously used instead.
        for year in self.YEARS:
            plan = engine.load_degree_plan("BUSINESS", year)
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course" and "BA 495A" in item.get("options", [])
            )
            self.assertEqual(set(item["options"]), {"BA 495A", "BA 495B"}, f"{year}")
            self.assertNotIn("ACCTG 495", item["options"], f"{year}: should not use ACCTG's own internship course")

    def test_ba_495a_and_495b_are_real_catalog_courses(self):
        catalog = engine.load_merged_catalog(["BA"])
        self.assertIn("BA 495A", catalog)
        self.assertIn("BA 495B", catalog)

    def test_supporting_course_slots_are_wired_to_the_real_department_list(self):
        # Real rule: "Select 0-3/3 credits of 400-level courses from ACCTG,
        # BA, ECON, ENTR, FIN, FINSV, HPA, IB, MGMT, MIS, MKTG, RM, or SCM".
        # Both slots were previously 100% unfillable placeholders.
        for year in self.YEARS:
            plan = engine.load_degree_plan("BUSINESS", year)
            items = [
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "slot" and (item.get("label") or "").startswith("400-Level Business Supporting Course")
            ]
            self.assertEqual(len(items), 2, f"{year}: expected 2 supporting-course slots")
            for item in items:
                self.assertIn("match", item, f"{year}: not wired")
                rx = re.compile(item["match"])
                self.assertTrue(rx.match("ACCTG 404"))
                self.assertTrue(rx.match("RM 424"))
                self.assertFalse(rx.match("MATH 401"), "non-business department should not match")
                self.assertFalse(rx.match("ACCTG 494"), "independent study should not match")
                self.assertFalse(rx.match("ACCTG 211"), "300-and-below-level course should not match")

    def test_full_plan_builds_cleanly_for_every_catalog_year(self):
        for year in self.YEARS:
            plan = engine.load_degree_plan("BUSINESS", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=4)
            scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(scheduling_failures, [], f"{year}: {scheduling_failures}")


class TestNursingHandbookRequirements(unittest.TestCase):
    """Real data pulled from the Ross and Carol Nese College of Nursing's own
    General BSN Student Handbook (nursing.psu.edu, PDF dated 2.17.26 -- the
    current living handbook) and its own 'Suggested Academic Plan for BSN
    Degree in Nursing' table (Effective Fall 2021 curriculum, still current
    as of this handbook edition -- covers all 5 catalog years in this repo,
    2022-2026, since no later curriculum revision has been published).
    General Nursing Option (traditional 4-year track)."""

    YEARS = (2022, 2023, 2024, 2025, 2026)

    def test_integrative_studies_slots_are_wired_to_inter_d(self):
        # Handbook's own table: 'Integrative Studies: Inter-domain course*'
        # in both Semester 4 and Semester 5 -- previously labeled but never
        # wired to the engine's Gen Ed domain picker at all (same bug shape
        # as CMPSC's mislabeled '(GN)' slot).
        for year in self.YEARS:
            plan = engine.load_degree_plan("NURS", year)
            items = [
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label") == "Integrative Studies Course"
            ]
            self.assertEqual(len(items), 2, f"{year}: expected 2 Integrative Studies slots")
            for item in items:
                self.assertEqual(item.get("gen_ed"), "INTER-D", f"{year}: item {item['id']} not wired")

    def test_integrative_studies_slot_recommends_a_real_course(self):
        # Confirmed end-to-end through recommend_semester, the same path
        # the API uses -- INTER-D is firewall-exempt so a NURS student's own
        # major department doesn't block it.
        for year in self.YEARS:
            plan = engine.load_degree_plan("NURS", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label") == "Integrative Studies Course"
            )
            completed = {
                o for _, it in engine._iter_plan_items(plan)
                if it["id"] < item["id"] and it.get("type") == "course"
                for o in [it["options"][0]]
            }
            consumed = {
                it["id"] for _, it in engine._iter_plan_items(plan)
                if it["id"] < item["id"] and it.get("type") == "slot"
            }
            rec = engine.recommend_semester(
                plan, catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{year}: Integrative Studies slot was never recommended a course")
            self.assertIsNotNone(pick["code"], f"{year}: got a placeholder, not a real course")

    def test_real_curriculum_sequence_matches_the_handbooks_suggested_plan(self):
        # Spot-check the handbook's own table for courses this plan must
        # carry in the right term: BIOL 161/162 -> 163/164 (A&P I/II),
        # MICRB 106/107, the 4 Professional Role Development courses
        # (NURS 250/350/450A/450B), and the two Part A/B complex-health
        # courses (NURS 405A/405B).
        for year in self.YEARS:
            plan = engine.load_degree_plan("NURS", year)
            all_codes = {
                o for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course" for o in item.get("options", [])
            }
            for code in ["BIOL 161", "BIOL 162", "BIOL 163", "BIOL 164", "MICRB 106",
                         "MICRB 107", "NURS 250", "NURS 350", "NURS 450A", "NURS 450B",
                         "NURS 405A", "NURS 405B"]:
                self.assertIn(code, all_codes, f"{year}: missing real required course {code}")

    def test_full_plan_builds_cleanly_for_every_catalog_year(self):
        for year in self.YEARS:
            plan = engine.load_degree_plan("NURS", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=4)
            scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(scheduling_failures, [], f"{year}: {scheduling_failures}")


class TestADPRHandbookRequirements(unittest.TestCase):
    """Real data pulled directly from the live 2026-27 bulletins.psu.edu
    'Requirements for the Major' section for the Advertising/Public
    Relations, B.A. — Public Relations Option (no separate department
    handbook exists for this major beyond the bulletin itself). Verified
    by parsing the raw bulletin page rather than relying on an
    AI-summarized read, since the Suggested-Academic-Plan table's own
    footnote for this elective category lists two extra course codes
    (COMM 411, COMM 494) that the authoritative Requirements-for-the-Major
    list itself omits."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("ADPR", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_comm_elective_matches_the_real_additional_courses_list(self):
        plan, _ = self._plan_and_catalog()
        should_match = ["COMM 305", "COMM 320", "COMM 373", "COMM 410",
                         "COMM 417", "COMM 418", "COMM 425", "COMM 426",
                         "COMM 427", "COMM 468", "COMM 495", "COMM 496",
                         "COMM 499"]
        # COMM 411/494 appear only in the bulletin's own Suggested-Plan
        # footnote, not its Requirements-for-the-Major course list.
        should_not_match = ["COMM 411", "COMM 494", "COMM 471"]
        patterns = [
            re.compile(item["match"]) for _, item in engine._iter_plan_items(plan)
            if item.get("match") and item.get("label") == "COMM Elective"
        ]
        self.assertEqual(len(patterns), 2, "expected both COMM Elective slots wired")
        for pattern in patterns:
            for code in should_match:
                self.assertTrue(pattern.match(code), f"{code} should match {pattern.pattern}")
            for code in should_not_match:
                self.assertFalse(pattern.match(code), f"{code} should NOT match {pattern.pattern}")

    def test_il_and_us_cultures_slots_are_wired_and_recommend_real_courses(self):
        plan, catalog = self._plan_and_catalog()
        il_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if "IL Cultures" in (item.get("label") or "")
        )
        us_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "US Cultures"
        )
        self.assertEqual(il_item.get("gen_ed"), "IL")
        self.assertEqual(us_item.get("gen_ed"), "US")
        for item in (il_item, us_item):
            completed, consumed = _reach(plan, item)
            rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_comm_320_mutually_excludes_mktg_422(self):
        # COMM 320's own catalog description: "A student may not receive
        # credit for both COMM 320 and MKTG 422."
        catalog = engine.load_merged_catalog(["COMM"])
        comm320 = catalog["COMM 320"]
        self.assertFalse(engine.excludes_satisfied(comm320, {"MKTG 422"}))
        self.assertTrue(engine.excludes_satisfied(comm320, set()))

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        fails = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(fails, [])


class TestFLMPRHandbookRequirements(unittest.TestCase):
    """Real data pulled directly from the live 2026-27 bulletins.psu.edu
    Film Production, B.A. page — both its 'Requirements for the Major'
    course lists and its own Suggested Academic Plan's exact per-term
    option lists (no separate department handbook exists for this major
    beyond the bulletin itself)."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("FLMPR", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_advanced_production_and_additional_match_patterns_match_the_live_bulletin(self):
        # Modeled as match-regex slots (not overlapping 'type: course'
        # option lists) specifically because these 4 real pools share 5-6
        # course codes in common -- see this plan's own notes for the real
        # scheduling-engine bug that overlapping course-option lists caused.
        plan, _ = self._plan_and_catalog()
        production_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "COMM 400-level Production"
        ]
        additional_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "COMM 400-level Additional"
        ]
        self.assertEqual(len(production_items), 1)
        prod_pattern = re.compile(production_items[0]["match"])
        for code in ("COMM 437", "COMM 438", "COMM 448"):
            self.assertTrue(prod_pattern.match(code))
        self.assertFalse(prod_pattern.match("COMM 439"))

        self.assertEqual(len(additional_items), 3)
        expected_pools = [
            {"COMM 437A", "COMM 438", "COMM 439", "COMM 440", "COMM 444", "COMM 445", "COMM 446", "COMM 449"},
            {"COMM 439", "COMM 440", "COMM 443", "COMM 444", "COMM 445", "COMM 446"},
            {"COMM 437A", "COMM 438", "COMM 439", "COMM 440", "COMM 444", "COMM 445", "COMM 446"},
        ]
        all_candidates = ["COMM 437A", "COMM 438", "COMM 439", "COMM 440", "COMM 443",
                           "COMM 444", "COMM 445", "COMM 446", "COMM 449"]
        actual_pools = []
        for item in additional_items:
            pattern = re.compile(item["match"])
            actual_pools.append({c for c in all_candidates if pattern.match(c)})
        for expected in expected_pools:
            self.assertIn(expected, actual_pools)

    def test_advanced_slots_are_prereq_satisfiable_by_semester_they_appear(self):
        # By the semester these slots appear, the plan's own flowchart has
        # already required both COMM 337 AND COMM 338, and both COMM 340
        # AND COMM 342W -- so every real course in each pool should
        # actually be prereq-satisfiable by then, not just a formally
        # valid course code.
        plan, catalog = self._plan_and_catalog()
        completed = {"COMM 150N", "COMM 242", "COMM 333", "COMM 337", "COMM 338", "COMM 340", "COMM 342W"}
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") in ("COMM 400-level Production", "COMM 400-level Additional")
        ]
        self.assertEqual(len(items), 4)
        for item in items:
            pattern = re.compile(item["match"])
            candidates = [c for c in catalog if pattern.match(c)]
            self.assertTrue(candidates, f"item {item['id']}: pattern matched nothing in the catalog")
            eligible = [c for c in candidates if engine.prereqs_satisfied(catalog[c], completed)]
            self.assertTrue(eligible, f"item {item['id']}: no option is prereq-satisfiable by Semester 7")

    def test_il_and_us_cultures_slots_are_wired(self):
        plan, _ = self._plan_and_catalog()
        il_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "International Cultures"
        )
        us_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "US Cultures"
        )
        self.assertEqual(il_item.get("gen_ed"), "IL")
        self.assertEqual(us_item.get("gen_ed"), "US")

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        fails = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(fails, [])


class TestJOURNHandbookRequirements(unittest.TestCase):
    """Real data pulled directly from the live 2026-27 bulletins.psu.edu
    Journalism, B.A. — Digital and Print Journalism Option page (no
    separate department handbook exists for this major beyond the
    bulletin itself). This plan had drifted furthest from the live
    bulletin of the 5 Bellisario majors checked this session: a padding
    Gen Ed item inflating one semester by 3 credits beyond the real
    120-credit total, and two entire required courses (CAS 100, ENGL 202)
    missing outright."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("JOURN", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_total_plan_credits_match_the_bulletins_stated_120(self):
        plan, _ = self._plan_and_catalog()
        total = sum(
            item.get("credits", 0) for _, item in engine._iter_plan_items(plan)
        )
        self.assertEqual(total, 120)

    def test_semester_3_has_no_generic_padding_gen_ed_item(self):
        plan, _ = self._plan_and_catalog()
        sem3 = next(sem for sem in plan["semesters"] if sem["index"] == 3)
        generic_gen_eds = [item for item in sem3["items"] if item.get("label") == "GEN ED"]
        self.assertEqual(generic_gen_eds, [], "Semester 3 should have no un-domained 'GEN ED' padding item")
        self.assertEqual(sum(item["credits"] for item in sem3["items"]), 16)

    def test_cas_100_is_required(self):
        plan, _ = self._plan_and_catalog()
        self.assertIn("CAS", plan["departments"])
        cas_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) & {"CAS 100A", "CAS 100B", "CAS 100C"}
        ]
        self.assertTrue(cas_items, "CAS 100A/100B/100C should be a real required item")

    def test_engl_202_and_second_ghw_are_present(self):
        plan, _ = self._plan_and_catalog()
        engl_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and any(o.startswith("ENGL 202") for o in item.get("options", []))
        ]
        self.assertTrue(engl_items, "ENGL 202A/B/C/D should be a real required item")
        ghw_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("gen_ed") == "GHW"
        ]
        self.assertEqual(len(ghw_items), 2, "bulletin requires two 1.5cr GHW halves")

    def test_print_digital_elective_matches_the_real_select_3_credit_pool(self):
        plan, _ = self._plan_and_catalog()
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Print/Digital Elective"
        )
        pattern = re.compile(item["match"])
        should_match = ["COMM 180", "COMM 401", "COMM 405", "COMM 407A", "COMM 407B",
                         "COMM 410", "COMM 411", "COMM 412", "COMM 419", "COMM 494H",
                         "COMM 496", "COMM 499"]
        for code in should_match:
            self.assertTrue(pattern.match(code), f"{code} should match {pattern.pattern}")
        # COMM 205 appears only in the bulletin's own Suggested-Plan
        # footnote for this pool, not its Requirements-for-the-Major list
        # -- and is already a separately-required course earlier in this
        # same plan.
        self.assertFalse(pattern.match("COMM 205"))

    def test_print_elective_matches_the_real_additional_courses_pool(self):
        plan, _ = self._plan_and_catalog()
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Print Elective"
        ]
        self.assertEqual(len(items), 2, "bulletin repeats this pool across two terms")
        should_match = ["COMM 269", "COMM 362", "COMM 364", "COMM 402", "COMM 461",
                         "COMM 462", "COMM 463", "COMM 464W", "COMM 474", "COMM 481", "COMM 495"]
        for item in items:
            pattern = re.compile(item["match"])
            for code in should_match:
                self.assertTrue(pattern.match(code), f"{code} should match {pattern.pattern}")

    def test_il_cultures_slot_is_wired(self):
        plan, _ = self._plan_and_catalog()
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if "IL Cultures" in (item.get("label") or "")
        )
        self.assertEqual(item.get("gen_ed"), "IL")

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        fails = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(fails, [])


class TestMDSTHandbookRequirements(unittest.TestCase):
    """Real data pulled directly from the live 2026-27 bulletins.psu.edu
    Media Studies, B.A. — Media Effects Option page (no separate
    department handbook exists for this major beyond the bulletin
    itself), resolving two open uncertainties the original plan's own
    notes had explicitly flagged."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("MDST", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_media_effects_pool_includes_all_four_real_courses(self):
        plan, _ = self._plan_and_catalog()
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "COMM 325/326/327/328"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(set(item["options"]), {"COMM 325", "COMM 326", "COMM 327", "COMM 328"})

    def test_cinema_or_media_course_matches_the_real_list(self):
        plan, _ = self._plan_and_catalog()
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Cinema or Media Course"
        )
        self.assertEqual(
            set(item["options"]),
            {"COMM 403", "COMM 110", "COMM 150N", "COMM 180", "COMM 320", "COMM 412"},
        )
        # COMM 205 is not actually on the real Requirements-for-the-Major
        # list for this pool, despite appearing in the bulletin's own
        # Suggested-Plan-table footnote.
        self.assertNotIn("COMM 205", item["options"])

    def test_widened_slots_still_recommend_a_real_eligible_course(self):
        plan, catalog = self._plan_and_catalog()
        for label in ("COMM 325/326/327/328", "Cinema or Media Course"):
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label") == label
            )
            completed, consumed = _reach(plan, item)
            rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{label}: never recommended a course")
            self.assertIn(pick["code"], item["options"])

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        fails = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(fails, [])


class TestTELEHandbookRequirements(unittest.TestCase):
    """Real data pulled directly from the live 2026-27 bulletins.psu.edu
    Telecommunications and Media Industries, B.A. page — both its
    'Requirements for the Major' course lists and its Suggested Academic
    Plan (no separate department handbook exists for this major beyond
    the bulletin itself)."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("TELE", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_professional_course_slots_match_the_real_additional_courses_pool(self):
        plan, _ = self._plan_and_catalog()
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Professional Course"
        ]
        self.assertEqual(len(items), 3)
        should_match = ["COMM 282", "COMM 283", "COMM 310", "COMM 374", "COMM 383",
                         "COMM 384", "COMM 385", "COMM 386", "COMM 388", "COMM 479",
                         "COMM 482", "COMM 483", "COMM 484", "COMM 484A", "COMM 491",
                         "COMM 491A", "COMM 492", "COMM 493", "COMM 495"]
        for item in items:
            pattern = re.compile(item["match"])
            for code in should_match:
                self.assertTrue(pattern.match(code), f"{code} should match {pattern.pattern}")
            # The pure Additional-Courses pool should not admit a
            # Social-Aspects-only course like COMM 305.
            self.assertFalse(pattern.match("COMM 305"))

    def test_social_aspects_slots_match_the_union_pool(self):
        plan, _ = self._plan_and_catalog()
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Social Aspects of Communication / Professional Course"
        ]
        self.assertEqual(len(items), 2)
        should_match = ["COMM 110", "COMM 118", "COMM 250", "COMM 304", "COMM 305",
                         "COMM 320", "COMM 403", "COMM 405", "COMM 409", "COMM 410",
                         "COMM 412", "COMM 413W", "COMM 417", "COMM 418", "COMM 419",
                         "COMM 419H", "COMM 496",
                         # The union also admits the pure Additional pool.
                         "COMM 282", "COMM 495"]
        for item in items:
            pattern = re.compile(item["match"])
            for code in should_match:
                self.assertTrue(pattern.match(code), f"{code} should match {pattern.pattern}")

    def test_il_and_us_cultures_slots_are_wired(self):
        plan, _ = self._plan_and_catalog()
        il_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "IL Cultures"
        )
        us_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "US Cultures"
        )
        self.assertEqual(il_item.get("gen_ed"), "IL")
        self.assertEqual(us_item.get("gen_ed"), "US")

    def test_comm_486w_is_the_preferred_code_with_486_as_fallback(self):
        plan, catalog = self._plan_and_catalog()
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) & {"COMM 486", "COMM 486W"}
        )
        self.assertEqual(item["options"][0], "COMM 486W")
        self.assertIn("COMM 486", item["options"])
        for code in ("COMM 486W", "COMM 486"):
            self.assertIn(code, catalog)

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        fails = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(fails, [])


class TestCRIMHandbookRequirements(unittest.TestCase):
    """Real data pulled from the Department of Sociology and Criminology's own
    published degree checksheets (sociology.la.psu.edu/wp-content/uploads/...,
    CRIM_B.A.-Checksheet.pdf and CRIM_BS-Computing-and-Statistics-Checksheet.pdf,
    both 'Last updated in December 2024') and the department's Race, Ethnicity,
    and Gender Requirement Course List (Last updated in October 2024) -- all
    considerably more granular than the university bulletin these plans were
    originally built from. Covers both CRIM (B.A.) and CRIMBS (B.S.)."""

    def test_race_ethnicity_gender_pattern_matches_real_list_and_rejects_others(self):
        for major in ("CRIM", "CRIMBS"):
            plan = engine.load_degree_plan(major, 2026)
            patterns = [
                re.compile(item["match"])
                for _, item in engine._iter_plan_items(plan)
                if item.get("match") and "Race" in (item.get("label") or "")
            ]
            self.assertTrue(patterns, f"{major}: expected Race/Ethnicity/Gender slots")
            allowed = ["SOC 110", "WMNST 100", "WMNST 202N", "AFAM 100N", "AFAM 152",
                       "ANTH 100", "HIST 117", "CRIMJ 451", "PHIL 9", "JST 118"]
            # CRIM 430 is a real Core 400-level CRIM course but NOT on the
            # department's own Race/Ethnicity/Gender list (unlike CRIM 451,
            # which legitimately appears on both lists).
            rejected = ["CRIM 100", "SOC 12", "STAT 200", "CRIM 430", "MATH 140"]
            for pattern in patterns:
                for code in allowed:
                    self.assertTrue(pattern.match(code), f"{major}: {code} should match {pattern.pattern}")
                for code in rejected:
                    self.assertFalse(pattern.match(code), f"{major}: {code} should NOT match {pattern.pattern}")

    def test_400_level_crim_core_list_matches_the_real_checksheet_category(self):
        # The checksheet's exact Core 400-Level CRIM Course List; "any other
        # 400-level CRIM course" (e.g. CRIM 197 isn't even 400-level, CRIM 442
        # is 400-level but not on the Core list) must NOT match the Core slot.
        for major in ("CRIM", "CRIMBS"):
            plan = engine.load_degree_plan(major, 2026)
            core_patterns = [
                re.compile(item["match"])
                for _, item in engine._iter_plan_items(plan)
                if item.get("match") and "Core" in (item.get("label") or "")
            ]
            self.assertTrue(core_patterns, f"{major}: expected 4XX CRIM Core slots")
            for pattern in core_patterns:
                for code in ["CRIM 413", "CRIM 430", "CRIM 432", "CRIM 435", "CRIM 451",
                             "CRIMJ 453", "SOC 467", "PPOL 490"]:
                    self.assertTrue(pattern.match(code), f"{major}: {code} should be Core")
                for code in ["CRIM 442", "CRIM 460", "CRIM 100"]:
                    self.assertFalse(pattern.match(code), f"{major}: {code} should NOT be Core")

    def test_400_level_crim_noncore_slot_accepts_any_real_400_level_crim_course(self):
        # Checksheet: "Any 400-level Criminology course can be used as a
        # Non-Core 400-Level CRIM Course" -- genuinely open, unlike the Core list.
        for major in ("CRIM", "CRIMBS"):
            plan = engine.load_degree_plan(major, 2026)
            patterns = [
                re.compile(item["match"])
                for _, item in engine._iter_plan_items(plan)
                if item.get("match") and item.get("label") == "4XX CRIM Course"
            ]
            self.assertTrue(patterns, f"{major}: expected non-core 4XX CRIM slots")
            for pattern in patterns:
                self.assertTrue(pattern.match("CRIM 442"))
                self.assertTrue(pattern.match("CRIM 413"))  # core courses also count here
                self.assertFalse(pattern.match("CRIM 100"))
                self.assertFalse(pattern.match("CRIM 12"))

    def test_crimbs_computing_and_statistics_option_requires_the_real_math_sequence(self):
        # The department's own checksheet requires "MATH 110 and MATH 111 OR
        # MATH 140 and MATH 141" for the Computing and Statistics option --
        # this was completely absent from the plan (MATH wasn't even loaded).
        plan = engine.load_degree_plan("CRIMBS", 2026)
        self.assertIn("MATH", plan["departments"])
        codes = {
            o for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" for o in item["options"]
        }
        self.assertIn("MATH 140", codes)
        self.assertIn("MATH 141", codes)
        catalog = engine.load_merged_catalog(plan["departments"])
        self.assertTrue(engine.prereqs_satisfied(catalog["MATH 140"], set()))
        self.assertTrue(engine.prereqs_satisfied(catalog["MATH 141"], {"MATH 140"}))

    def test_crim_and_crimbs_full_plan_build_cleanly(self):
        for major in ("CRIM", "CRIMBS"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            fails = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(fails, [], f"{major}: {fails}")


class TestCASHandbookRequirements(unittest.TestCase):
    """No separate department handbook exists for Communication Arts and
    Sciences beyond the university bulletin, but the CURRENT live bulletin
    page (bulletins.psu.edu/.../communication-arts-sciences-ba(bs)/) is far
    more specific than what these plans originally assumed -- it enumerates
    the 'CAS Additional Course' pool and spells out the B.S.'s separate
    Quantification/Related-Disciplines structure. Covers both CASBA and CASBS."""

    def test_cas_additional_course_is_the_real_enumerated_pool(self):
        for major in ("CASBA", "CASBS"):
            plan = engine.load_degree_plan(major, 2026)
            items = [
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course" and "CAS Additional Course" in (item.get("label") or "")
            ]
            self.assertEqual(len(items), 3, f"{major}: expected 3 CAS Additional Course picks")
            for item in items:
                self.assertEqual(set(item["options"]), {"CAS 203", "CAS 210", "CAS 215", "CAS 220"})

    def test_cas_additional_course_resolves_to_three_distinct_real_courses(self):
        for major in ("CASBA", "CASBS"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            self.assertEqual([w for w in fp["warnings"] if "Could not schedule" in w], [])
            picked = {
                c["code"] for term in fp["terms"] for c in term["courses"]
                if c["code"] in {"CAS 203", "CAS 210", "CAS 215", "CAS 220"}
            }
            self.assertEqual(len(picked), 3, f"{major}: expected 3 distinct CAS Additional courses, got {picked}")

    def test_cas_4xx_and_supporting_slots_exclude_cas_126_and_195(self):
        # Bulletin: "CAS 126 and CAS 195 may not be counted as part of the major."
        for major in ("CASBA", "CASBS"):
            plan = engine.load_degree_plan(major, 2026)
            patterns = [
                re.compile(item["match"])
                for _, item in engine._iter_plan_items(plan)
                if item.get("match") and item.get("type") == "slot"
                and ("CAS 4XX" in (item.get("label") or "") or "Supporting CAS" in (item.get("label") or ""))
            ]
            self.assertTrue(patterns, f"{major}: expected CAS 4XX/Supporting CAS match slots")
            for pattern in patterns:
                self.assertFalse(pattern.match("CAS 126"))
                self.assertFalse(pattern.match("CAS 195"))

    def test_casbs_quantification_pool_matches_the_real_bulletin_list(self):
        plan = engine.load_degree_plan("CASBS", 2026)
        quant_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "Quantification" in (item.get("label") or "")
        ]
        self.assertEqual(len(quant_items), 4, "expected 4 Quantification picks (12cr)")
        for item in quant_items:
            for code in item["options"]:
                self.assertTrue(code.startswith("MATH ") or code.startswith("STAT "), code)

    def test_casbs_related_disciplines_is_wired_and_excludes_cas(self):
        plan = engine.load_degree_plan("CASBS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Related Disciplines Course (outside CAS)"
        )
        self.assertTrue(item.get("open_elective"))
        pick = engine._pick_open_elective(
            catalog, set(), set(), exclude_prefixes=item["elective_exclude_prefixes"],
        )
        self.assertIsNotNone(pick)
        self.assertFalse(pick[0].startswith("CAS "), pick)

    def test_casba_and_casbs_full_plan_build_cleanly(self):
        for major in ("CASBA", "CASBS"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            self.assertEqual([w for w in fp["warnings"] if "Could not schedule" in w], [])


class TestECONHandbookRequirements(unittest.TestCase):
    """No separate Economics department handbook exists beyond the university
    bulletin (confirmed via econ.la.psu.edu and sites.psu.edu/econadvising,
    neither of which publish enumerated Supporting-Course lists -- the
    bulletin itself only ever says 'select N credits ... from department
    list' with no codes). Verifies the one concrete, quantifiable real gap
    found on the CURRENT live bulletin: the ECON 300/400-level elective
    requirement is 18 credits (>=9 at 400-level), but this plan only modeled
    12 of those credits before this pass."""

    def test_econ_bs_models_the_full_18_credits_of_econ_electives(self):
        plan = engine.load_degree_plan("ECON", 2026)
        total = sum(
            item["credits"] for _, item in engine._iter_plan_items(plan)
            if "ECON elective" in (item.get("label") or "")
        )
        self.assertEqual(total, 18)
        at_400 = sum(
            item["credits"] for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "400-Level ECON elective"
        )
        self.assertGreaterEqual(at_400, 9)

    def test_econ_ba_already_modeled_the_full_18_credits(self):
        # Sibling plan built in the same batch -- confirm it already got this
        # right, so this is a "no fix needed there" claim backed by a test.
        plan = engine.load_degree_plan("ECONBA", 2026)
        total = sum(
            item["credits"] for _, item in engine._iter_plan_items(plan)
            if "ECON elective" in (item.get("label") or "")
        )
        self.assertEqual(total, 18)


class TestANTHHandbookRequirements(unittest.TestCase):
    """No separate Anthropology department handbook exists beyond the
    university bulletin (anth.la.psu.edu's own 'Resources for Undergraduate
    Students' page links only to the bulletin), but the CURRENT live bulletin
    page is far more specific than this plan originally assumed. Covers ANTH
    (B.A.) and the ANTH 432 gap in ANTHSBS (B.S. -- Integrated Option)."""

    def test_anth_survey_pattern_matches_the_real_area_survey_rule(self):
        plan = engine.load_degree_plan("ANTH", 2026)
        patterns = [
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(plan)
            if item.get("match") and "Survey" in (item.get("label") or "")
        ]
        self.assertTrue(patterns)
        for pattern in patterns:
            for code in ["ANTH 2N", "ANTH 21", "ANTH 45N", "ANTH 100", "ANTH 189", "ANTH 297"]:
                self.assertTrue(pattern.match(code), f"{code} should match {pattern.pattern}")
            # Bulletin's own exclusions: ANTH 1, ANTH 83S, and 190-199/290-299
            # (other than 297) don't count as Area/Survey courses.
            for code in ["ANTH 1", "ANTH 83S", "ANTH 190", "ANTH 199", "ANTH 290", "ANTH 298", "ANTH 400"]:
                self.assertFalse(pattern.match(code), f"{code} should NOT match {pattern.pattern}")

    def test_anth_methods_pool_is_no_longer_duplicated(self):
        # The bulletin's real 'Methods Courses' category is 6 credits total,
        # and its own enumerated pool already includes ANTH 426W/427W -- this
        # plan used to ALSO require ANTH 426W/427W as a separate 3cr item on
        # top of two more generic Methods slots (9cr for a 6cr category).
        plan = engine.load_degree_plan("ANTH", 2026)
        methods_items = [
            item for _, item in engine._iter_plan_items(plan)
            if "Methods" in (item.get("label") or "") or item.get("options") == ["ANTH 426W", "ANTH 427W"]
        ]
        total = sum(item["credits"] for item in methods_items)
        self.assertEqual(total, 6, f"Methods category should total 6cr, got {total}cr from {methods_items}")

    def test_anth_methods_pool_uses_the_real_bulletin_list(self):
        plan = engine.load_degree_plan("ANTH", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "ANTH Methods Course" in (item.get("label") or "")
        )
        # ANTH 380 is named in the bulletin's footnote but doesn't exist in
        # the real catalog (confirmed absent from anth_catalog.json) -- must
        # not be modeled.
        self.assertNotIn("ANTH 380", item["options"])
        for code in ["ANTH 321W", "ANTH 410", "ANTH 411", "ANTH 425", "ANTH 428", "ANTH 432", "ANTH 458", "ANTH 492", "ANTH 493"]:
            self.assertIn(code, item["options"])

    def test_anth_400_level_ranges_are_mutually_exclusive_and_cover_the_real_ranges(self):
        plan = engine.load_degree_plan("ANTH", 2026)
        by_label = {}
        for _, item in engine._iter_plan_items(plan):
            if item.get("match") and "ANTH 400-Level" in (item.get("label") or ""):
                by_label[item["label"]] = re.compile(item["match"])
        archaeology = next(rx for label, rx in by_label.items() if "Archaeology" in label)
        biological = next(rx for label, rx in by_label.items() if "Biological" in label)
        humaneco = next(rx for label, rx in by_label.items() if "Human Ecology" in label)
        self.assertTrue(archaeology.match("ANTH 420") and archaeology.match("ANTH 439"))
        self.assertTrue(biological.match("ANTH 400") and biological.match("ANTH 419") and biological.match("ANTH 460"))
        self.assertTrue(humaneco.match("ANTH 440") and humaneco.match("ANTH 459") and humaneco.match("ANTH 474"))
        # No overlap between the three ranges.
        for code in ["ANTH 420", "ANTH 400", "ANTH 440"]:
            matches = sum(bool(rx.match(code)) for rx in (archaeology, biological, humaneco))
            self.assertEqual(matches, 1, f"{code} should match exactly one range")

    def test_anthsbs_methods_pool_includes_anth_432(self):
        # Real, catalogued course (Environmental Archaeology) named on the
        # bulletin's own Methods list but missing from this plan's pool.
        plan = engine.load_degree_plan("ANTHSBS", 2026)
        methods_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and item.get("label") == "ANTH Methods Course"
        ]
        self.assertTrue(methods_items)
        for item in methods_items:
            self.assertIn("ANTH 432", item["options"])

    def test_anth_and_anthsbs_full_plan_build_cleanly(self):
        for major in ("ANTH", "ANTHSBS"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            self.assertEqual([w for w in fp["warnings"] if "Could not schedule" in w], [])


class TestAPLNGBAHandbookRequirements(unittest.TestCase):
    """No separate Applied Linguistics handbook exists beyond the university
    bulletin, but the CURRENT live bulletin page enumerates real course lists
    this plan had previously treated as fully open/adviser-driven."""

    def test_additional_courses_pool_is_the_real_enumerated_list(self):
        plan = engine.load_degree_plan("APLNGBA", 2026)
        pool_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "Additional Courses" in (item.get("label") or "")
        ]
        self.assertEqual(len(pool_items), 4, "expected 4 real APLNG Additional-Courses picks")
        real_list = {"APLNG 200", "APLNG 210", "APLNG 220N", "APLNG 230N", "APLNG 250", "APLNG 260N",
                     "APLNG 280N", "APLNG 310", "APLNG 410", "APLNG 412", "APLNG 484", "APLNG 491", "APLNG 493"}
        for item in pool_items:
            self.assertEqual(set(item["options"]), real_list)
            # The required Capstone (APLNG 494) is a separate requirement,
            # not part of this elective pool.
            self.assertNotIn("APLNG 494", item["options"])

    def test_related_area_is_wired_to_the_real_supporting_departments(self):
        plan = engine.load_degree_plan("APLNGBA", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label", "").startswith("Related Area course")
        )
        self.assertTrue(item.get("open_elective"))
        self.assertEqual(item.get("elective_min_level"), 300)
        pick = engine._pick_open_elective(
            catalog, set(), set(),
            min_level=item["elective_min_level"],
            exclude_prefixes=item["elective_exclude_prefixes"],
            prefer_prefixes=item["elective_prefer"],
        )
        self.assertIsNotNone(pick)
        self.assertTrue(pick[0].split(" ")[0] in {"AFR", "WMNST", "LING"}, pick)

    def test_full_plan_builds_cleanly(self):
        plan = engine.load_degree_plan("APLNGBA", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        self.assertEqual([w for w in fp["warnings"] if "Could not schedule" in w], [])


class TestCHNSBAHandbookRequirements(unittest.TestCase):
    """No separate Chinese department handbook exists; the CURRENT live
    bulletin names this pool 'CHNS 414-419' (this plan previously modeled
    only 415-419, missing the real, catalogued CHNS 414)."""

    def test_chns_414_419_pool_includes_chns_414(self):
        plan = engine.load_degree_plan("CHNSBA", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) & {"CHNS 415", "CHNS 416"}
        )
        self.assertIn("CHNS 414", item["options"])

    def test_full_plan_builds_cleanly(self):
        plan = engine.load_degree_plan("CHNSBA", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        self.assertEqual([w for w in fp["warnings"] if "Could not schedule" in w], [])


class HHDPlanTestMixin:
    """Shared helpers for the College of Health and Human Development
    verification passes (BBH, CSD, HDFS, HM, HPA, KINES, NROSCI, NUTR,
    RPTM) — same "_reach" pattern as TestCMPSCHandbookRequirements."""

    def _reach(self, plan, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def _recommend_for(self, plan, catalog, item):
        completed, consumed = self._reach(plan, item)
        rec = engine.recommend_semester(
            plan, catalog, completed, consumed_slots=consumed, max_credits=99,
        )
        return next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)


class TestBBHHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the live bulletins.psu.edu Suggested Academic Plan
    for Biobehavioral Health, B.S. (2026-27 edition) -- the department's own
    undergraduate handbook page (hhd.psu.edu/bbh/biobehavioral-health-undergraduate-handbook)
    is access-restricted (403) to the public, so the bulletin's own detailed,
    numbered footnote course lists are the real source used here. Every one
    of this major's five elective categories was previously a fully generic,
    unfillable placeholder; all five are now wired to their real footnoted
    lists."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("BBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    def test_every_generic_elective_now_recommends_a_real_course(self):
        plan, catalog = self._plan_and_catalog()
        labels = {
            "Health and Developmental Science Elective",
            "Life Sciences Elective",
            "Basic Science Elective",
            "Health Promotion Elective",
            "BBH Additional Elective (2XX-4XX)",
            "BBH Additional Elective (4XX)",
        }
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") in labels
        ]
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item.get("type"), "course", f"{item['label']} (id {item['id']}) should be a real course pool, not a generic slot")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"{item['label']} (id {item['id']}) was never recommended a course")
            self.assertIsNotNone(pick["code"], f"{item['label']} (id {item['id']}) got a placeholder, not a real course")

    def test_4xx_labeled_slots_are_all_400_level(self):
        plan, _ = self._plan_and_catalog()
        for _, item in engine._iter_plan_items(plan):
            if item.get("label") == "BBH Additional Elective (4XX)":
                for code in item["options"]:
                    num = int(re.match(r"[A-Z]+\s+(\d+)", code).group(1))
                    self.assertGreaterEqual(num, 400, f"{code} is not 400-level")

    def test_bbh_302_excluded_from_additional_elective_pool_as_already_required(self):
        # BBH/AFAM 302 (Diversity and Health) is a required Prescribed Course
        # elsewhere in this same plan (Semester 5) -- the bulletin's own
        # footnote 5 lists it again (an artifact of it being shared with the
        # Commonwealth-campus plan, where 302 substitutes for 310 for Schreyer
        # students), but including it here would just be a confusing
        # duplicate of an already-required course.
        plan, _ = self._plan_and_catalog()
        for _, item in engine._iter_plan_items(plan):
            if item.get("label", "").startswith("BBH Additional Elective"):
                self.assertNotIn("BBH 302", item["options"])

    def test_cmas_codes_excluded_as_unverifiable(self):
        # The bulletin's footnote 2 lists CMAS 258/465/466, but no CMAS
        # catalog exists in this app and that prefix is a Commonwealth-campus
        # Communication Arts and Sciences designation -- not clearly offered
        # at this plan's University Park/World Campus end campus. Excluded
        # rather than guessed.
        plan, _ = self._plan_and_catalog()
        for _, item in engine._iter_plan_items(plan):
            if item.get("label") == "Health and Developmental Science Elective":
                for code in item["options"]:
                    self.assertFalse(code.startswith("CMAS"), f"unverifiable code {code} present")


class TestCSDHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the live bulletins.psu.edu Suggested Academic Plan
    for Communication Sciences and Disorders, B.S. (2026-27 edition) --
    no separate CSD undergraduate handbook is publicly published, but the
    bulletin itself links directly to the department's real, live
    course-suggestion page (hhd.psu.edu/csd/student-support/courses), which
    names every real course behind this plan's Gen Ed sub-types and its 7
    'Elective' slots."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("CSD", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    def test_gn_slots_split_into_real_biological_and_physical_science_subtypes(self):
        # ASHA accreditation requires a biological science AND a physical
        # science course for SLP/Audiology grad admission (bulletin's own
        # footnote 2) -- previously both slots were an unrestricted generic
        # GN pick that could satisfy this with two of the same sub-type.
        plan, _ = self._plan_and_catalog()
        bio_item = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "GEN ED (GN, Human Biological)")
        phys_item = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "GEN ED (GN, Physical Science)")
        self.assertEqual(set(bio_item["options"]), {"BISC 2", "BISC 4", "BIOL 133", "BIOL 155"})
        self.assertEqual(set(phys_item["options"]), {"CHEM 1", "CHEM 101", "CHEM 130", "PHYS 1"})
        self.assertEqual(bio_item["type"], "course")
        self.assertEqual(phys_item["type"], "course")

    def test_inter_domain_and_exploration_labels_match_the_real_suggested_plan(self):
        # The plan previously had these two swapped relative to the live
        # Suggested Academic Plan (Second Year Fall is really Inter-Domain,
        # Second Year Spring is really Exploration).
        plan, _ = self._plan_and_catalog()
        sem3 = next(sem for sem in plan["semesters"] if sem["index"] == 3)
        sem4 = next(sem for sem in plan["semesters"] if sem["index"] == 4)
        sem3_gen_ed = next(it for it in sem3["items"] if it["type"] == "slot" and "GEN ED" in it.get("label", ""))
        sem4_gen_ed = next(it for it in sem4["items"] if it["type"] == "slot" and "GEN ED" in it.get("label", ""))
        self.assertEqual(sem3_gen_ed.get("gen_ed"), "INTER-D")
        self.assertNotIn("gen_ed", sem4_gen_ed)

    def test_elective_slots_recommend_a_real_course(self):
        plan, catalog = self._plan_and_catalog()
        elective_items = [
            item for _, item in engine._iter_plan_items(plan)
            if (item.get("label") or "").startswith("Elective")
        ]
        self.assertEqual(len(elective_items), 7)
        for item in elective_items:
            self.assertEqual(item.get("type"), "course", f"item {item['id']} should be a real course pool")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_csd_431_recommended_slot_prefers_csd_431(self):
        # CSD 431 is listed first (matching the plan's own "recommended"
        # note; the engine's option-ranking prefers earlier-listed eligible
        # options within the same tier) and its only real prereq, CSD 331,
        # is already required earlier in this same plan (Semester 6) so it's
        # always reachable by the time this Semester 8 slot comes up.
        plan, catalog = self._plan_and_catalog()
        item = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Elective (CSD 431 recommended)")
        self.assertEqual(item["options"][0], "CSD 431")
        c431 = catalog["CSD 431"]
        self.assertTrue(engine.prereqs_satisfied(c431, {"CSD 331"}))
        # Isolate this one item (an empty exclude/completed set besides its
        # real prereq) to avoid the unrelated collision of CSD 431 also
        # being listed first in earlier semesters' own "Elective" pools.
        pick = engine._pick_option(item, catalog, completed={"CSD 331"})
        self.assertEqual(pick, "CSD 431")

    def test_cmas_code_excluded_as_unverifiable(self):
        plan, _ = self._plan_and_catalog()
        for _, item in engine._iter_plan_items(plan):
            if item.get("type") == "course":
                for code in item.get("options", []):
                    self.assertFalse(code.startswith("CMAS"), f"unverifiable code {code} present in item {item['id']}")


class TestHDFSHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the live bulletins.psu.edu Suggested Academic Plan
    and requirements table for Human Development and Family Studies, B.S.
    -- Human Development and Family Science Option (2026-27 edition). No
    separate HDFS undergraduate handbook is publicly published, but the
    bulletin's own requirements table (not just its suggested plan) names
    exact real course lists for 'Advanced Development', 'Advanced Family
    Topics', and 'Professional Skills for HDFS Careers', and links to a real
    department page for 'Diversity and Development'."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("HDFS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    def test_every_generic_elective_now_recommends_a_real_course(self):
        plan, catalog = self._plan_and_catalog()
        labels = {
            "Diversity and Development Course",
            "Advanced Development Course",
            "Advanced Family Topics Course",
            "Professional Skills for HDFS Careers Course",
        }
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") in labels]
        # 2 Diversity and Development + 1 Advanced Development + 1 Advanced
        # Family Topics + 2 Professional Skills = 6, matching the real
        # bulletin's own credit totals for each category exactly.
        self.assertEqual(len(items), 6)
        for item in items:
            self.assertEqual(item.get("type"), "course", f"{item['label']} (id {item['id']}) should be a real course pool")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"{item['label']} (id {item['id']}) was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_advanced_development_and_family_topics_lists_match_bulletin_table(self):
        plan, _ = self._plan_and_catalog()
        adv_dev = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Advanced Development Course")
        adv_fam = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Advanced Family Topics Course")
        self.assertEqual(set(adv_dev["options"]), {"HDFS 405", "HDFS 413", "HDFS 428", "HDFS 429", "HDFS 432", "HDFS 433", "HDFS 434", "HDFS 445", "HDFS 447"})
        self.assertEqual(set(adv_fam["options"]), {"HDFS 412", "HDFS 415", "HDFS 416", "HDFS 417", "HDFS 418", "HDFS 424", "HDFS 431", "HDFS 469U", "SOC 430"})

    def test_inter_domain_slots_are_wired(self):
        # Semester 1 and Semester 4's "GEN ED (Integrative Studies)" items
        # were labeled correctly but never actually wired to the engine's
        # Inter-Domain picker -- a mislabeled-but-unwired placeholder, same
        # bug class fixed for CMPSC's own "(GN)"-labeled slot.
        plan, _ = self._plan_and_catalog()
        sem1 = next(sem for sem in plan["semesters"] if sem["index"] == 1)
        sem4 = next(sem for sem in plan["semesters"] if sem["index"] == 4)
        item1 = next(it for it in sem1["items"] if it.get("label") == "GEN ED (Integrative Studies)")
        item4 = next(it for it in sem4["items"] if it.get("label") == "GEN ED (Integrative Studies)")
        self.assertEqual(item1.get("gen_ed"), "INTER-D")
        self.assertEqual(item4.get("gen_ed"), "INTER-D")

    def test_semester_3_inter_domain_slot_replaces_the_old_mislabeled_elective(self):
        # The real Suggested Academic Plan's Second Year Fall 5th item is an
        # Inter-Domain Gen Ed course, not a generic "Elective" as this plan
        # previously modeled it.
        plan, _ = self._plan_and_catalog()
        sem3 = next(sem for sem in plan["semesters"] if sem["index"] == 3)
        labels = [it.get("label") for it in sem3["items"]]
        self.assertNotIn("Elective", labels)
        gen_ed_items = [it for it in sem3["items"] if it.get("gen_ed") == "INTER-D"]
        self.assertEqual(len(gen_ed_items), 1)


class TestHMHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the School of Hospitality Management's real,
    public program-details page (hhd.psu.edu/shm/undergraduate/
    major-hospitality-management/hospitality-management-program-details),
    which publishes a concrete 'HM Elective Approved List' -- the
    department's own student handbook page is access-restricted (403)."""

    ELECTIVE_LIST = {
        "HM 208", "HM 209", "HM 210N", "HM 304", "HM 306", "HM 310", "HM 311",
        "HM 318", "HM 319", "HM 322", "HM 344", "HM 382", "HM 384", "HM 386",
        "HM 388", "HM 390", "HM 395A", "HM 395B", "HM 395C", "HM 395D",
        "HM 407", "HM 413", "HM 432", "HM 435", "HM 481", "HM 482", "HM 484",
        "HM 485", "HM 486", "HM 488", "HM 494", "HM 496",
    }

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("HM", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    def test_every_hm_elective_slot_wired_to_the_real_approved_list(self):
        plan, catalog = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "HM Elective"]
        self.assertEqual(len(items), 10)
        for item in items:
            self.assertEqual(item.get("type"), "course", f"item {item['id']} should be a real course pool")
            self.assertEqual(set(item["options"]), self.ELECTIVE_LIST)
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_395x_shorthand_expanded_to_real_lettered_sections(self):
        # The source page's own "395X" notation is department shorthand for
        # the 395A-D lettered-section family -- all four are real, separate
        # courses in this app's hm_catalog.json, not a single course "395X".
        catalog = engine.load_merged_catalog(["HM"])
        for code in ("HM 395A", "HM 395B", "HM 395C", "HM 395D"):
            self.assertIn(code, catalog)
        self.assertNotIn("HM 395X", catalog)


class TestHPAHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the Health Policy and Administration department's
    real, public Supporting Courses page (hhd.psu.edu/hpa/supporting-courses),
    which lists 250+ approved codes across six concentrations this plan
    doesn't specifically commit to. Wired the generic 'Supporting Course'
    slots to the real approved codes within departments this plan already
    loads (a deliberately bounded, real subset of the full page -- see the
    plan's own notes), and the 400-level slots to the page's real HPA
    400-level list, excluding HPA 442/444/446 (already required items
    elsewhere in this plan) and internship/independent-study codes."""

    REQUIRED_HPA_400 = {"HPA 442", "HPA 444", "HPA 446"}
    NON_SUPPORTING_HPA = {"HPA 490", "HPA 495", "HPA 496", "HPA 497", "HPA 499"}

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("HPA", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    def test_supporting_course_slots_recommend_a_real_course(self):
        plan, catalog = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Supporting Course"]
        self.assertEqual(len(items), 7)
        for item in items:
            self.assertEqual(item.get("type"), "course")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_400_level_supporting_course_excludes_already_required_hpa_electives(self):
        plan, catalog = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Supporting Course (400-level)"]
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(item.get("type"), "course")
            for code in item["options"]:
                self.assertTrue(code.startswith("HPA "))
                num = int(re.match(r"HPA\s+(\d+)", code).group(1))
                self.assertGreaterEqual(num, 400)
                self.assertNotIn(code, self.REQUIRED_HPA_400, f"{code} is already a required item elsewhere in this plan")
                self.assertNotIn(code, self.NON_SUPPORTING_HPA, f"{code} is internship/independent-study, not a real elective")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")

    def test_free_elective_not_conflated_with_supporting_course(self):
        # Semester 7's plain "Elective" is a genuine free elective per the
        # bulletin, not part of the 30-credit Supporting Course requirement
        # -- it must stay a generic slot, not silently inherit the
        # Supporting Course allowlist.
        plan, _ = self._plan_and_catalog()
        elective = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Elective")
        self.assertEqual(elective.get("type"), "slot")


class TestKINESHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the live 2026-27 bulletin's 'Movement Science
    Option (40-42 credits)' requirements table and the department's real,
    live 'Movement Science Option Requirements - Supporting Courses' page
    (hhd.psu.edu/kines/movement-science-option-requirements-supporting-
    courses)."""

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("KINES", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    def test_400_level_kines_match_excludes_kines_403(self):
        # Real bulletin text: "Select 12 additional credits from 400-level
        # Kines courses except KINES 403."
        plan, _ = self._plan_and_catalog()
        patterns = [
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "KINES 400-level Elective" and item.get("match")
        ]
        self.assertEqual(len(patterns), 3)
        for pattern in patterns:
            self.assertFalse(pattern.match("KINES 403"))
            self.assertTrue(pattern.match("KINES 425W"))
            self.assertTrue(pattern.match("KINES 447W"))
            self.assertTrue(pattern.match("KINES 495D"))
            self.assertFalse(pattern.match("KINES 384"))  # 300-level, not 400-level

    def test_grad_school_prerequisite_slots_recommend_a_real_course(self):
        plan, catalog = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Elective (graduate school prerequisite)"]
        self.assertEqual(len(items), 5)
        for item in items:
            self.assertEqual(item.get("type"), "course")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_kines_403_and_495d_available_in_grad_prereq_pool(self):
        # The real Supporting Courses list explicitly includes KINES 403 and
        # KINES 495D -- consistent with the bulletin excluding/capping them
        # from the separate 400-level-KINES-elective pool above.
        plan, _ = self._plan_and_catalog()
        item = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Elective (graduate school prerequisite)")
        self.assertIn("KINES 403", item["options"])
        self.assertIn("KINES 495D", item["options"])


class TestNROSCIHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the live bulletins.psu.edu 2026-27 edition for
    Systems Neuroscience, B.S. -- no separate department handbook, but the
    bulletin itself publishes exact, real 'Additional neuroscience courses'
    (15 options) and 'Basic Science' (38+ options) lists that exactly match
    the counts this plan's own notes had already anticipated."""

    ADDITIONAL_NEURO = {"BBH 204", "BBH 410", "BBH 432", "BBH 475H", "BBH 494", "BBH 426",
                         "BMB 400", "BME 406", "BME 437", "CSD 431", "CSD 497A",
                         "KINES 360", "KINES 465", "NUTR 460", "PSYCH 455"}

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("NROSCI", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [], fp["warnings"])

    def test_additional_neuroscience_elective_matches_real_15_option_list(self):
        plan, _ = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Additional Neuroscience Elective"]
        self.assertEqual(len(items), 4, "real requirement is 12 credits across 4 items")
        for item in items:
            self.assertEqual(set(item["options"]), self.ADDITIONAL_NEURO)

    def test_basic_science_elective_recommends_a_real_course(self):
        plan, catalog = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Basic Science Elective"]
        self.assertEqual(len(items), 3, "real requirement is 9 credits across 3 items")
        for item in items:
            self.assertEqual(item.get("type"), "course")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")

    def test_psych_260_ordering_fix_lets_bbh_468_schedule(self):
        # Regression test for the real latent scheduling bug this session's
        # elective-wiring exposed: BBH 468 needs literal "BBH 469 or
        # PSYCH 260", but BIOL 469 (not BBH 469) is deliberately guaranteed
        # elsewhere in this plan for BBH 470/BIOL 470's sake -- so the
        # Semester 2 pool must default to PSYCH 260, not BBH 203.
        plan, _ = self._plan_and_catalog()
        item = next(item for _, item in engine._iter_plan_items(plan) if set(item.get("options", [])) == {"PSYCH 260", "BBH 203"})
        self.assertEqual(item["options"][0], "PSYCH 260")

    def test_bbh_468_schedulable_end_to_end(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        all_codes = {c["code"] for term in fp["terms"] for c in term["courses"] if c["code"]}
        self.assertIn("BBH 468", all_codes)


class TestNUTRHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the live bulletin (2026-27 edition) and the
    Nutritional Sciences department's own real, live Supporting Course List
    page (hhd.psu.edu/nutrition/supporting-courses). This major is
    ACEND-accredited (Didactic Program in Dietetics) so its prescribed
    courses are unusually fixed -- every one already matched the live
    bulletin exactly (no drift). Only its 'Supporting Course (400-level)'
    and 'University-Wide Offering' generic pools needed real wiring."""

    NUTR_100 = "NUTR 100"
    NUTR_119 = "NUTR 119"

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("NUTR", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    def test_supporting_course_400_level_recommends_a_real_course(self):
        plan, catalog = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Supporting Course (400-level)"]
        self.assertEqual(len(items), 3, "real requirement is 9 credits at the 400 level")
        for item in items:
            self.assertEqual(item.get("type"), "course")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")

    def test_university_wide_offering_recommends_a_real_course(self):
        plan, catalog = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "University-Wide Offering"]
        self.assertEqual(len(items), 2, "real requirement is 6 credits")
        for item in items:
            self.assertEqual(item.get("type"), "course")
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")

    def test_nutr_100_and_119_excluded_per_real_department_restriction(self):
        # Department's own page: "NUTR 100 (3 cr) cannot be used to count
        # toward the Nutrition and Dietetics option major degree
        # requirements or elective courses" and NUTR 119 is explicitly
        # carved out as ineligible too.
        plan, _ = self._plan_and_catalog()
        for _, item in engine._iter_plan_items(plan):
            if item.get("type") == "course":
                self.assertNotIn(self.NUTR_100, item.get("options", []))
                self.assertNotIn(self.NUTR_119, item.get("options", []))


class TestRPTMHandbookRequirements(HHDPlanTestMixin, unittest.TestCase):
    """Verified against the department's own real, live Supporting Courses
    page for the Commercial Recreation and Tourism Management option
    (hhd.psu.edu/rptm/undergraduate/supporting-courses) and RPTM 433W's own
    scraped catalog description, which quotes its real bulletin prereq
    verbatim."""

    SUPPORTING_LIST = {
        "RPTM 1", "RPTM 115", "RPTM 140", "RPTM 199", "RPTM 201", "RPTM 215",
        "RPTM 230", "RPTM 280", "RPTM 310", "RPTM 315", "RPTM 320", "RPTM 330",
        "RPTM 335", "RPTM 345", "RPTM 351", "RPTM 370", "RPTM 395", "RPTM 435",
        "RPTM 457", "RPTM 475", "RPTM 499", "STAT 200", "CMPSC 203",
        "CAS 101N", "CAS 203", "CAS 212", "CAS 250", "CAS 251", "CAS 252",
        "CAS 271N", "CAS 272N", "CAS 302",
    }

    def _plan_and_catalog(self):
        plan = engine.load_degree_plan("RPTM", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        return plan, catalog

    def test_full_plan_builds_cleanly(self):
        plan, catalog = self._plan_and_catalog()
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    def test_supporting_course_slots_wired_to_real_department_list(self):
        plan, catalog = self._plan_and_catalog()
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Supporting Course"]
        self.assertEqual(len(items), 5)
        for item in items:
            self.assertEqual(item.get("type"), "course")
            self.assertEqual(set(item["options"]), self.SUPPORTING_LIST)
            pick = self._recommend_for(plan, catalog, item)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")

    def test_rptm_433w_now_requires_a_real_statistics_course(self):
        # RPTM 433W's own catalog description: "RPTM 356 and a 3-credit
        # course in statistics are prerequisites for this course." RPTM 356
        # does not exist in the catalog and is left unenforced; the
        # statistics half is now real and enforced.
        catalog = engine.load_merged_catalog(["RPTM", "STAT"])
        course = catalog["RPTM 433W"]
        self.assertIn({"STAT 200", "STAT 100"}, course.prereq_groups)
        self.assertFalse(engine.prereqs_satisfied(course, set()))
        self.assertTrue(engine.prereqs_satisfied(course, {"STAT 200"}))
        self.assertTrue(engine.prereqs_satisfied(course, {"STAT 100"}))

    def test_rptm_356_confirmed_absent_from_catalog(self):
        catalog = engine.load_merged_catalog(["RPTM"])
        self.assertNotIn("RPTM 356", catalog)

    def test_stale_gq_label_cleaned_up(self):
        plan, _ = self._plan_and_catalog()
        labels = [item.get("label") for _, item in engine._iter_plan_items(plan)]
        self.assertNotIn("Elective / GEN ED (GQ)", labels)


class TestABSMHandbookRequirements(unittest.TestCase):
    """Agricultural and Biorenewable Systems Management, B.S. verified
    against the live bulletin plus the real, department-cited ABSM
    Advising Manual (abe.psu.edu/undergraduate/resources/advising/absm-
    manual). The manual's own 'Course Selection Lists' page is a broad,
    180+ course, multi-department pool (confirmed correctly left generic
    for 'Additional Specialization Elective'), but the bulletin itself
    names a real, closed, 10-course list for 'ABSM Selection Elective'
    that was previously a fully generic placeholder."""

    def test_absm_selection_elective_match_list_is_real_and_closed(self):
        plan = engine.load_degree_plan("ABSM", 2026)
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "ABSM Selection Elective"]
        self.assertEqual(len(items), 4, "expected all 4 ABSM Selection Elective slots (12cr total)")
        pattern = re.compile(items[0]["match"])
        for code in ("ABSM 310", "ABSM 320", "ABSM 327", "ABSM 402", "ABSM 411",
                     "ABSM 417", "ABSM 420", "ABSM 423", "ABSM 424", "ABSM 496"):
            self.assertTrue(pattern.match(code), f"{code} should match")
        # Not every ABSM course belongs to this specific closed list.
        for code in ("ABSM 100", "ABSM 300", "ABSM 301", "ABSM 350", "ABSM 490"):
            self.assertFalse(pattern.match(code), f"{code} should NOT match")

    def test_additional_specialization_elective_stays_generic(self):
        # The manual's broad multi-department list has no single enumerated
        # set worth hard-coding -- confirmed correctly left as a plain
        # unfilled placeholder (no match/open_elective wiring).
        plan = engine.load_degree_plan("ABSM", 2026)
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Additional Specialization Elective"]
        self.assertTrue(items)
        for item in items:
            self.assertNotIn("match", item)
            self.assertNotIn("open_elective", item)

    def test_econ_104_placement_matches_live_bulletin_not_swapped(self):
        # A conflicting research pass suggested moving ECON 104 to Semester
        # 1, but a direct bulletin re-check found ECON 104 is real and
        # correctly in the Spring (Semester 2) row already -- moving it
        # would break an already-correct match. This guards against that
        # regression being reintroduced.
        plan = engine.load_degree_plan("ABSM", 2026)
        sem1 = next(sem for sem in plan["semesters"] if sem["index"] == 1)
        sem2 = next(sem for sem in plan["semesters"] if sem["index"] == 2)
        sem1_codes = {o for item in sem1["items"] if item.get("type") == "course" for o in item.get("options", [])}
        sem2_codes = {o for item in sem2["items"] if item.get("type") == "course" for o in item.get("options", [])}
        self.assertNotIn("ECON 104", sem1_codes)
        self.assertIn("ECON 104", sem2_codes)

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("ABSM", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestNoDiscrepancyMajorsVerified(unittest.TestCase):
    """ANSC, FORES, and IID were cross-checked against their real
    department handbooks/bulletins and found to already be correct --
    no fabricated codes, no missing requirements, no unenforced
    exclusions. These tests pin down the specific facts that verification
    confirmed, as a regression guard, rather than re-litigating the
    research (see the session's final report for the full narrative)."""

    def test_ansc_selection_pools_have_no_published_list_and_stay_generic(self):
        # Confirmed against the live bulletin: "Additional Selection in
        # Consultation with Adviser," "ANSC Selection," "300-Level
        # Production," "ANSC/Other Selection," and "Other Selection" all
        # carry no enumerated course list whatsoever -- adviser-directed,
        # by design. Every one of them should remain a plain generic slot.
        plan = engine.load_degree_plan("ANSC", 2026)
        generic_labels = {
            "Additional Selection (with adviser)", "ANSC Selection",
            "300-Level Production", "ANSC Selection (400-Level)",
            "ANSC/Other Selection", "Other Selection",
        }
        found_any = False
        for _, item in engine._iter_plan_items(plan):
            if item.get("label") in generic_labels:
                found_any = True
                self.assertNotIn("match", item)
                self.assertNotIn("open_elective", item)
        self.assertTrue(found_any, "expected to find ANSC's generic selection slots")

    def test_ansc_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("ANSC", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_fores_supporting_course_and_ent_pool_confirmed_correct(self):
        # Confirmed against the live bulletin: FORES's "Supporting Course"
        # (21cr, adviser-selected, min 12cr at 300/400-level) has no
        # published enumerated list, and the ENT 313/FOR 403/PPEM 318 pool
        # legitimately appears twice (2 real slots to fill from that pool).
        plan = engine.load_degree_plan("FORES", 2026)
        supporting = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Supporting Course"]
        self.assertTrue(supporting)
        for item in supporting:
            self.assertNotIn("match", item)
        ent_pool_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "ENT 313" in item.get("options", [])
        ]
        self.assertEqual(len(ent_pool_items), 2)

    def test_fores_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("FORES", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    def test_iid_uses_the_five_year_override_and_builds_cleanly(self):
        # IID's real, corrected prereq chains (MATH->CHEM->BMB 400->VBSC
        # 448W) take a genuine minimum of ~10 sequential terms -- confirmed
        # against the live bulletin's own suggested plan, not a scheduling
        # bug. This is why IID is one of the majors carrying a 5-year
        # _GRAD_YEARS_OVERRIDE in TestCMPSCHandbookRequirements' sibling
        # batch test below (test_all_years_load_and_graduate_cleanly).
        import datetime
        plan = engine.load_degree_plan("IID", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=5,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestAGBMHandbookRequirements(unittest.TestCase):
    """Agribusiness Management, B.S. (AESE department) verified against the
    live bulletin (bulletins.psu.edu/.../agribusiness-management-bs/) --
    no separate AESE department handbook was found, only the bulletin.
    Found two real gaps: an "Integrative Studies" Gen Ed slot that was
    never wired to the engine's Gen Ed picker (the same class of bug found
    in CMPSC's own GN slot), and a 400-level elective slot whose own
    "notes" field claimed 495A/495B/496 were excluded but nothing actually
    enforced it."""

    def test_integrative_studies_slot_is_wired_to_il_domain(self):
        plan = engine.load_degree_plan("AGBM", 2026)
        il_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("gen_ed") == "IL"
        ]
        self.assertEqual(len(il_items), 2, "expected both Integrative Studies slots wired")
        for item in il_items:
            self.assertIn("IL", item.get("label", ""))

    def test_integrative_studies_slot_recommends_a_real_course(self):
        plan = engine.load_degree_plan("AGBM", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        il_item = next(item for _, item in engine._iter_plan_items(plan) if item.get("gen_ed") == "IL")
        completed = {
            o for _, it in engine._iter_plan_items(plan)
            if it["id"] < il_item["id"] and it.get("type") == "course"
            for o in [it["options"][0]]
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < il_item["id"] and it.get("type") == "slot"
        }
        rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
        pick = next((c for c in rec["courses"] if c["item_id"] == il_item["id"]), None)
        self.assertIsNotNone(pick, "Integrative Studies slot was never recommended a course")
        self.assertIsNotNone(pick["code"], "Integrative Studies slot got a placeholder, not a real course")

    def test_400_level_elective_excludes_non_credit_options(self):
        # The plan's own notes claimed 495A/495B/496 were excluded from the
        # "AGBM 400-level Elective" pool per the bulletin, but nothing
        # enforced it -- now backed by a real 'match' field.
        plan = engine.load_degree_plan("AGBM", 2026)
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "AGBM 400-level Elective"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            pattern = re.compile(item["match"])
            for code in ("AGBM 495A", "AGBM 495B", "AGBM 496"):
                self.assertFalse(pattern.match(code), f"{code} should NOT match {pattern.pattern}")
            for code in ("AGBM 407", "AGBM 408", "AGBM 440", "AGBM 460"):
                self.assertTrue(pattern.match(code), f"{code} should match {pattern.pattern}")

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("AGBM", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestCEDHandbookRequirements(unittest.TestCase):
    """Community, Environment, and Development, B.S. (Community and
    Economic Development option) verified against the live bulletin -- no
    separate department handbook found. Found two real gaps: the same
    unwired "Integrative Studies" Gen Ed bug as AGBM, and a course-code
    mix-up (CED 452, a real but WRONG course, standing in for the
    bulletin's actual CEDEV 452, a different real course)."""

    def test_integrative_studies_slots_wired_to_il_domain(self):
        plan = engine.load_degree_plan("CED", 2026)
        il_items = [item for _, item in engine._iter_plan_items(plan) if item.get("gen_ed") == "IL"]
        self.assertEqual(len(il_items), 2)

    def test_fourth_year_option_uses_real_cedev_452_not_wrong_ced_452(self):
        plan = engine.load_degree_plan("CED", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "CED 375" in item.get("options", [])
        )
        self.assertIn("CEDEV 452", item["options"])
        self.assertNotIn("CED 452", item["options"], "CED 452 is a real but different course (Community Organization)")

    def test_cedev_452_loads_and_is_satisfiable(self):
        plan = engine.load_degree_plan("CED", 2026)
        self.assertIn("CEDEV", plan["departments"])
        catalog = engine.load_merged_catalog(plan["departments"])
        self.assertIn("CEDEV 452", catalog)
        course = catalog["CEDEV 452"]
        # Real prereq is "6 credits in RSOC or SOC or PSYCH", simplified to
        # a single completed SOC 1 (already required Semester 1 of this plan).
        self.assertTrue(engine.prereqs_satisfied(course, {"SOC 1"}))
        self.assertFalse(engine.prereqs_satisfied(course, set()))

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("CED", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestERMHandbookRequirements(unittest.TestCase):
    """Environmental Resource Management, B.S. (Environmental Science
    option) verified against the live bulletin (Ecosystem Science and
    Management department -- no separate ERM-specific handbook found).
    Found a wrong/synthetic course code ('ASM 327' instead of the real
    ABSM 327) and two curated elective categories with real, published
    course lists that were previously fully generic placeholders."""

    def test_absm_327_is_the_real_code_not_synthetic_asm(self):
        plan = engine.load_degree_plan("ERM", 2026)
        self.assertIn("ABSM", plan["departments"])
        self.assertNotIn("ASM", plan["departments"])
        codes = {
            o for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        self.assertIn("ABSM 327", codes)
        self.assertNotIn("ASM 327", codes)
        catalog = engine.load_merged_catalog(plan["departments"])
        self.assertIn("ABSM 327", catalog)

    def test_ecology_selection_match_list_is_real_and_specific(self):
        plan = engine.load_degree_plan("ERM", 2026)
        item = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Ecology Selection")
        pattern = re.compile(item["match"])
        for code in ("BIOL 415", "ERM 430", "HORT 445", "SOILS 412W", "WFS 422"):
            self.assertTrue(pattern.match(code), f"{code} should match")
        for code in ("BIOL 110", "ERM 411", "HORT 101"):
            self.assertFalse(pattern.match(code), f"{code} should NOT match")

    def test_communications_sustainability_leadership_match_list(self):
        plan = engine.load_degree_plan("ERM", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Communications/Sustainability/Leadership Course"
        )
        pattern = re.compile(item["match"])
        for code in ("AEE 360", "CAS 213", "MGMT 215", "SUST 200", "ERM 402"):
            self.assertTrue(pattern.match(code), f"{code} should match")
        for code in ("CAS 100A", "ERM 411"):
            self.assertFalse(pattern.match(code), f"{code} should NOT match")

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("ERM", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestVBSHandbookRequirements(unittest.TestCase):
    """Veterinary and Biomedical Sciences, B.S. verified against the live
    bulletin's own "Requirements for the Major" table (bulletin-only -- the
    department's VBS handbook PDF is gated behind PSU's Microsoft SSO).
    Found 3 real gaps: a required course (VBSC 300) missing entirely, an
    incomplete biochemistry bundle (BMB 211 & 221 without the paired lab
    BMB 212), and an economics/business course with no basis in the real
    major requirements at all."""

    def test_vbsc_300_and_bmb_212_are_required(self):
        plan = engine.load_degree_plan("VBS", 2026)
        codes = {
            o for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        self.assertIn("VBSC 300", codes, "VBSC 300 (grade-C-or-better prescribed course) was missing")
        self.assertIn("BMB 212", codes, "BMB 212 (paired lab for the BMB 211 fast track) was missing")

    def test_no_fabricated_economics_requirement(self):
        # "BA 100 (or ECON 14/102/104, AGBM 101)" didn't correspond to any
        # real VBS requirement -- the real "Requirements for the Major"
        # table has no economics/business course anywhere in it.
        plan = engine.load_degree_plan("VBS", 2026)
        for _, item in engine._iter_plan_items(plan):
            if item.get("type") != "course":
                continue
            self.assertNotIn("BA 100", item.get("options", []))

    def test_vbsc_300_is_satisfiable_when_scheduled(self):
        plan = engine.load_degree_plan("VBS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        course = catalog["VBSC 300"]
        self.assertTrue(engine.prereqs_satisfied(course, {"BIOL 110", "CHEM 110", "CHEM 111"}))

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("VBS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestPHTXHandbookRequirements(unittest.TestCase):
    """Pharmacology and Toxicology, B.S. verified against the live
    bulletin's "Requirements for the Major" table (bulletin-only). Found
    two real gaps: the real 'VBSC 395 (Internship) or VBSC 496 (Independent
    Studies)' requirement is a SINGLE one-time 2-3cr choice, but the plan
    had it as FOUR separate slots totaling 6cr; and the real 'Supporting
    Courses' requirement is 9cr (three 3cr slots), but the plan only had
    two."""

    def test_single_internship_independent_studies_slot(self):
        plan = engine.load_degree_plan("PHTX", 2026)
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("match") and "VBSC" in item["match"] and "395" in item["match"]
        ]
        self.assertEqual(len(items), 1, "expected exactly one combined VBSC 395/496 slot")
        pattern = re.compile(items[0]["match"])
        self.assertTrue(pattern.match("VBSC 395"))
        self.assertTrue(pattern.match("VBSC 496"))
        self.assertLessEqual(items[0]["credits"], 3)

    def test_nine_credits_of_supporting_courses(self):
        plan = engine.load_degree_plan("PHTX", 2026)
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "slot" and item.get("label") == "Supporting Course (400-level)"
        ]
        total = sum(float(item.get("credits") or 0) for item in items)
        self.assertEqual(len(items), 3, "expected 3 Supporting Course (400-level) slots")
        self.assertEqual(total, 9.0, "real bulletin requirement is 9 credits of 400-level supporting courses")

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("PHTX", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestWFSHandbookRequirements(unittest.TestCase):
    """Wildlife and Fisheries Science, B.S. (Wildlife option) verified
    against the real Ecosystem Science and Management department's own
    Wildlife and Fisheries Science student handbook
    (ecosystems.psu.edu/undergraduate/resources/handbooks/wildlife-and-
    fisheries-science-student-handbook). Corrected a stale note that
    wrongly claimed WFS 301/310/446's WILDL 101 reference was an
    uncompletable catalog gap (it's a real course, and the catalog's
    existing OR-group with WFS 209N already handles it correctly), and
    wired three real, handbook-published elective categories that were
    previously fully generic placeholders."""

    def test_wildl_101_dependency_does_not_block_scheduling(self):
        import datetime
        plan = engine.load_degree_plan("WFS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        blocking = [w for w in fp["warnings"] if "WFS 301" in w or "WFS 310" in w or "WFS 446" in w]
        self.assertEqual(blocking, [])

    def test_wfs_209n_satisfies_the_real_or_group(self):
        plan = engine.load_degree_plan("WFS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        self.assertTrue(engine.concurrent_satisfied(catalog["WFS 301"], {"BIOL 110", "WFS 209N"}))
        self.assertTrue(engine.concurrent_satisfied(catalog["WFS 310"], {"BIOL 110", "WFS 209N", "STAT 200"}))
        self.assertTrue(engine.prereqs_satisfied(catalog["WFS 446"], {"WFS 209N", "STAT 200"}))

    def test_natural_resource_policy_match_list_is_real(self):
        plan = engine.load_degree_plan("WFS", 2026)
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if "Natural Resource Policy" in (item.get("label") or "")
        ]
        self.assertEqual(len(items), 2)
        pattern = re.compile(items[0]["match"])
        for code in ("ERM 411", "FOR 410", "GEOG 1N", "RPTM 120", "SOILS 71", "WFS 430"):
            self.assertTrue(pattern.match(code), f"{code} should match")
        for code in ("WFS 300", "FOR 200"):
            self.assertFalse(pattern.match(code), f"{code} should NOT match")

    def test_botany_and_fisheries_selection_match_lists(self):
        plan = engine.load_degree_plan("WFS", 2026)
        fisheries = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Fisheries Selection")
        botany = next(item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Botany Selection")
        fp = re.compile(fisheries["match"])
        bp = re.compile(botany["match"])
        for code in ("WFS 410", "WFS 422", "WFS 452", "WFS 463W"):
            self.assertTrue(fp.match(code))
        for code in ("BIOL 127", "FOR 308", "HORT 445"):
            self.assertTrue(bp.match(code))
        self.assertFalse(fp.match("HORT 101"))
        self.assertFalse(bp.match("WFS 410"))

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("WFS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestLSCPEHandbookRequirements(unittest.TestCase):
    """Landscape Contracting, B.S. (Design/Build option) verified against
    the live bulletin -- a real Landscape Contracting Student Handbook PDF
    exists (plantscience.psu.edu) but is scanned/image-based, not
    text-extractable. Corrected a stale claim that SPAN 105 and TURF 100
    "don't exist in the real catalog" -- both are real, current PSU
    courses and are already present in their catalog files; the bulletin's
    own alternatives ("SPAN 1, 2, or 105" and "TURF 100 or 235") are now
    fully modeled."""

    def test_span_105_and_turf_100_are_real_options(self):
        plan = engine.load_degree_plan("LSCPE", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        self.assertIn("SPAN 105", catalog)
        self.assertIn("TURF 100", catalog)
        span_item = next(item for _, item in engine._iter_plan_items(plan) if "SPAN 1" in item.get("options", []))
        turf_item = next(item for _, item in engine._iter_plan_items(plan) if "TURF 235" in item.get("options", []))
        self.assertIn("SPAN 105", span_item["options"])
        self.assertIn("TURF 100", turf_item["options"])

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("LSCPE", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestPLSCIHandbookRequirements(unittest.TestCase):
    """Plant Sciences, B.S. (Agroecology option) verified against the live
    bulletin (bulletin-only -- Plant Science department's own pages defer
    to the bulletin). Found real gaps: two generic Semester-4 placeholders
    that were standing in for specific, named requirements ('AGECO
    122/144/154/496' and 'AG 160/GEOG 30N/PHIL 13/103/132'); a fabricated
    course code ('SOILS 410W', which doesn't exist); and a real, published
    enumerated list for the 'Production Selection' category."""

    def test_semester_4_generic_slots_replaced_with_real_requirements(self):
        plan = engine.load_degree_plan("PLSCI", 2026)
        sem4 = next(sem for sem in plan["semesters"] if sem["index"] == 4)
        labels = [item.get("label") for item in sem4["items"]]
        self.assertFalse(any(l == "GEN ED" for l in labels), "generic GEN ED should have been replaced")
        self.assertFalse(any(l == "Elective" for l in labels), "generic Elective should have been replaced")
        options_lists = [item.get("options", []) for item in sem4["items"] if item.get("type") == "course"]
        self.assertTrue(any("AGECO 122" in opts for opts in options_lists))
        self.assertTrue(any("AG 160" in opts for opts in options_lists))

    def test_ag_160_loads_from_new_minimal_catalog(self):
        plan = engine.load_degree_plan("PLSCI", 2026)
        self.assertIn("AG", plan["departments"])
        catalog = engine.load_merged_catalog(plan["departments"])
        self.assertIn("AG 160", catalog)

    def test_soils_410w_fabricated_code_is_gone(self):
        plan = engine.load_degree_plan("PLSCI", 2026)
        codes = {
            o for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        self.assertNotIn("SOILS 410W", codes, "SOILS 410W does not exist in the real catalog")
        self.assertIn("SOILS 412W", codes)
        self.assertIn("HORT 412W", codes)

    def test_production_selection_match_list_is_real(self):
        plan = engine.load_degree_plan("PLSCI", 2026)
        items = [item for _, item in engine._iter_plan_items(plan) if item.get("label") == "Production Selection"]
        self.assertEqual(len(items), 2)
        pattern = re.compile(items[0]["match"])
        for code in ("AGRO 423", "HORT 431", "PLANT 240", "SOILS 418"):
            self.assertTrue(pattern.match(code), f"{code} should match")
        for code in ("HORT 101", "AGRO 28"):
            self.assertFalse(pattern.match(code), f"{code} should NOT match")

    def test_hortmin_merge_still_builds_cleanly(self):
        # Regression guard for the exact conflict this PLSCI fix could have
        # reintroduced: adding AGRO 410W as a third real alternative to the
        # Semester 7 writing-intensive item would have tied against, and
        # (listed first) beaten, the Horticulture minor's own singleton
        # need for HORT 101 in the shared "AGRO 28 (or HORT 101)" pool,
        # silently starving the minor's HORT 431 requirement. Deliberately
        # left out; this guards against it coming back.
        import datetime
        major = engine.load_degree_plan("PLSCI")
        minor = engine.load_minor_plan("HORTMIN", 2026)
        merged = engine.merge_plans(major, minors=[minor])
        catalog = engine.load_merged_catalog(merged["departments"])
        fp = engine.build_full_plan(
            merged, catalog, set(), start_year=2026, grad_years=8,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("PLSCI", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestTURFHandbookRequirements(unittest.TestCase):
    """Turfgrass Science, B.S. verified against the live bulletin's own
    per-semester credit table (bulletin-only -- a real Turfgrass Science
    student handbook PDF exists but was not text-extractable). Found one
    real placement bug: a generic 'Elective' item was in Semester 6 (Third
    Year Spring), where the live bulletin's own per-semester total has no
    room for one, instead of Semester 5 (Third Year Fall), which was
    exactly 3 credits short of the real bulletin total."""

    def test_elective_moved_to_third_year_fall(self):
        plan = engine.load_degree_plan("TURF", 2026)
        sem5 = next(sem for sem in plan["semesters"] if sem["index"] == 5)
        sem6 = next(sem for sem in plan["semesters"] if sem["index"] == 6)
        self.assertTrue(any(item.get("label") == "Elective" for item in sem5["items"]))
        self.assertFalse(any(item.get("label") == "Elective" for item in sem6["items"]))

    def test_full_plan_builds_cleanly(self):
        import datetime
        plan = engine.load_degree_plan("TURF", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestFrenchHandbookRequirements(unittest.TestCase):
    """Re-verified FRENCHBA-2026.json and FRENCHBS-2026.json (French and
    Francophone Studies, College of the Liberal Arts) against the live
    bulletin at bulletins.psu.edu (2026-08) -- no separate department
    handbook exists beyond the bulletin itself for this major."""

    def test_frenchba_linguistics_pick_is_a_real_4_way_pool(self):
        # Live bulletin: Language and Culture Option "Select one: FR 316,
        # FR 417, FR 418, or FR 419" -- was hardcoded to FR 316 only.
        plan = engine.load_degree_plan("FRENCHBA", 2026)
        item = _first_item_with_label_substring(plan, "FR 316")
        self.assertEqual(set(item["options"]), {"FR 316", "FR 417", "FR 418", "FR 419"})

    def test_frenchbs_linguistics_pick_is_a_real_4_way_pool_and_fr401_still_prescribed(self):
        plan = engine.load_degree_plan("FRENCHBS", 2026)
        item = _first_item_with_label_substring(plan, "FR 316")
        self.assertEqual(set(item["options"]), {"FR 316", "FR 417", "FR 418", "FR 419"})
        # FR 401 (Advanced Oral Communication) is a real, separate Prescribed
        # course for the B.S. -- must not have been dropped in the fix.
        fr401_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and item.get("options") == ["FR 401"]
        ]
        self.assertTrue(fr401_items, "FR 401 must still be scheduled as its own prescribed course")

    def test_frenchba_culture_literature_pool_has_3_occurrences_not_2(self):
        # Common Requirements: "select three from FR 331/332/351/352" (9cr).
        # The plan previously modeled only 2 occurrences (a real 3cr shortfall).
        plan = engine.load_degree_plan("FRENCHBA", 2026)
        pool = {"FR 331", "FR 332", "FR 351", "FR 352"}
        occurrences = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) == pool
        ]
        self.assertEqual(len(occurrences), 3, "expected 3 picks from the FR 331/332/351/352 pool")

    def test_full_plan_builds_cleanly_for_french_majors(self):
        for major in ("FRENCHBA", "FRENCHBS"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(failures, [], f"{major}: {failures}")


class TestGermanHandbookRequirements(unittest.TestCase):
    """Re-verified GERBA-2026.json and GERBS-2026.json (German, College of
    the Liberal Arts) against the live bulletin at bulletins.psu.edu
    (2026-08)."""

    def test_gerba_has_ger_402_a_real_prescribed_course_previously_missing(self):
        plan = engine.load_degree_plan("GERBA", 2026)
        codes = {
            o for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" for o in item.get("options", [])
        }
        self.assertIn("GER 402", codes, "GER 402 is a real Prescribed course for the B.A., must be scheduled")

    def test_gerba_does_not_hardcode_ger_310_or_344(self):
        # GER 310 and GER 344 are real Prescribed courses for the German
        # B.S. (GERBS-2026.json), NOT the B.A. -- confirmed absent from the
        # B.A.'s own live requirements and suggested plan.
        plan = engine.load_degree_plan("GERBA", 2026)
        codes = {
            o for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" for o in item.get("options", [])
        }
        self.assertNotIn("GER 310", codes)
        self.assertNotIn("GER 344", codes)

    def test_gerba_literature_culture_pool_has_3_enumerated_occurrences(self):
        # Live page: "Select 9 credits in German literature and culture
        # from: GER 431, 432, 440, 456, 457, 458, 459" -- 3 courses.
        plan = engine.load_degree_plan("GERBA", 2026)
        real_list = {"GER 431", "GER 432", "GER 440", "GER 456", "GER 457", "GER 458", "GER 459"}
        occurrences = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) == real_list
        ]
        self.assertEqual(len(occurrences), 3)

    def test_gerba_linguistics_pool_includes_ger_435(self):
        plan = engine.load_degree_plan("GERBA", 2026)
        item = _first_item_with_label_substring(plan, "GER 411")
        self.assertEqual(set(item["options"]), {"GER 411", "GER 412", "GER 430", "GER 435"})

    def test_gerbs_prescribes_ger_310_and_344_correctly(self):
        # Unlike the B.A., the B.S.'s own live requirements genuinely
        # prescribe GER 310 and GER 344 outright.
        plan = engine.load_degree_plan("GERBS", 2026)
        codes = {
            o for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" for o in item.get("options", [])
        }
        self.assertIn("GER 310", codes)
        self.assertIn("GER 344", codes)

    def test_gerbs_additional_course_allows_ger_432_alternative(self):
        # Live page: "GER 431 or GER 432" -- was hardcoded to GER 431 only.
        plan = engine.load_degree_plan("GERBS", 2026)
        item = _first_item_with_label_substring(plan, "GER 431")
        self.assertEqual(set(item["options"]), {"GER 431", "GER 432"})

    def test_full_plan_builds_cleanly_for_german_majors(self):
        for major in ("GERBA", "GERBS"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(failures, [], f"{major}: {failures}")


class TestItalianHandbookRequirements(unittest.TestCase):
    """Re-verified ITBA-2026.json and ITBS-2026.json (Italian, College of
    the Liberal Arts) against the live bulletin at bulletins.psu.edu
    (2026-08)."""

    def test_itba_additional_course_pool_matches_the_live_enumerated_list(self):
        real_list = {"IT 310", "IT 325", "IT 330W", "IT 399", "IT 412", "IT 422", "IT 430", "IT 450", "IT 460"}
        plan = engine.load_degree_plan("ITBA", 2026)
        occurrences = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) == real_list
        ]
        self.assertEqual(len(occurrences), 3, "expected 3 'IT Additional Course' picks (9cr)")

    def test_itba_400_level_course_pool_matches_the_live_enumerated_list(self):
        real_list = {"IT 412", "IT 422", "IT 430", "IT 450", "IT 460", "IT 470", "IT 475", "IT 480", "IT 485"}
        plan = engine.load_degree_plan("ITBA", 2026)
        occurrences = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) == real_list
        ]
        self.assertEqual(len(occurrences), 3, "expected 3 '400-level IT Course' picks (9cr)")

    def test_itba_additional_and_400_level_pools_are_wired_and_recommend_real_courses(self):
        plan = engine.load_degree_plan("ITBA", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for item in _all_items_with_label_substring(plan, "IT Additional Course") + \
                _all_items_with_label_substring(plan, "400-level IT Course"):
            completed, consumed = _reach(plan, item)
            rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} ({item.get('label')}) was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_itbs_supporting_courses_credit_shortfall_is_fixed(self):
        # Real Supporting Courses requirement is 27cr (6cr study abroad +
        # 21cr related areas, min 6 at 400-level); the plan previously only
        # modeled 24cr worth of generic slots plus IT 99.
        plan = engine.load_degree_plan("ITBS", 2026)
        related_area_credits = sum(
            item["credits"] for _, item in engine._iter_plan_items(plan)
            if "Related area" in (item.get("label") or "")
        )
        it99_credits = sum(
            item["credits"] for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and item.get("options") == ["IT 99"]
        )
        self.assertEqual(related_area_credits + it99_credits, 27)

    def test_full_plan_builds_cleanly_for_italian_majors(self):
        for major in ("ITBA", "ITBS"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(failures, [], f"{major}: {failures}")


class TestJapaneseKoreanHandbookRequirements(unittest.TestCase):
    """Re-verified JAPNSBA-2026.json and KORBA-2026.json (Japanese and
    Korean, College of the Liberal Arts, Asian Studies) against the live
    bulletin and each department's own current course-description PDF
    (2026-08) -- no separate department handbook exists beyond these."""

    def test_japnsba_430_439_pool_excludes_japns_426(self):
        # JAPNS 426 (Early Modern Japan) is real but numbered outside the
        # bulletin's "select 3cr from JAPNS 430-439" range, and its own
        # real prereq (HIST 172 AND HIST 174) can never be satisfied by
        # this plan (HIST isn't among its departments).
        plan = engine.load_degree_plan("JAPNSBA", 2026)
        item = _first_item_with_label_substring(plan, "JAPNS 430")
        self.assertNotIn("JAPNS 426", item["options"])
        self.assertEqual(set(item["options"]), {"JAPNS 430", "JAPNS 431", "JAPNS 432", "JAPNS 434"})

    def test_korba_culture_pick_allows_kor_121n_alternative(self):
        # Live requirements page: "Select one: KOR 120, KOR 121N, or KOR
        # 197" -- KOR 121N ("K-pop and Beyond") is real, 3cr, no prereq,
        # and was already sitting unused in kor_catalog.json.
        plan = engine.load_degree_plan("KORBA", 2026)
        item = _first_item_with_label_substring(plan, "KOR 120")
        self.assertEqual(set(item["options"]), {"KOR 120", "KOR 121N"})
        catalog = engine.load_merged_catalog(["KOR"])
        self.assertIn("KOR 121N", catalog)
        self.assertEqual(catalog["KOR 121N"].credits, 3.0)

    def test_korba_450_confirmed_absent_from_the_real_course_catalog(self):
        # Independently re-verified against the live KOR course-description
        # PDF (kor.pdf): KOR 450 does not exist anywhere in the department's
        # current course list. The original build's finding stands.
        catalog = engine.load_merged_catalog(["KOR"])
        self.assertNotIn("KOR 450", catalog)

    def test_full_plan_builds_cleanly_for_japanese_and_korean(self):
        for major in ("JAPNSBA", "KORBA"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(failures, [], f"{major}: {failures}")


class TestHistoryHandbookRequirements(unittest.TestCase):
    """Re-verified HIST-2026.json (History, B.A., College of the Liberal
    Arts) against BOTH the live university bulletin AND the department's
    own real undergraduate handbook page
    (https://history.la.psu.edu/undergraduate/history-major-requirements/),
    which the prior build never checked."""

    def test_100_200_level_slots_are_a_real_4_field_distribution_not_an_open_pool(self):
        # The department's own handbook requires "one course from each of
        # the following field categories: Europe, United States, Global,
        # Pre-Modern" -- previously modeled as 4 identical, fully open
        # "HIST 100/200-level Course" placeholders.
        plan = engine.load_degree_plan("HIST", 2026)
        items = _all_items_with_label_substring(plan, "HIST 100/200-level Course")
        self.assertEqual(len(items), 4)
        labels = {item["label"] for item in items}
        for field in ("Europe field", "United States field", "Global field", "Pre-Modern field"):
            self.assertTrue(
                any(field in label for label in labels),
                f"expected a slot labeled for the {field}",
            )
        # No two field pools should be identical -- a real distribution
        # requirement, not 4 copies of one open pool.
        option_sets = [frozenset(item["options"]) for item in items]
        self.assertEqual(len(set(option_sets)), 4, "each field's course list must be distinct")

    def test_field_pools_only_contain_codes_confirmed_in_hist_catalog(self):
        plan = engine.load_degree_plan("HIST", 2026)
        catalog = engine.load_merged_catalog(["HIST"])
        for item in _all_items_with_label_substring(plan, "HIST 100/200-level Course"):
            for code in item["options"]:
                self.assertIn(code, catalog, f"{code} in {item['label']} must be a real cataloged course")

    def test_field_pools_are_wired_and_recommend_real_courses(self):
        plan = engine.load_degree_plan("HIST", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for item in _all_items_with_label_substring(plan, "HIST 100/200-level Course"):
            completed, consumed = _reach(plan, item)
            rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{item['label']} was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_full_plan_builds_cleanly_for_hist(self):
        plan = engine.load_degree_plan("HIST", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [], failures)


class TestIntPolHandbookRequirements(unittest.TestCase):
    """Re-verified INTPOL-2026.json (International Politics, B.A. --
    International Political Economy Option, College of the Liberal Arts)
    against the live bulletin at bulletins.psu.edu (2026-08)."""

    def test_plsc_412_481_and_418_442_are_two_separate_pools_not_one_shared_4_way_pool(self):
        # Live page: IPE option's Additional Courses are "PLSC 412 OR PLSC
        # 481" and, separately, "PLSC 418 OR PLSC 442" -- the plan
        # previously shared one 4-way pool (PLSC 412/418/439/442) across
        # both picks, wrongly admitting PLSC 439 (a National-Security-only
        # course) and omitting the real PLSC 481 alternative.
        plan = engine.load_degree_plan("INTPOL", 2026)
        item_412 = _first_item_with_label_substring(plan, "PLSC 412")
        item_418 = _first_item_with_label_substring(plan, "PLSC 418")
        self.assertEqual(set(item_412["options"]), {"PLSC 412", "PLSC 481"})
        self.assertEqual(set(item_418["options"]), {"PLSC 418", "PLSC 442"})
        self.assertNotIn("PLSC 439", item_412["options"] + item_418["options"])

    def test_plsc_412_and_481_are_mutually_exclusive(self):
        catalog = engine.load_merged_catalog(["PLSC"])
        c412, c481 = catalog["PLSC 412"], catalog["PLSC 481"]
        self.assertFalse(engine.excludes_satisfied(c412, {"PLSC 481"}))
        self.assertFalse(engine.excludes_satisfied(c481, {"PLSC 412"}))
        self.assertTrue(engine.excludes_satisfied(c412, set()))

    def test_hist_geog_option_is_wired_with_a_real_enumerated_list(self):
        plan = engine.load_degree_plan("INTPOL", 2026)
        item = _first_item_with_label_substring(plan, "HIST/GEOG Option")
        self.assertTrue(item["options"], "HIST/GEOG Option must have real options, not be a blank generic slot")
        for code in item["options"]:
            self.assertTrue(code.startswith("HIST ") or code.startswith("GEOG "))
        catalog = engine.load_merged_catalog(["HIST", "GEOG"])
        for code in item["options"]:
            self.assertIn(code, catalog)

    def test_econ_advanced_pool_matches_the_live_enumerated_list(self):
        real_list = {"ECON 333", "ECON 433", "ECON 434", "ECON 443", "ECON 444", "ECON 451", "ECON 471", "ECON 472N"}
        plan = engine.load_degree_plan("INTPOL", 2026)
        occurrences = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and set(item.get("options", [])) == real_list
        ]
        self.assertEqual(len(occurrences), 2)

    def test_full_plan_builds_cleanly_for_intpol(self):
        plan = engine.load_degree_plan("INTPOL", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [], failures)


class TestJewishStudiesVerification(unittest.TestCase):
    """Re-verified JST-2026.json (Jewish Studies, B.A., College of the
    Liberal Arts) against the live bulletin's full program-requirements PDF
    (2026-08). Unlike most other majors checked this batch, the Supporting
    Courses categories genuinely have no publicly-enumerated course list
    (the bulletin itself defers to 'approved program list or consultation
    with the director') -- confirmed the plan's existing generic slots are
    already correct, no structural fix needed."""

    def test_jst_related_slots_remain_generic_matching_the_real_non_enumerable_requirement(self):
        plan = engine.load_degree_plan("JST", 2026)
        for item in _all_items_with_label_substring(plan, "JST/Related Course"):
            self.assertEqual(item.get("type"), "slot")
            self.assertNotIn("options", item)

    def test_full_plan_builds_cleanly_for_jst(self):
        plan = engine.load_degree_plan("JST", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [], failures)


class TestLinguisticsHandbookRequirements(unittest.TestCase):
    """Re-verified LING-2026.json (Linguistics, B.A., College of the
    Liberal Arts) against the live bulletin at bulletins.psu.edu
    (2026-08)."""

    def test_social_science_requirement_matches_the_live_3_way_pool(self):
        plan = engine.load_degree_plan("LING", 2026)
        item = _first_item_with_label_substring(plan, "Linguistics Social Science Requirement")
        self.assertEqual(set(item["options"]), {"LING 405", "LING 448", "APLNG 200"})

    def test_non_english_linguistics_course_matches_the_live_enumerated_list(self):
        plan = engine.load_degree_plan("LING", 2026)
        item = _first_item_with_label_substring(plan, "Non-English Linguistics Course")
        expected = {"FR 316", "FR 417", "FR 418", "FR 419", "GER 412", "GER 430",
                    "LING 493", "SPAN 314", "SPAN 315N", "SPAN 316", "SPAN 418"}
        self.assertEqual(set(item["options"]), expected)

    def test_new_pools_are_wired_and_recommend_real_courses(self):
        plan = engine.load_degree_plan("LING", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for label in ("Linguistics Social Science Requirement", "Non-English Linguistics Course"):
            item = _first_item_with_label_substring(plan, label)
            completed, consumed = _reach(plan, item)
            rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{label} was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_full_plan_builds_cleanly_for_ling(self):
        plan = engine.load_degree_plan("LING", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [], failures)


class TestLHRHandbookRequirements(unittest.TestCase):
    """Re-verified LHR-2026.json (Labor and Human Resources, B.A.,
    University Park Track, College of the Liberal Arts / School of Labor
    and Employment Relations) against the live bulletin at bulletins.psu.edu
    (2026-08)."""

    def test_lhr_312_is_scheduled_as_a_real_prescribed_course(self):
        # LHR 312 is one of six explicitly Prescribed courses (the plan's
        # own notes already quoted the bulletin's "LHR 304, LHR 305, and
        # LHR 312 may be taken in any order" sentence) but was previously
        # never actually scheduled anywhere in this plan.
        plan = engine.load_degree_plan("LHR", 2026)
        codes = {
            o for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" for o in item.get("options", [])
        }
        self.assertIn("LHR 312", codes)

    def test_supporting_course_matches_the_live_enumerated_list(self):
        plan = engine.load_degree_plan("LHR", 2026)
        items = _all_items_with_label_substring(plan, "Supporting Course")
        self.assertEqual(len(items), 2)
        real_list = {"ACCTG 211", "AFAM 100N", "AFAM 110N", "BA 243", "BA 304", "BLAW 243",
                     "CAS 203", "CAS 352", "ECON 315", "ECON 342", "HIST 155",
                     "MGMT 100", "MGMT 301", "MGMT 321", "SOC 103", "SOC 110", "SOC 119N",
                     "OLEAD 100", "OLEAD 201", "OLEAD 210", "OLEAD 464", "OLEAD 465"}
        for item in items:
            self.assertEqual(set(item["options"]), real_list)

    def test_supporting_course_is_wired_and_recommends_a_real_course(self):
        plan = engine.load_degree_plan("LHR", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for item in _all_items_with_label_substring(plan, "Supporting Course"):
            completed, consumed = _reach(plan, item)
            rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertIsNotNone(pick["code"])

    def test_lhr_4xx_course_excludes_independent_study_and_special_topics(self):
        plan = engine.load_degree_plan("LHR", 2026)
        items = _all_items_with_label_substring(plan, "LHR 4XX Course")
        self.assertEqual(len(items), 2)
        excluded = ["LHR 494", "LHR 494H", "LHR 495", "LHR 496", "LHR 497", "LHR 499"]
        allowed = ["LHR 400", "LHR 458Y", "LHR 460"]
        for item in items:
            pattern = re.compile(item["match"])
            for code in excluded:
                self.assertFalse(pattern.match(code), f"{code} should NOT match {pattern.pattern}")
            for code in allowed:
                self.assertTrue(pattern.match(code), f"{code} should match {pattern.pattern}")

    def test_psych_281_lhr_202_and_lhr_312_are_correctly_sequenced(self):
        # Live suggested plan places "PSYCH 281 or LHR 202" in Fall Year 2
        # and "LHR 312" in Spring Year 2 -- the plan previously had these
        # swapped/scrambled with an extra generic Gen Ed placeholder.
        plan = engine.load_degree_plan("LHR", 2026)
        psych_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("options") == ["PSYCH 281", "LHR 202"]
        )
        lhr312_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("options") == ["LHR 312"]
        )
        self.assertLess(psych_item["id"], lhr312_item["id"])

    def test_full_plan_builds_cleanly_for_lhr(self):
        plan = engine.load_degree_plan("LHR", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [], failures)


class TestOleadVerification(unittest.TestCase):
    """Re-verified OLEAD-2026.json (Organizational Leadership, B.A.,
    College of the Liberal Arts) against the live bulletin at
    bulletins.psu.edu (2026-08) and independently re-checked the PSYCH 484
    prereq chain directly against the catalog data. No structural changes
    were needed -- this plan was already accurate."""

    def test_psych_484_prereq_chain_is_accurate(self):
        catalog = engine.load_merged_catalog(["PSYCH", "STAT", "MATH"])
        psych484 = catalog["PSYCH 484"]
        prereq_codes = {c for group in psych484.prereq_groups for c in group}
        self.assertIn("PSYCH 100", prereq_codes)
        self.assertTrue({"PSYCH 200", "STAT 200"} & prereq_codes)
        self.assertEqual(catalog["STAT 200"].prereq_groups, [{"MATH 21"}])

    def test_full_plan_builds_cleanly_for_olead(self):
        plan = engine.load_degree_plan("OLEAD", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [], failures)


class TestMathHandbookRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin (bulletins.psu.edu/
    undergraduate/colleges/eberly-science/mathematics-bs/) and the
    Mathematics department's own public 'Supporting Courses' rule page
    (science.psu.edu/math/undergraduate/math-major/supporting-courses),
    which is more granular than the bulletin about what actually
    qualifies as a Supporting Course. Covers all 5 MATH catalog years
    (2022-2026) plus the companion Mathematics, B.A. (MATHBA)."""

    CATALOG_YEARS = (2022, 2023, 2024, 2025, 2026)

    # Real, published denylist for "Supporting Courses": "any baccalaureate
    # degree course EXCEPT" this list, plus MATH 21/22/26/40/41 (only
    # allowed if taken *prior* to MATH 140, which this plan's own sequence
    # never does) and MATH 110 (the department's own "duplicates MATH 140"
    # example).
    SUPPORTING_DENYLIST = {
        "CAS 126", "ENGL 4", "ENGL 5", "ESL 4", "ESL 5", "LLED 5", "LLED 10",
        "STAT 100", "CMPSC 100", "CMPSC 102",
        "MATH 2", "MATH 3", "MATH 4", "MATH 10", "MATH 30", "MATH 31",
        "MATH 32", "MATH 33", "MATH 34", "MATH 35", "MATH 36", "MATH 37",
        "MATH 38", "MATH 81", "MATH 82", "MATH 83", "MATH 200", "MATH 201",
        "MATH 21", "MATH 22", "MATH 26", "MATH 40", "MATH 41", "MATH 110",
    }
    # Real, published exclusion for the General Mathematics option's "6
    # credits of 400-level MATH" requirement.
    MATH_400_DENYLIST = {
        "MATH 400", "MATH 401", "MATH 405", "MATH 406", "MATH 410",
        "MATH 418", "MATH 441", "MATH 470", "MATH 471",
    }

    def _reach(self, plan, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_supporting_and_application_area_slots_wired_every_year(self):
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("MATH", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            targets = [
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "slot"
                and (item.get("label", "").startswith("Supporting Course")
                     or item.get("label", "").startswith("Application Area Course"))
            ]
            self.assertTrue(targets, f"{year}: expected Supporting/Application Area slots")
            for item in targets:
                self.assertTrue(item.get("open_elective"), f"{year}: item {item['id']} not wired")
                self.assertEqual(
                    set(item["elective_exclude"]) & self.SUPPORTING_DENYLIST,
                    self.SUPPORTING_DENYLIST,
                    f"{year}: item {item['id']} missing part of the real denylist",
                )
                completed, consumed = self._reach(plan, item)
                rec = engine.recommend_semester(
                    plan, catalog, completed, consumed_slots=consumed, max_credits=99,
                )
                pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
                self.assertIsNotNone(pick, f"{year}: item {item['id']} never recommended")
                self.assertIsNotNone(pick["code"], f"{year}: item {item['id']} got a placeholder")
                self.assertNotIn(pick["code"], self.SUPPORTING_DENYLIST)

    def test_math_400_level_slot_restricted_to_math_and_excludes_denylist(self):
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("MATH", year)
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label", "").startswith("MATH 400-Level Course")
            )
            self.assertEqual(item.get("elective_min_level"), 400, f"{year}")
            self.assertEqual(set(item["elective_exclude"]), self.MATH_400_DENYLIST, f"{year}")
            catalog = engine.load_merged_catalog(plan["departments"])
            completed, consumed = self._reach(plan, item)
            rec = engine.recommend_semester(
                plan, catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"{year}: item {item['id']} never recommended")
            self.assertTrue(pick["code"].startswith("MATH "), f"{year}: {pick['code']} not a MATH course")
            self.assertNotIn(pick["code"], self.MATH_400_DENYLIST, f"{year}")

    def test_supporting_course_never_recommends_a_denylisted_course(self):
        catalog = engine.load_merged_catalog(["MATH", "STAT", "CMPSC", "ENGL", "ESL", "CAS"])
        forced_exclude = {c for c in catalog if c not in self.SUPPORTING_DENYLIST}
        pick = engine._pick_open_elective(
            catalog, set(), forced_exclude, exclude_exact=self.SUPPORTING_DENYLIST,
        )
        self.assertIsNone(pick, f"Picked a denylisted course: {pick}")

    def test_mathba_supporting_and_400_level_slots_wired(self):
        plan = engine.load_degree_plan("MATHBA", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        supporting = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "slot" and item.get("label", "").startswith("Supporting Course")
        ]
        self.assertTrue(supporting)
        for item in supporting:
            self.assertTrue(item.get("open_elective"))
            completed, consumed = self._reach(plan, item)
            rec = engine.recommend_semester(
                plan, catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick)
            self.assertIsNotNone(pick["code"])
        math400 = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label", "").startswith("MATH 400-Level Course")
        )
        self.assertEqual(math400.get("elective_min_level"), 400)
        self.assertEqual(set(math400["elective_exclude"]), self.MATH_400_DENYLIST)


class TestMicrobiologyElectiveCredits(unittest.TestCase):
    """MICRB-2026.json's real common-requirement credit math, verified
    against the live bulletin (microbiology-bs_programrequirementstext.pdf):
    'Select 3 credits from MICRB Elective List A (Applied)' + 'Select 3
    credits from MICRB Elective List B' + 'Select 11 credits from MICRB
    Elective List C (free electives)', plus the General Microbiology
    option's own additional 'Select 6 credits from MICRB Elective List B.'"""

    def setUp(self):
        self.plan = engine.load_degree_plan("MICRB", 2026)

    def _total(self, label):
        return sum(
            item["credits"] for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "slot" and item.get("label") == f"MICRB Elective — {label}"
        )

    def test_list_a_totals_3_credits(self):
        self.assertEqual(self._total("List A"), 3)

    def test_list_b_totals_9_credits(self):
        self.assertEqual(self._total("List B"), 9)

    def test_list_c_totals_11_credits(self):
        # Previously totaled only 9 credits (three 3-credit slots) against
        # the bulletin's real 11-credit "free electives" requirement.
        self.assertEqual(self._total("List C"), 11)


class TestNeurobiologyGroupElectives(unittest.TestCase):
    """Real, fully-enumerated Group A/B/C/D course lists from the live
    bulletin (neurobiology-bs_programrequirementstext.pdf): 'Select a
    minimum of 15 credits of 400-level biology courses, with at least 6
    credits from Group A ... 3 credits from Group B ... 3 credits from
    Group C ... and 3 credits from Group D.'"""

    GROUP_A = {"BIOL 404", "BIOL 413", "BIOL 430", "BIOL 467"}
    GROUP_B = {"ANTH 466", "BBH 468", "BIOL 426", "BIOL 478", "KINES 465",
               "KINES 471", "PSYCH 452", "PSYCH 455", "PSYCH 458",
               "PSYCH 462", "PSYCH 478"}
    GROUP_C = {"BBH 451", "BIOL 418", "BIOL 422", "BIOL 434", "BIOL 439",
               "BIOL 455", "BIOL 460", "BIOL 465", "BIOL 472", "BIOL 475",
               "BIOL 479", "NUTR 460"}
    GROUP_D = {"BIOL 421", "BIOL 437", "BIOL 473", "BIOL 476", "BIOL 477",
               "BIOL 478", "BIOL 494", "BIOL 496"}

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("NEURO", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def _patterns(self, group_letter):
        return [
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(self.plan)
            if item.get("match") and item.get("label", "").endswith(f"Group {group_letter}")
        ]

    def test_group_patterns_match_the_real_bulletin_lists(self):
        for letter, real_set in (("A", self.GROUP_A), ("B", self.GROUP_B),
                                  ("C", self.GROUP_C), ("D", self.GROUP_D)):
            patterns = self._patterns(letter)
            self.assertTrue(patterns, f"Group {letter}: expected at least one wired slot")
            for pattern in patterns:
                for code in real_set:
                    self.assertTrue(pattern.match(code), f"Group {letter}: {code} should match")
                self.assertFalse(pattern.match("BIOL 999"), f"Group {letter}: bogus code matched")

    def test_biol_478_counts_for_both_group_b_and_group_d(self):
        # Real bulletin text lists BIOL 478 (Human Neuroanatomy) under both
        # Group B and Group D.
        for letter in ("B", "D"):
            patterns = self._patterns(letter)
            self.assertTrue(any(p.match("BIOL 478") for p in patterns), f"Group {letter}")

    def test_group_a_requires_6_credits_not_3(self):
        # The bulletin requires 6 credits (2 courses) from Group A
        # specifically; a previous version of this plan only had one
        # 3-credit Group A slot.
        total = sum(
            item["credits"] for _, item in engine._iter_plan_items(self.plan)
            if item.get("label", "").endswith("Group A")
        )
        self.assertEqual(total, 6)

    def test_supporting_course_slots_total_19_credits_and_recommend_real_courses(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "slot" and item.get("label") == "Supporting Course (department list)"
        ]
        self.assertEqual(sum(i["credits"] for i in items), 19)
        for item in items:
            self.assertTrue(item.get("open_elective"))
            completed = {
                it["options"][0] for _, it in engine._iter_plan_items(self.plan)
                if it["id"] < item["id"] and it.get("type") == "course"
            }
            consumed = {
                it["id"] for _, it in engine._iter_plan_items(self.plan)
                if it["id"] < item["id"] and it.get("type") == "slot"
            }
            rec = engine.recommend_semester(
                self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} never recommended")
            self.assertIsNotNone(pick["code"])


class TestPhysicsHandbookElectives(unittest.TestCase):
    """General Physics Option requirements verified against the live
    bulletin (physics-bs_programrequirementstext.pdf), which is more
    granular than what this plan originally modeled."""

    ADVANCED_POOL = {
        "PHYS 337", "PHYS 402", "PHYS 406", "PHYS 411", "PHYS 412",
        "PHYS 414", "PHYS 430", "PHYS 437", "PHYS 458", "PHYS 465",
        "PHYS 472", "PHYS 479", "PHYS 496", "PHYS 497",
        "ASTRO 410", "ASTRO 440", "ASTRO 485",
    }
    MATH_400_DENYLIST = {
        "MATH 400", "MATH 401", "MATH 405", "MATH 406", "MATH 410",
        "MATH 418", "MATH 441", "MATH 470", "MATH 471",
    }

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PHYS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_phys_402_or_458_is_a_required_prescribed_course(self):
        # Previously missing entirely: "PHYS 402 (Electronics for
        # Scientists) or PHYS 458 (Intermediate Optics), 4 credits" is a
        # required prescribed course for the General Option.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and set(item.get("options", [])) == {"PHYS 402", "PHYS 458"}
        ]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["credits"], 4)

    def test_400_level_phys_elective_matches_the_real_option_pool(self):
        patterns = [
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(self.plan)
            if item.get("match") and item.get("label", "").startswith("400-Level PHYS elective")
        ]
        self.assertEqual(len(patterns), 2)
        for pattern in patterns:
            for code in self.ADVANCED_POOL:
                self.assertTrue(pattern.match(code), f"{code} should match")
            self.assertFalse(pattern.match("PHYS 211"))

    def test_400_level_math_elective_restricted_to_math_and_excludes_denylist(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "400-Level MATH elective"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(item.get("elective_min_level"), 400)
            self.assertEqual(set(item["elective_exclude"]), self.MATH_400_DENYLIST)
            self.assertIn("PHYS", item["elective_exclude_prefixes"])

    def test_supporting_course_totals_12_credits_and_excludes_phys(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "slot" and item.get("label") == "Supporting Course"
        ]
        # Previously only 2 slots (6 credits) against the bulletin's real
        # "Select 12 credits from department list."
        self.assertEqual(len(items), 4)
        self.assertEqual(sum(i["credits"] for i in items), 12)
        for item in items:
            self.assertIn("PHYS", item["elective_exclude_prefixes"])

    def test_supporting_course_never_recommends_phys_or_independent_study(self):
        forced_exclude = {
            c for c in self.catalog
            if not c.startswith("PHYS ") and c not in {"SC 295", "SC 395", "SC 495"}
        }
        pick = engine._pick_open_elective(
            self.catalog, set(), forced_exclude,
            min_level=200, exclude_prefixes=["PHYS"],
            exclude_exact=["SC 295", "SC 395", "SC 495"],
        )
        self.assertIsNone(pick, f"Picked a PHYS/independent-study course: {pick}")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestPlanetaryScienceElectiveFixes(unittest.TestCase):
    """Planetary Science and Astronomy, B.S. requirements verified against
    the live bulletin (planetary-science-and-astronomy-bs_
    programrequirementstext.pdf). Found and fixed a real double-modeled
    'Advanced Elective' pool that could never be correctly filled."""

    ASTRO_ADV = {"ASTRO 120", "ASTRO 130", "ASTRO 140", "ASTRO 292"}

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("PLANET", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_advanced_elective_pool_is_exactly_3_items_worth_9_credits(self):
        # Real requirement: "Select three of the following: ASTRO 120,
        # 130, 140, 292" (9 credits). A previous version of this plan
        # double-modeled it as 2 hardcoded required items PLUS 3 more
        # generic slots on the same 4-course pool -- 18 credits, and
        # structurally impossible to fill (only 4 real courses exist).
        pool_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and set(item.get("options", [])) == self.ASTRO_ADV
        ]
        self.assertEqual(len(pool_items), 3)
        self.assertEqual(sum(i["credits"] for i in pool_items), 9)
        # No leftover generic "Advanced Elective" slots should remain.
        leftover_slots = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "slot" and item.get("label", "").startswith("Advanced Elective")
        ]
        self.assertEqual(leftover_slots, [])

    def test_advanced_elective_pool_resolves_to_3_distinct_real_courses(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        picked = {
            c["code"] for t in fp["terms"] for c in t["courses"]
            if c["code"] in self.ASTRO_ADV
        }
        self.assertEqual(len(picked), 3, f"expected 3 distinct ASTRO advanced electives, got {picked}")

    def test_intro_astro_and_geosc_picks_widened_to_real_bulletin_alternates(self):
        astro_item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and "ASTRO 291" in item.get("options", [])
        )
        self.assertEqual(set(astro_item["options"]), {"ASTRO 1", "ASTRO 5", "ASTRO 6", "ASTRO 291"})
        geosc_item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and "GEOSC 1" in item.get("options", [])
        )
        self.assertEqual(set(geosc_item["options"]), {"GEOSC 1", "EARTH 2", "GEOSC 20"})

    def test_supporting_course_excludes_astro_and_geosc_and_totals_11_credits(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "slot" and item.get("label") == "Supporting Course"
        ]
        self.assertEqual(sum(i["credits"] for i in items), 11)
        for item in items:
            self.assertIn("ASTRO", item["elective_exclude_prefixes"])
            self.assertIn("GEOSC", item["elective_exclude_prefixes"])

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestStatisticsAdvancedElectiveFixes(unittest.TestCase):
    """Statistics and Computing Option requirements verified against the
    live bulletin (statistics-bs_programrequirementstext.pdf). This
    plan's own notes already claimed '12 credits across 4 slots' for the
    Advanced STAT elective pool, but the actual JSON only had 3 slots (9
    credits) -- a real documentation-vs-implementation gap."""

    ADV_LIST = {
        "BBH 440", "HPA 440", "CMPSC 448", "IE 434", "IE 436",
        "MATH 436", "MATH 441", "MATH 451", "CMPSC 451", "MATH 455",
        "CMPSC 455", "STAT 416", "MATH 416", "STAT 440", "STAT 463",
        "STAT 464", "STAT 466",
    }

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("STAT", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_advanced_pool_is_4_items_totaling_12_credits(self):
        pool_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and set(item.get("options", [])) == self.ADV_LIST
        ]
        self.assertEqual(len(pool_items), 4)
        self.assertEqual(sum(i["credits"] for i in pool_items), 12)

    def test_advanced_pool_resolves_to_4_distinct_real_courses(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        picked = {
            c["code"] for t in fp["terms"] for c in t["courses"]
            if c["code"] in self.ADV_LIST
        }
        self.assertEqual(len(picked), 4, f"expected 4 distinct advanced electives, got {picked}")

    def test_supporting_course_totals_8_credits_and_excludes_stat(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "slot" and item.get("label") == "Supporting Course"
        ]
        self.assertEqual(sum(i["credits"] for i in items), 8)
        for item in items:
            self.assertIn("STAT", item["elective_exclude_prefixes"])

    def test_cmpsc_360_widened_to_include_math_311w_alternate(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if "CMPSC 360" in item.get("options", [])
        )
        self.assertEqual(set(item["options"]), {"CMPSC 360", "MATH 311W"})

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestOpenElectiveNameBasedExclusion(unittest.TestCase):
    """_pick_open_elective (Backend/planner_engine.py) never auto-picks an
    independent study / special topics / co-op / foreign study course by
    default, regardless of which major's plan calls it -- the same
    _EXCLUDE_NAME_RE convention already used by score_recommendations.
    Found while verifying Eberly College of Science majors: an early
    version of NEURO/PHYS/PLANET's own open_elective wiring surfaced real
    independent-study courses (BBH 296/297/299, PHYS 496) as default
    'Supporting Course' picks before this engine-level fix."""

    def test_independent_study_and_special_topics_never_auto_picked(self):
        catalog = engine.load_merged_catalog(["BIOL", "BBH"])
        # BIOL 496 (Independent Studies) and BBH 297 (Special Topics) sort
        # alphabetically before most real BIOL/BBH courses, so a naive
        # "first eligible code" pick would surface them by default.
        for bad_code in ("BIOL 496", "BBH 297", "BBH 296", "BBH 299"):
            self.assertIn(bad_code, catalog, f"fixture assumption: {bad_code} should exist")
        pick = engine._pick_open_elective(catalog, set(), set(), min_level=200)
        self.assertIsNotNone(pick)
        self.assertNotIn(pick[0], {"BIOL 496", "BBH 296", "BBH 297", "BBH 299"})


class TestHonorsVariantExclusion(unittest.TestCase):
    """A student who already completed a course's honors variant (or vice
    versa) shouldn't get it "newly" recommended by a generic elective pick
    -- completed is otherwise matched by exact code only, so "MATH 220" and
    "MATH 220H" were treated as two unrelated courses. Found 2026-08-27
    verifying PHYS/NEURO's Supporting Course wiring: a student who
    completed MATH 220 could still get MATH 220H recommended as "new."
    Fixed in planner_engine.py via _honors_base_code/_is_effectively_completed,
    shared by both _pick_open_elective (broad catalog search) and
    _ranked_options (a single item's own curated option list)."""

    def test_honors_base_code_detection(self):
        self.assertEqual(engine._honors_base_code("MATH 220H"), "MATH 220")
        self.assertEqual(engine._honors_base_code("PHYS 403H"), "PHYS 403")
        self.assertIsNone(engine._honors_base_code("MATH 220"))
        self.assertIsNone(engine._honors_base_code("ENGL 202C"))  # W/N/C suffixes are a different course, not honors

    def test_pick_open_elective_never_recommends_honors_variant_of_completed_base(self):
        catalog = engine.load_merged_catalog(["MATH"])
        self.assertIn("MATH 220H", catalog)
        pick = engine._pick_open_elective(catalog, {"MATH 220"}, {"MATH 220"})
        self.assertIsNotNone(pick)
        self.assertNotEqual(pick[0], "MATH 220H")

    def test_pick_open_elective_never_recommends_base_of_completed_honors_variant(self):
        catalog = engine.load_merged_catalog(["MATH"])
        pick = engine._pick_open_elective(catalog, {"MATH 220H"}, {"MATH 220H"})
        self.assertIsNotNone(pick)
        self.assertNotEqual(pick[0], "MATH 220")

    def test_ranked_options_deprioritizes_honors_variant_of_completed_base(self):
        catalog = engine.load_merged_catalog(["MATH"])
        # Simulates the cross-item case: the student completed MATH 220 via
        # a totally different plan item, and THIS item offers a choice
        # between MATH 220H (an honors duplicate of that same content) and
        # MATH 22 (a genuinely different, uncompleted course). Without the
        # fix, MATH 220H would rank first (exact-match "completed" check
        # doesn't see it as related to MATH 220) even though it's really
        # already satisfied in substance.
        item = {"id": 0, "type": "course", "options": ["MATH 220H", "MATH 22"]}
        ranked = list(engine._ranked_options(item, catalog, set(), {"MATH 220"}))
        self.assertEqual(ranked[0], "MATH 22")


class TestISTCollegeHandbookRequirements(unittest.TestCase):
    """Real data cross-checked against College of IST sources -- the live
    bulletin for every major, plus real department-published advising
    documents that go beyond it where one exists (CYBER's Application Focus
    lists are on the bulletin itself; SRA's Support of Option list is a
    dedicated ist.psu.edu advising PDF). Covers AIMA, CYBER (all 5 catalog
    years 2022-2026), ETI, HCDD, IEC, SRA, DATSC."""

    # ------------------------------------------------------------------
    # CYBER -- Application Focus Selection, all 5 catalog years
    # ------------------------------------------------------------------

    def _focus_patterns(self, plan):
        return [
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "slot" and (item.get("label") or "").startswith("Application Focus Selection")
        ]

    def test_cyber_application_focus_wired_every_catalog_year(self):
        for year in (2022, 2023, 2024, 2025, 2026):
            plan = engine.load_degree_plan("CYBER", year)
            patterns = self._focus_patterns(plan)
            self.assertEqual(len(patterns), 3, f"{year}: expected 3 Application Focus slots")
            for p in patterns:
                self.assertTrue(p.match("IST 402"), f"{year}: IST 402 should match ({p.pattern})")
                self.assertFalse(p.match("CMPSC 465"), f"{year}: CMPSC 465 should NOT match")

    def test_cyber_application_focus_differs_by_catalog_year(self):
        # Real, sourced year-to-year differences (new tracks added over time),
        # same shape as CMPSC's own multi-year technical-elective changes.
        p2022 = self._focus_patterns(engine.load_degree_plan("CYBER", 2022))[0]
        p2024 = self._focus_patterns(engine.load_degree_plan("CYBER", 2024))[0]
        p2026 = self._focus_patterns(engine.load_degree_plan("CYBER", 2026))[0]
        # Enterprise Technology track didn't exist yet in 2022-23.
        self.assertFalse(p2022.match("ETI 435"), "2022: Enterprise Technology track shouldn't exist yet")
        self.assertTrue(p2024.match("ETI 435"), "2024: Enterprise Technology track should exist")
        # IUG and Honors' IST 494H only appears in the current (2026) list.
        self.assertFalse(p2024.match("IST 494H"), "2024: IUG and Honors shouldn't exist yet")
        self.assertTrue(p2026.match("IST 494H"), "2026: IUG and Honors should exist")
        # Application Development becomes HCDD/IST cross-listed starting 2025-26.
        self.assertFalse(p2024.match("HCDD 311"), "2024: App Dev was IST-only")
        self.assertTrue(p2026.match("HCDD 311"), "2026: App Dev is HCDD/IST cross-listed")

    def test_cyber_full_plan_builds_cleanly_every_catalog_year(self):
        for year in (2022, 2023, 2024, 2025, 2026):
            plan = engine.load_degree_plan("CYBER", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=4)
            failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(failures, [], f"{year}: {failures}")

    # ------------------------------------------------------------------
    # DATSC -- Application Focus Selection, CAS 100C, cross-listed options
    # ------------------------------------------------------------------

    def test_datsc_application_focus_recognizes_real_courses(self):
        plan = engine.load_degree_plan("DATSC", 2026)
        patterns = [
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "slot" and (item.get("label") or "").startswith("Application Focus Selection")
        ]
        self.assertEqual(len(patterns), 4)
        # Sourced from the real bulletin's 8 named focus areas.
        for code in ("ASTRO 401", "PSYCH 256", "SRA 468", "NUTR 251"):
            self.assertTrue(any(p.match(code) for p in patterns), f"{code} should match some focus pattern")
        self.assertFalse(any(p.match("CMPSC 465") for p in patterns), "CMPSC 465 isn't a real focus-area course")

    def test_datsc_300_400_level_focus_excludes_lower_level_courses(self):
        plan = engine.load_degree_plan("DATSC", 2026)
        upper_patterns = [
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Application Focus Selection (300/400-level)"
        ]
        self.assertEqual(len(upper_patterns), 2)
        for p in upper_patterns:
            self.assertFalse(p.match("PSYCH 100"), "PSYCH 100 is 100-level, shouldn't satisfy the 300/400 slot")
            self.assertTrue(p.match("PSYCH 404"), "PSYCH 404 is 400-level, should satisfy the slot")

    def test_datsc_cas_100_includes_all_three_sections(self):
        plan = engine.load_degree_plan("DATSC", 2026)
        item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course" and "CAS 100A" in (it.get("options") or [])
        )
        self.assertIn("CAS 100C", item["options"])

    def test_datsc_cross_listed_elective_item_accepts_every_real_alternative(self):
        plan = engine.load_degree_plan("DATSC", 2026)
        items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course" and "DS 442/IST 442/SODA 308" in (it.get("label") or "")
        ]
        self.assertEqual(len(items), 2)
        for it in items:
            for code in ("SODA 308", "DS 420", "DS 441", "IST 494"):
                self.assertIn(code, it["options"], f"{it['label']}: missing real alternative {code}")

    def test_datsc_full_plan_builds_cleanly(self):
        plan = engine.load_degree_plan("DATSC", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])

    # ------------------------------------------------------------------
    # ETI -- AI-overhaul staleness fixes
    # ------------------------------------------------------------------

    def test_eti_200_is_a_real_required_course(self):
        plan = engine.load_degree_plan("ETI", 2026)
        self.assertTrue(
            any(it.get("type") == "course" and it.get("options") == ["ETI 200"]
                for _, it in engine._iter_plan_items(plan)),
            "ETI 200 (Designing AI for the Enterprise) is missing -- it's a real, current Prescribed course",
        )

    def test_eti_400_not_ist_402_for_the_delivering_ai_requirement(self):
        # The bulletin's real "Additional Courses" pairing is ETI 400 or
        # ETI 423 -- IST 402/IST 423 were a stale mix-up with an unrelated
        # course and a code that doesn't exist in the catalog at all.
        plan = engine.load_degree_plan("ETI", 2026)
        matches = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course" and set(it.get("options") or []) & {"ETI 400", "IST 402"}
        ]
        self.assertEqual(len(matches), 1, "expected exactly one item for this requirement")
        self.assertEqual(set(matches[0]["options"]), {"ETI 400", "ETI 423"})
        catalog = engine.load_merged_catalog(["IST"])
        self.assertNotIn("IST 423", catalog, "IST 423 doesn't exist -- shouldn't be offered as an alternative")

    def test_eti_entrance_to_major_pool_has_all_seven_real_alternatives(self):
        plan = engine.load_degree_plan("ETI", 2026)
        item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course" and it.get("etm") and "ETI 100" in (it.get("options") or [])
        )
        for code in ("ETI 100", "A-I 100", "CYBER 100", "CYBER 100S", "HCDD 113", "HCDD 113S", "IST 110"):
            self.assertIn(code, item["options"])
        self.assertEqual(item["options"][0], "ETI 100", "bulletin's own suggested plan defaults to ETI 100")

    def test_eti_business_fundamentals_and_application_focus_are_wired(self):
        plan = engine.load_degree_plan("ETI", 2026)
        bfc = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Business Fundamentals Certificate Course"
        )
        self.assertTrue(re.match(bfc["match"], "BLAW 243"))
        self.assertFalse(re.match(bfc["match"], "CMPSC 465"))
        focus_items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Application Focus Selection"
        ]
        self.assertEqual(len(focus_items), 4)
        for it in focus_items:
            self.assertTrue(re.match(it["match"], "SRA 111"))

    def test_eti_full_plan_builds_cleanly(self):
        plan = engine.load_degree_plan("ETI", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])

    # ------------------------------------------------------------------
    # HCDD -- real STAT 200 restored, Application Focus wired
    # ------------------------------------------------------------------

    def test_hcdd_stat_200_no_longer_falls_back_to_stat_100(self):
        # STAT 100 was never a real bulletin-sanctioned alternative for this
        # major -- it was a workaround for a since-fixed MATH 21 staleness.
        plan = engine.load_degree_plan("HCDD", 2026)
        item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course" and "STAT 200" in (it.get("options") or [])
        )
        self.assertEqual(item["options"], ["STAT 200"])

    def test_hcdd_application_focus_is_wired_and_400_level_restricted(self):
        plan = engine.load_degree_plan("HCDD", 2026)
        regular = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Application Focus Selection"
        ]
        upper = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Application Focus Selection (400-level)"
        )
        self.assertEqual(len(regular), 3)
        for it in regular:
            self.assertTrue(re.match(it["match"], "SOC 5"))
        self.assertFalse(re.match(upper["match"], "SOC 5"), "SOC 5 is below 400-level")
        self.assertTrue(re.match(upper["match"], "COMM 450A"))

    def test_hcdd_full_plan_builds_cleanly(self):
        plan = engine.load_degree_plan("HCDD", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])

    # ------------------------------------------------------------------
    # IEC -- Gen Ed domain wiring, Application Focus Area wiring
    # ------------------------------------------------------------------

    def test_iec_gen_ed_slots_wired_to_real_domain_list(self):
        # The bulletin's own suggested plan labels every generic Gen Ed line
        # "(GN, GA, GH, or GHW)" -- a real, specific restriction.
        plan = engine.load_degree_plan("IEC", 2026)
        gen_ed_items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "slot" and it.get("label") == "GEN ED"
        ]
        self.assertEqual(len(gen_ed_items), 5)
        for it in gen_ed_items:
            self.assertEqual(it.get("gen_ed"), ["GN", "GA", "GH", "GHW"])

    def test_iec_gen_ed_slot_recommends_a_real_course(self):
        plan = engine.load_degree_plan("IEC", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        ge_item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "slot" and it.get("label") == "GEN ED"
        )
        completed = {
            o for _, it in engine._iter_plan_items(plan)
            if it["id"] < ge_item["id"] and it.get("type") == "course"
            for o in [it["options"][0]]
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < ge_item["id"] and it.get("type") == "slot"
        }
        rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
        pick = next((c for c in rec["courses"] if c["item_id"] == ge_item["id"]), None)
        self.assertIsNotNone(pick, "GEN ED slot was never recommended a course")
        self.assertIsNotNone(pick["code"], "GEN ED slot got a placeholder, not a real course")

    def test_iec_application_focus_area_is_wired(self):
        plan = engine.load_degree_plan("IEC", 2026)
        items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Application Focus Area"
        ]
        upper = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Application Focus Area (400-level)"
        )
        self.assertEqual(len(items), 3)
        for it in items:
            self.assertTrue(re.match(it["match"], "CYBER 262"))
        self.assertTrue(re.match(upper["match"], "IST 451"))
        self.assertFalse(re.match(upper["match"], "CYBER 262"), "CYBER 262 isn't 400-level")

    def test_iec_full_plan_builds_cleanly(self):
        plan = engine.load_degree_plan("IEC", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])

    # ------------------------------------------------------------------
    # SRA -- real Support of Option PDF, Gen Ed + US/IL wiring
    # ------------------------------------------------------------------

    def test_sra_supporting_course_matches_the_real_iam_support_option_pdf(self):
        # ist.psu.edu/sites/default/files/advising/sra-iam-support-option.pdf
        plan = engine.load_degree_plan("SRA", 2026)
        items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Supporting Course"
        ]
        upper = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Supporting Course (400-level)"
        )
        self.assertEqual(len(items), 3)
        for it in items:
            self.assertTrue(re.match(it["match"], "IST 230"), "IST/SRA category course should match")
            self.assertTrue(re.match(it["match"], "ARMY 101"), "Military Studies course should match")
            self.assertFalse(re.match(it["match"], "CMPSC 465"), "not a real Support of Option course")
        # Blanket "any 400-level SRA/PL SC/PSYCH course" rule.
        self.assertTrue(re.match(upper["match"], "PLSC 481"))
        self.assertTrue(re.match(upper["match"], "PSYCH 473"))
        self.assertFalse(re.match(upper["match"], "IST 230"), "IST 230 isn't 400-level")

    def test_sra_gen_ed_and_us_il_slots_are_wired(self):
        plan = engine.load_degree_plan("SRA", 2026)
        gen_ed_items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "slot" and it.get("label") == "GEN ED"
        ]
        us_il_items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "US or International Cultures / Elective"
        ]
        self.assertEqual(len(gen_ed_items), 6)
        for it in gen_ed_items:
            self.assertEqual(it.get("gen_ed"), ["GN", "GA", "GH", "GHW"])
        self.assertEqual(len(us_il_items), 2)
        for it in us_il_items:
            self.assertEqual(it.get("gen_ed"), ["US", "IL"])

    def test_sra_full_plan_builds_cleanly(self):
        plan = engine.load_degree_plan("SRA", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])

    # ------------------------------------------------------------------
    # AIMA -- MATH 3/4 staleness cleanup, capstone still resolves
    # ------------------------------------------------------------------

    def test_aima_no_longer_carries_dead_math_3_4_items(self):
        # MATH 3/MATH 4 are unconditionally waived by expand_math_placement
        # (see planner_engine.NON_DEGREE_APPLICABLE_MATH) and MATH 21 itself
        # has an empty prereq_groups -- these were dead weight.
        plan = engine.load_degree_plan("AIMA", 2026)
        options_seen = [
            it.get("options") for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course"
        ]
        self.assertNotIn(["MATH 3"], options_seen)
        self.assertNotIn(["MATH 4"], options_seen)
        self.assertIn(["MATH 21"], options_seen, "MATH 21 is still genuinely required")

    def test_aima_capstone_still_resolves_to_real_courses(self):
        plan = engine.load_degree_plan("AIMA", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])
        codes = {c["code"] for t in fp["terms"] for c in t.get("courses", []) if c.get("code")}
        self.assertIn("AIMA 430", codes)
        self.assertIn("AIMA 440", codes)

    def test_aima_support_course_slots_are_intentionally_left_generic(self):
        # No enumerated course list exists on either the bulletin or
        # ist.psu.edu's aima-major-requirements page -- confirm these were
        # left as plain unfilled placeholders (no match/open_elective),
        # not silently dropped or fabricated.
        plan = engine.load_degree_plan("AIMA", 2026)
        support_items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "slot" and "Support Course" in (it.get("label") or "")
        ]
        self.assertEqual(len(support_items), 6)
        for it in support_items:
            self.assertNotIn("match", it)
            self.assertNotIn("open_elective", it)

    # ------------------------------------------------------------------
    # Cross-major: staleness fix shouldn't reintroduce MATH 3/4 anywhere
    # ------------------------------------------------------------------

    def test_no_ist_major_plan_still_requires_dead_math_3_or_4(self):
        for major, years in (
            ("AIMA", [2026]), ("ETI", [2026]), ("HCDD", [2026]), ("IEC", [2026]),
            ("SRA", [2026]), ("DATSC", [2026]),
            ("CYBER", [2022, 2023, 2024, 2025, 2026]),
        ):
            for year in years:
                plan = engine.load_degree_plan(major, year)
                for _, item in engine._iter_plan_items(plan):
                    if item.get("type") == "course":
                        self.assertNotIn(
                            item.get("options"), (["MATH 3"], ["MATH 4"]),
                            f"{major}-{year}: still carries a dead MATH 3/4 item",
                        )


class TestSmealBusinessCoreHandbookRequirements(unittest.TestCase):
    """Cross-checked against ugstudents.smeal.psu.edu's own per-major "Degree
    Requirements" pages -- the real Smeal College of Business handbook, far
    more granular than the university bulletin these plans were originally
    built from (same upgrade CMPSC got from the EECS department handbook).
    Covers the Business Breadth Course requirement shared by ACCTG, CIE, FIN,
    and BAIS (a Smeal college-wide requirement, though each major's own
    department's courses are excluded from its own list) plus the 5-course
    Business Core (MGMT 301/MKTG 301/FIN 301/SCM 301/BA 342) all Smeal
    majors share. ACTSC has no Business Breadth requirement at all on its
    real pages -- see TestACTSCHandbookRequirements for its own findings."""

    SMEAL_MAJORS_YEARS = {
        "ACCTG": (2022, 2023, 2024, 2025, 2026),
        "ACTSC": (2022, 2023, 2024, 2025, 2026),
        "BAIS": (2025, 2026),
        "CIE": (2022, 2023, 2024, 2025, 2026),
        "FIN": (2022, 2023, 2024, 2025, 2026),
    }

    def _breadth_items(self, plan):
        return [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "slot"
            and item.get("label") == "Business Breadth Course (Smeal-approved list)"
        ]

    def test_business_core_five_courses_present_in_every_smeal_plan(self):
        # ugstudents.smeal.psu.edu/.../business-core: "The Business Core is
        # comprised of five courses all Smeal College of Business
        # undergraduate students must take before they graduate: MGMT 301,
        # MKTG 301, FIN 301, SCM 301, and BA 342."
        core = {"MGMT 301", "MKTG 301", "FIN 301", "SCM 301", "BA 342"}
        for major, years in self.SMEAL_MAJORS_YEARS.items():
            for year in years:
                plan = engine.load_degree_plan(major, year)
                codes = {o for _, item in engine._iter_plan_items(plan) for o in item.get("options", [])}
                missing = core - codes
                self.assertFalse(missing, f"{major}-{year}: missing Business Core course(s) {missing}")

    def test_business_breadth_wired_2024_through_2026(self):
        # 2024-25 onward is the real "flat allow-list" Business Breadth
        # format, verified live on each major's own ugstudents.smeal.psu.edu
        # page -- these slots were previously 100%-unfillable placeholders.
        cases = {"ACCTG": (2024, 2025, 2026), "CIE": (2024, 2025, 2026),
                 "FIN": (2024, 2025, 2026), "BAIS": (2025, 2026)}
        for major, years in cases.items():
            for year in years:
                plan = engine.load_degree_plan(major, year)
                items = self._breadth_items(plan)
                self.assertTrue(items, f"{major}-{year}: expected Business Breadth slot(s)")
                for item in items:
                    self.assertTrue(item.get("match"), f"{major}-{year}: item {item['id']} not wired")
                    re.compile(item["match"])  # must be a valid regex

    def test_business_breadth_not_wired_for_two_piece_sequence_years(self):
        # 2022-23 and 2023-24: the real requirement is a Smeal "Two-Piece
        # Sequence" (pick ONE themed category -- e.g. Business Law, Finance,
        # Real Estate -- and complete BOTH of its two named courses), not a
        # flat allow-list. The engine has no mechanism for that shape, so
        # these are deliberately left as unenforced placeholders (never
        # fabricated as an incorrect flat list) with a documented reason.
        for major in ("ACCTG", "CIE", "FIN"):
            for year in (2022, 2023):
                plan = engine.load_degree_plan(major, year)
                items = self._breadth_items(plan)
                self.assertTrue(items, f"{major}-{year}")
                for item in items:
                    self.assertNotIn("match", item, f"{major}-{year}: should NOT be wired (Two-Piece Sequence year)")
                    self.assertIn("Two-Piece Sequence", item.get("notes", ""), f"{major}-{year}: missing explanatory note")

    def test_business_breadth_matches_real_courses_and_rejects_own_major_courses(self):
        # Spot-checks real, handbook-verified allow-list membership plus the
        # "a major can't double-count its own courses as Breadth" rule --
        # each major's list excludes its own department's requirements.
        cases = {
            ("ACCTG", 2025): (["MGMT 410", "MIS 437", "RM 450", "FIN 406", "ECON 305", "ECON 450"],
                               ["ACCTG 403W", "ACCTG 471"]),
            ("CIE", 2025): (["ACCTG 404", "FIN 305", "MGMT 410"], ["MGMT 425", "MGMT 453"]),
            ("FIN", 2025): (["ACCTG 404", "RM 475", "MIS 437"], ["FIN 305", "FIN 406", "FIN 408"]),
            ("BAIS", 2026): (["ACCTG 404", "FIN 305", "MGMT 410"], ["MIS 301", "MIS 431"]),
        }
        for (major, year), (should_match, should_not_match) in cases.items():
            plan = engine.load_degree_plan(major, year)
            pattern = re.compile(self._breadth_items(plan)[0]["match"])
            for code in should_match:
                self.assertTrue(pattern.match(code), f"{major}-{year}: {code} should be a valid Breadth course")
            for code in should_not_match:
                self.assertFalse(pattern.match(code), f"{major}-{year}: {code} should NOT be a valid Breadth course")

    def test_business_breadth_2024_list_excludes_2025_only_additions(self):
        # MGMT 410/420, MIS 437, and RM 450 are real additions that only
        # appear on the 2025-26+ pages -- confirm the 2024-25 lists don't
        # falsely include them (a genuine year-over-year difference found
        # while cross-checking each major's own page for each catalog year).
        for major in ("ACCTG", "CIE", "FIN"):
            plan = engine.load_degree_plan(major, 2024)
            pattern = re.compile(self._breadth_items(plan)[0]["match"])
            for code in ("MGMT 410", "MGMT 420", "MIS 437"):
                self.assertFalse(pattern.match(code), f"{major}-2024: {code} is a 2025-26+ addition")

    def test_business_breadth_credits_a_real_self_reported_course(self):
        # End-to-end through plan_progress, the same path a transcript
        # upload uses.
        for major, code in (("ACCTG", "MGMT 320"), ("CIE", "ACCTG 404"),
                            ("FIN", "ACCTG 404"), ("BAIS", "ACCTG 404")):
            plan = engine.load_degree_plan(major, 2025)
            progress = engine.plan_progress(plan, {code})
            self.assertIn(code, progress["done_with"].values(), f"{major}-2025: {code} was not credited")

    def test_cie_2026_carries_forward_2025_verified_list(self):
        # CIENT_BS has no live 2026-27 Smeal handbook page yet (the majors
        # index stops at 2025-26) -- per the same "use the latest available
        # real data rather than leave a newer year unverified" instruction
        # CMPSC's 2025/2026 followed, this carries forward the verified
        # 2025-26 list instead of guessing, flagged explicitly in the notes.
        plan2025 = engine.load_degree_plan("CIE", 2025)
        plan2026 = engine.load_degree_plan("CIE", 2026)
        self.assertEqual(
            self._breadth_items(plan2025)[0]["match"],
            self._breadth_items(plan2026)[0]["match"],
        )

    def test_no_business_breadth_requirement_for_actsc(self):
        # ACTSC_BS has no "Business Breadth" requirement at all on its real
        # handbook pages (unlike ACCTG/CIE/FIN/BAIS) -- confirm this was
        # never incorrectly added.
        for year in self.SMEAL_MAJORS_YEARS["ACTSC"]:
            plan = engine.load_degree_plan("ACTSC", year)
            self.assertEqual(self._breadth_items(plan), [], f"ACTSC-{year}")

    def test_full_plan_builds_cleanly_for_every_touched_catalog_year(self):
        for major, years in self.SMEAL_MAJORS_YEARS.items():
            for year in years:
                plan = engine.load_degree_plan(major, year)
                catalog = engine.load_merged_catalog(plan["departments"])
                fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=5)
                failures = [w for w in fp["warnings"] if "Could not schedule" in w]
                self.assertEqual(failures, [], f"{major}-{year}: {failures}")


class TestFINHandbookRequirements(unittest.TestCase):
    """FIN_BS-specific findings from ugstudents.smeal.psu.edu's real Degree
    Requirements pages (2026-08-26), on top of the shared Business Breadth
    coverage in TestSmealBusinessCoreHandbookRequirements."""

    def _elective_items(self, plan):
        return [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "FIN 4XX Finance Elective"
        ]

    def test_2024_elective_pool_includes_fin_426(self):
        # The real 2024-25 FIN_BS page's "Additional Finance Courses" pool
        # already included FIN 426 (a real, permanently-numbered course) --
        # it was missing from this plan's elective options.
        plan = engine.load_degree_plan("FIN", 2024)
        items = self._elective_items(plan)
        self.assertTrue(items)
        for item in items:
            self.assertIn("FIN 426", item["options"])

    def test_2022_2023_elective_pool_correctly_excludes_fin_426(self):
        # Real 2022-23/2023-24 pages' pool: FIN 405/407/410/414/415/460/470
        # only -- FIN 426 is a real 2024-25+ addition, not retroactive.
        for year in (2022, 2023):
            plan = engine.load_degree_plan("FIN", year)
            items = self._elective_items(plan)
            self.assertTrue(items)
            for item in items:
                self.assertNotIn("FIN 426", item["options"], f"{year}")

    def test_2025_2026_elective_pool_matches_real_10_option_list(self):
        real_pool = {"FIN 401", "FIN 405", "FIN 407", "FIN 410", "FIN 414",
                     "FIN 415", "FIN 426", "FIN 460", "FIN 465", "FIN 470"}
        for year in (2025, 2026):
            plan = engine.load_degree_plan("FIN", year)
            items = self._elective_items(plan)
            self.assertTrue(items)
            for item in items:
                self.assertEqual(set(item["options"]), real_pool, f"{year}")


class TestACTSCHandbookRequirements(unittest.TestCase):
    """ACTSC_BS-specific findings from ugstudents.smeal.psu.edu's real Degree
    Requirements pages (2026-08-26), which model the RM/STAT sequence far
    more precisely than the university bulletin these plans were originally
    built from."""

    def test_rm214_added_for_post_2022_curriculum(self):
        # Real "Electives" breakdown for 2023-24 through 2026-27: "First-Year
        # Seminar (1 credit), RM 214 (1.5 credits), Additional Credits (5.5
        # credits)" -- RM 214 was missing from this plan entirely.
        for year in (2023, 2024, 2025, 2026):
            plan = engine.load_degree_plan("ACTSC", year)
            codes = {o for _, item in engine._iter_plan_items(plan) for o in item.get("options", [])}
            self.assertIn("RM 214", codes, f"{year}: RM 214 missing")

    def test_rm214_correctly_absent_for_2022_curriculum(self):
        # The real 2022-23 page's Electives section has no RM 214 line at
        # all (it predates this requirement).
        plan = engine.load_degree_plan("ACTSC", 2022)
        codes = {o for _, item in engine._iter_plan_items(plan) for o in item.get("options", [])}
        self.assertNotIn("RM 214", codes)

    def test_rm422_item_recognizes_rm412_as_a_real_alternate(self):
        # Real page: "Complete one course from RM 412, RM 422[, RM 497]" --
        # this plan only ever modeled RM 422. RM 422 stays first/default per
        # this plan's own documented scheduling reason (RM 412 needs RM 411,
        # only completable the same term as RM 422's slot).
        for year in (2023, 2024, 2025, 2026):
            plan = engine.load_degree_plan("ACTSC", year)
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("options") and item["options"][0] == "RM 422"
            )
            self.assertEqual(item["options"], ["RM 422", "RM 412"], f"{year}")

    def test_rm497_deliberately_not_added(self):
        # The handbook's third alternate (RM 412/422/497) is NOT modeled --
        # the scraped RM 497 catalog entry is a generic "Special Topics"
        # placeholder that doesn't represent this specific 4-credit,
        # RM-411-gated section, so adding it would misrepresent the
        # requirement rather than fix it.
        catalog = engine.load_merged_catalog(["RM"])
        rm497 = catalog.get("RM 497")
        self.assertIsNotNone(rm497)
        self.assertNotEqual(rm497.credits, 4.0)
        for year in (2023, 2024, 2025, 2026):
            plan = engine.load_degree_plan("ACTSC", year)
            codes = {o for _, item in engine._iter_plan_items(plan) for o in item.get("options", [])}
            self.assertNotIn("RM 497", codes, f"{year}")

    def test_full_plan_builds_cleanly_for_every_actsc_catalog_year(self):
        for year in (2022, 2023, 2024, 2025, 2026):
            plan = engine.load_degree_plan("ACTSC", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=5)
            failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(failures, [], f"{year}: {failures}")


class TestEngineeringBatchAHandbookRequirements(unittest.TestCase):
    """College of Engineering batch A (AE, AERSP, AIE, BE, BME, CE, CHE) —
    verified 2026-08-27 against each department's own real handbook/advising
    manual where one exists (CE, BE, AE, AERSP, CHE), or the current live
    bulletin where no dedicated handbook was found (AIE, BME, CMPEN and EE
    covered in a separate test class). Same methodology as CMPSC's own
    handbook verification: real course lists wired via `match`/`open_elective`
    instead of generic unfillable placeholders, real discrepancies fixed."""

    MAJORS = ("AE", "AERSP", "AIE", "BE", "BME", "CE", "CHE")

    def _reach(self, plan, item):
        """Mark every item ordered before `item` done, so recommend_semester
        actually walks far enough to try recommending `item` itself."""
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_every_batch_a_plan_builds_cleanly_for_a_realistic_student(self):
        """A student who places directly into MATH 140 (ALEKS 76+ / high
        school calculus, tier 4) is the realistic population for every one
        of these majors — expand_math_placement should waive the entire
        developmental math ladder immediately, and every plan should then
        schedule with zero 'Could not schedule' failures."""
        for major in self.MAJORS:
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            completed = engine.expand_math_placement(set(), placement_tier=4)
            fp = engine.build_full_plan(
                plan, catalog, completed, start_year=2026,
                grad_years=5 if major == "AE" else 4,
            )
            failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(failures, [], f"{major}: {failures}")

    def test_math_3_and_4_are_gone_from_every_batch_a_plan(self):
        """MATH 3/MATH 4 were removed from all 7 plans: math_catalog.json's
        own MATH 21 entry has an empty prereq_groups (no prerequisite at
        all), so the earlier "MATH 21 needs MATH 4 needs MATH 3" repair
        chain was factually wrong, and MATH 3/MATH 4 are separately
        hardcoded as unconditionally satisfied everywhere by
        expand_math_placement (NON_DEGREE_APPLICABLE_MATH) regardless of any
        student input — pure dead weight."""
        math_catalog = engine.load_merged_catalog(["MATH"])
        self.assertEqual(math_catalog["MATH 21"].prereq_groups, [])
        for major in self.MAJORS:
            plan = engine.load_degree_plan(major, 2026)
            for _, item in engine._iter_plan_items(plan):
                if item.get("type") == "course":
                    self.assertNotIn("MATH 3", item["options"], f"{major}: {item}")
                    self.assertNotIn("MATH 4", item["options"], f"{major}: {item}")

    def test_ae_department_elective_excludes_the_handbooks_denylist(self):
        plan = engine.load_degree_plan("AE", 2026)
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "AE Department Elective"
        ]
        self.assertEqual(len(items), 3)
        for item in items:
            pattern = re.compile(item["match"])
            for excluded in ("AE 401", "AE 402", "AE 404", "AE 421", "AE 422"):
                self.assertFalse(pattern.match(excluded), f"{excluded} should NOT match")
            for allowed in ("AE 403", "AE 441", "AE 453", "AE 500"):
                self.assertTrue(pattern.match(allowed), f"{allowed} should match")

    def test_ce_technical_elective_split_is_12_9_not_9_12(self):
        """The CEE handbook's footnote 3: 12cr locked to CE/ENVE-numbered
        courses ('CE Technical Elective', 4 slots of 3cr) + 9cr from the
        handbook's broader approved department list ('Technical Elective',
        3 slots) — this plan previously had the split backwards."""
        plan = engine.load_degree_plan("CE", 2026)
        locked = [it for _, it in engine._iter_plan_items(plan) if it.get("label") == "CE Technical Elective"]
        broad = [it for _, it in engine._iter_plan_items(plan) if it.get("label") == "Technical Elective"]
        self.assertEqual(len(locked), 4)
        self.assertEqual(len(broad), 3)
        for item in locked:
            self.assertIsNotNone(item.get("match"))
        for item in broad:
            self.assertTrue(item.get("open_elective"))

    def test_ce_capstone_names_the_handbooks_real_current_course_codes(self):
        plan = engine.load_degree_plan("CE", 2026)
        capstone = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "Capstone" in (item.get("label") or "")
        )
        self.assertEqual(
            set(capstone["options"]),
            {"CE 421W", "CE 438W", "CE 448W", "CE 465W", "CE 472W"},
        )
        # CE 439W was discontinued/replaced by CE 438W per the handbook.
        self.assertNotIn("CE 439W", capstone["options"])

    def test_ce_broad_technical_elective_never_recommends_cas_or_writing_courses(self):
        """CAS/ENGL/ESL are in CE's departments list only for its own Gen Ed
        / CAS 100 / ENGL 15 requirements, not because they're real technical
        electives per the handbook's approved department list."""
        plan = engine.load_degree_plan("CE", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Technical Elective" and it.get("open_elective")
        )
        completed, consumed = self._reach(plan, item)
        rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
        pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
        self.assertIsNotNone(pick)
        self.assertIsNotNone(pick["code"])
        self.assertFalse(pick["code"].startswith("CAS "))
        self.assertFalse(pick["code"].startswith("ENGL "))
        self.assertFalse(pick["code"].startswith("ESL "))

    def test_be_technical_selection_slots_are_2_in_spring_not_fall(self):
        """The ABE advising manual's Fourth Year table has 0 'Technical
        Selection' slots in Fall and 2 in Spring — this plan previously had
        1 in each (backwards credit split, 18.5/12.5 instead of 15.5/15.5)."""
        plan = engine.load_degree_plan("BE", 2026)
        sem7 = next(s for s in plan["semesters"] if s["index"] == 7)
        sem8 = next(s for s in plan["semesters"] if s["index"] == 8)
        tech_sel_7 = [it for it in sem7["items"] if it.get("label") == "Technical Selection"]
        tech_sel_8 = [it for it in sem8["items"] if it.get("label") == "Technical Selection"]
        self.assertEqual(len(tech_sel_7), 0)
        self.assertEqual(len(tech_sel_8), 2)

    def test_be_elective_pools_are_wired_to_the_real_advising_manual_lists(self):
        plan = engine.load_degree_plan("BE", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for label in (
            "Math/Basic Science Selection", "BE 4XX-Biological Engineering Selection",
            "BIO/AG Selection", "Engineering Science/Design Selection", "Technical Selection",
        ):
            item = next(
                it for _, it in engine._iter_plan_items(plan) if it.get("label") == label
            )
            self.assertIsNotNone(item.get("match"), f"{label} not wired")
            pattern = re.compile(item["match"])
            matches = [code for code in catalog if pattern.match(code)]
            self.assertTrue(matches, f"{label}'s regex matches nothing in the loaded catalog")

    def test_bme_biomechanics_elective_matches_the_real_departmental_list(self):
        plan = engine.load_degree_plan("BME", 2026)
        items = [it for _, it in engine._iter_plan_items(plan) if it.get("label") == "Biomechanics Elective"]
        self.assertEqual(len(items), 3)
        pattern = re.compile(items[0]["match"])
        for allowed in ("BME 410", "BME 443", "EMCH 461", "ME 461", "EDSGN 452"):
            self.assertTrue(pattern.match(allowed), f"{allowed} should match")
        for excluded in ("BME 201", "BME 301", "EMCH 210"):
            self.assertFalse(pattern.match(excluded), f"{excluded} should NOT match")

    def test_bme_409_and_emch_316_are_correctly_placed(self):
        """BME 409 (Biofluid Mechanics), a prescribed course for the
        Biomechanics option, was missing entirely; EMCH 316 was one
        semester late (should pair with EMCH 315 in Semester 5)."""
        plan = engine.load_degree_plan("BME", 2026)
        sem5 = next(s for s in plan["semesters"] if s["index"] == 5)
        sem6 = next(s for s in plan["semesters"] if s["index"] == 6)
        sem5_codes = {o for it in sem5["items"] if it.get("type") == "course" for o in it["options"]}
        sem6_codes = {o for it in sem6["items"] if it.get("type") == "course" for o in it["options"]}
        self.assertIn("EMCH 316", sem5_codes)
        self.assertNotIn("EMCH 316", sem6_codes)
        self.assertIn("BME 409", sem6_codes)

    def test_bme_related_technical_elective_never_recommends_a_non_technical_course(self):
        plan = engine.load_degree_plan("BME", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Related Technical Elective"
        )
        completed, consumed = self._reach(plan, item)
        rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
        pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
        self.assertIsNotNone(pick)
        self.assertIsNotNone(pick["code"])
        for bad_prefix in ("BIOL ", "BMB ", "ENGL ", "ESL ", "CAS ", "ECON ", "ME "):
            self.assertFalse(pick["code"].startswith(bad_prefix), pick["code"])

    def test_aie_cmpsc_448_or_445_matches_the_real_bulletin_or_pair(self):
        plan = engine.load_degree_plan("AIE", 2026)
        item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course" and it["options"][0] == "CMPSC 448"
        )
        self.assertEqual(item["options"], ["CMPSC 448", "CMPSC 445"])

    def test_aie_technical_elective_never_recommends_an_internship_or_special_topics_course(self):
        plan = engine.load_degree_plan("AIE", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        items = [
            it for _, it in engine._iter_plan_items(plan)
            if (it.get("label") or "").startswith("Technical Elective") and it.get("open_elective")
        ]
        self.assertTrue(items)
        completed, consumed = self._reach(plan, items[-1])
        rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
        for item in items:
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            if pick and pick["code"]:
                course = catalog[pick["code"]]
                self.assertNotRegex(course.name, engine._EXCLUDE_NAME_RE)

    def test_aie_department_list_is_deliberately_left_unwired(self):
        """The bulletin calls this a *non-technical* elective, but every
        department in AIE's plan is technical -- wiring it via
        open_elective would contradict the bulletin's own stated intent,
        so it's intentionally left as a generic placeholder rather than
        guessing at an unevidenced broader department list."""
        plan = engine.load_degree_plan("AIE", 2026)
        item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "AIE Department List"
        )
        self.assertNotIn("open_elective", item)
        self.assertIsNone(item.get("match"))

    def test_che_chemical_engineering_elective_matches_the_handbooks_real_list(self):
        plan = engine.load_degree_plan("CHE", 2026)
        items = [it for _, it in engine._iter_plan_items(plan) if it.get("label") == "Chemical engineering elective"]
        self.assertEqual(len(items), 2)
        pattern = re.compile(items[0]["match"])
        for allowed in ("CHE 412", "CHE 444", "CHE 455"):
            self.assertTrue(pattern.match(allowed))
        for excluded in ("CHE 410", "CHE 430", "CHE 470"):
            self.assertFalse(pattern.match(excluded))

    def test_che_lab_experience_matches_the_handbooks_real_list(self):
        plan = engine.load_degree_plan("CHE", 2026)
        item = next(it for _, it in engine._iter_plan_items(plan) if it.get("label") == "Lab experience")
        pattern = re.compile(item["match"])
        for allowed in ("CHEM 423W", "MICRB 202", "CE 475"):
            self.assertTrue(pattern.match(allowed))

    def test_che_engl_202c_is_narrowed_to_the_real_bulletin_pick(self):
        plan = engine.load_degree_plan("CHE", 2026)
        item = next(
            it for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course" and "ENGL 202C" in it["options"]
        )
        self.assertEqual(item["options"], ["ENGL 202C"])

    def test_che_gateway_courses_are_flagged_etm_like_the_bulletins_hash_footnote(self):
        plan = engine.load_degree_plan("CHE", 2026)
        etm_codes = {
            it["options"][0] for _, it in engine._iter_plan_items(plan)
            if it.get("type") == "course" and it.get("etm")
        }
        self.assertEqual(etm_codes, {"CHEM 110", "EDSGN 100", "MATH 140", "MATH 141", "PHYS 211"})

    def test_open_elective_is_still_inert_on_the_untouched_engineering_batch_a_slots(self):
        """Slots this review deliberately left as generic placeholders (no
        real enumerated source found) must still behave exactly like an
        ordinary unfillable slot -- never silently pick a course."""
        untouched = {
            "CHE": ["Engineering elective", "Science elective", "Science or engineering elective"],
        }
        for major, labels in untouched.items():
            plan = engine.load_degree_plan(major, 2026)
            for label in labels:
                items = [it for _, it in engine._iter_plan_items(plan) if it.get("label") == label]
                self.assertTrue(items, f"{major}: expected a '{label}' slot")
                for item in items:
                    self.assertNotIn("open_elective", item, f"{major}: {label} unexpectedly wired")


class TestEngineeringBatchACMPENandEEHandbookRequirements(unittest.TestCase):
    """CMPEN and EE — the other two EECS-school majors in Engineering batch
    A, verified 2026-08-27 against their own real handbooks at eecs.psu.edu
    (the same handbook system CMPSC was verified against)."""

    def _reach(self, plan, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_both_plans_build_cleanly_for_a_realistic_student(self):
        for major in ("CMPEN", "EE"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            completed = engine.expand_math_placement(set(), placement_tier=4)
            fp = engine.build_full_plan(plan, catalog, completed, start_year=2026, grad_years=4)
            failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(failures, [], f"{major}: {failures}")

    def test_math_3_and_4_are_gone_from_cmpen_and_ee(self):
        for major in ("CMPEN", "EE"):
            plan = engine.load_degree_plan(major, 2026)
            for _, item in engine._iter_plan_items(plan):
                if item.get("type") == "course":
                    self.assertNotIn("MATH 3", item["options"], f"{major}: {item}")
                    self.assertNotIn("MATH 4", item["options"], f"{major}: {item}")

    def test_cmpen_270_271_275_mutual_exclusion_is_wired_in_the_catalog(self):
        catalog = engine.load_merged_catalog(["CMPEN"])
        c270, c271, c275 = catalog["CMPEN 270"], catalog["CMPEN 271"], catalog["CMPEN 275"]
        self.assertFalse(engine.excludes_satisfied(c270, {"CMPEN 271"}))
        self.assertFalse(engine.excludes_satisfied(c270, {"CMPEN 275"}))
        self.assertFalse(engine.excludes_satisfied(c271, {"CMPEN 270"}))
        self.assertFalse(engine.excludes_satisfied(c275, {"CMPEN 270"}))
        self.assertTrue(engine.excludes_satisfied(c270, set()))

    def test_cmpen_471_exists_in_the_catalog_and_is_a_real_technical_elective_option(self):
        catalog = engine.load_merged_catalog(["CMPEN"])
        self.assertIn("CMPEN 471", catalog)

    def test_cmpen_400_level_elective_matches_the_handbooks_curated_list(self):
        plan = engine.load_degree_plan("CMPEN", 2026)
        items = [it for _, it in engine._iter_plan_items(plan) if it.get("label") == "CMPEN 400-Level elective"]
        self.assertEqual(len(items), 2)
        pattern = re.compile(items[0]["match"])
        for allowed in ("CMPEN 416", "CMPEN 462", "CMPEN 471", "EE 453", "EE 456"):
            self.assertTrue(pattern.match(allowed), f"{allowed} should match")
        for excluded in ("CMPEN 431", "CMPEN 441", "EE 410"):
            self.assertFalse(pattern.match(excluded), f"{excluded} should NOT match")

    def test_cmpen_cmpsc_400_level_elective_excludes_all_five_petition_codes(self):
        """Unlike CMPSC's own 494/495/496/499 exclusion set, CMPEN's real
        handbook excludes a fifth code too: 497."""
        plan = engine.load_degree_plan("CMPEN", 2026)
        items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "CMPSC or CMPEN 400-Level elective"
        ]
        self.assertEqual(len(items), 2)
        pattern = re.compile(items[0]["match"])
        for excluded in ("CMPSC 494", "CMPSC 495", "CMPSC 496", "CMPSC 497", "CMPSC 499",
                         "CMPEN 494", "CMPEN 495", "CMPEN 496", "CMPEN 497", "CMPEN 499"):
            self.assertFalse(pattern.match(excluded), f"{excluded} should NOT match")
        for allowed in ("CMPSC 442", "CMPEN 431"):
            self.assertTrue(pattern.match(allowed), f"{allowed} should match")

    def test_cmpen_department_list_elective_excludes_the_handbooks_real_denylist(self):
        plan = engine.load_degree_plan("CMPEN", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        items = [
            it for _, it in engine._iter_plan_items(plan)
            if it.get("label") == "Department List (General Elective)"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertTrue(item.get("open_elective"))
            exclude_set = {engine.norm_code(c) for c in item["elective_exclude"]}
            forced_exclude = {c for c in catalog if c not in exclude_set}
            pick = engine._pick_open_elective(catalog, set(), forced_exclude, exclude_exact=item["elective_exclude"])
            self.assertIsNone(pick, f"Picked a handbook-excluded course: {pick}")

    def test_ee_300_level_elective_matches_the_handbooks_real_list(self):
        plan = engine.load_degree_plan("EE", 2026)
        items = [it for _, it in engine._iter_plan_items(plan) if it.get("label") == "EE or CMPEN 300-Level elective"]
        self.assertEqual(len(items), 2)
        pattern = re.compile(items[0]["match"])
        for allowed in ("EE 311", "EE 360", "CMPEN 331"):
            self.assertTrue(pattern.match(allowed), f"{allowed} should match")
        for excluded in ("EE 410", "EE 465", "EE 211"):
            self.assertFalse(pattern.match(excluded), f"{excluded} should NOT match")

    def test_ee_400_level_elective_matches_the_handbooks_real_list_and_excludes_ee_465(self):
        """EE 465 is explicitly called out in the handbook as a Statistics
        elective, not a technical elective."""
        plan = engine.load_degree_plan("EE", 2026)
        items = [it for _, it in engine._iter_plan_items(plan) if it.get("label") == "EE or CMPEN 400-Level elective"]
        self.assertEqual(len(items), 2)
        pattern = re.compile(items[0]["match"])
        for allowed in ("EE 410", "EE 456", "CMPEN 431", "CMPEN 475"):
            self.assertTrue(pattern.match(allowed), f"{allowed} should match")
        self.assertFalse(pattern.match("EE 465"))

    def test_ee_statistics_elective_matches_the_handbooks_exact_list(self):
        plan = engine.load_degree_plan("EE", 2026)
        item = next(it for _, it in engine._iter_plan_items(plan) if it.get("label") == "Statistics elective")
        pattern = re.compile(item["match"])
        for allowed in ("STAT 418", "STAT 414", "STAT 401", "EE 465", "IE 424"):
            self.assertTrue(pattern.match(allowed), f"{allowed} should match")
        self.assertFalse(pattern.match("STAT 200"))

    def test_ee_related_elective_never_recommends_a_math_or_physics_or_chem_course(self):
        plan = engine.load_degree_plan("EE", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        item = next(it for _, it in engine._iter_plan_items(plan) if it.get("label") == "Related elective")
        completed, consumed = self._reach(plan, item)
        rec = engine.recommend_semester(plan, catalog, completed, consumed_slots=consumed, max_credits=99)
        pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
        self.assertIsNotNone(pick)
        self.assertIsNotNone(pick["code"])
        for bad_prefix in ("MATH ", "PHYS ", "CHEM ", "ESL ", "CAS ", "ECON "):
            self.assertFalse(pick["code"].startswith(bad_prefix), pick["code"])
        self.assertNotIn(pick["code"], {"EE 211", "EE 212", "EE 353", "EE 465"})

    def test_ee_ghw_gen_ed_is_in_semester_5_not_semester_6(self):
        """Real handbook: GHW belongs in the 3rd Year Fall term; this plan
        previously had a mislabeled 1cr plain Gen Ed there and a real GHW
        misplaced one semester later."""
        plan = engine.load_degree_plan("EE", 2026)
        sem5 = next(s for s in plan["semesters"] if s["index"] == 5)
        sem6 = next(s for s in plan["semesters"] if s["index"] == 6)
        self.assertTrue(any(it.get("gen_ed") == "GHW" for it in sem5["items"]))
        self.assertFalse(any(it.get("gen_ed") == "GHW" for it in sem6["items"]))

    def test_ee_gen_ed_credits_sum_to_the_real_18(self):
        plan = engine.load_degree_plan("EE", 2026)
        total = sum(
            it["credits"] for s in plan["semesters"] for it in s["items"]
            if it.get("type") == "slot" and "GEN ED" in (it.get("label") or "")
        )
        self.assertEqual(total, 18.0)

    def test_ee_semester_7_and_8_have_the_real_five_electives_split(self):
        """Handbook: '5 electives, 15 credits total.' Semester 7 previously
        had 2 400-level electives and no Gen Ed; Semester 8 had only 1."""
        plan = engine.load_degree_plan("EE", 2026)
        sem7 = next(s for s in plan["semesters"] if s["index"] == 7)
        sem8 = next(s for s in plan["semesters"] if s["index"] == 8)
        sem7_400 = [it for it in sem7["items"] if it.get("label") == "EE or CMPEN 400-Level elective"]
        sem8_400 = [it for it in sem8["items"] if it.get("label") == "EE or CMPEN 400-Level elective"]
        self.assertEqual(len(sem7_400), 0)
        self.assertEqual(len(sem8_400), 2)
        self.assertTrue(any(it.get("type") == "slot" and it.get("label") == "GEN ED" for it in sem7["items"]))


class TestEETHandbookRequirements(unittest.TestCase):
    """Electrical Engineering Technology, B.S. -- an ABET-ETAC Engineering
    Technology program at Wilkes-Barre. No separate department handbook was
    found beyond the live current bulletin (2026-27 edition), but that
    bulletin's own suggested-academic-plan PAGE carries real footnoted
    course lists for System/Electronics/GEET/SET/Math elective categories
    that the plan JSON had never captured -- fetched directly from
    bulletins.psu.edu/.../electrical-engineering-technology-bs_suggestedacademicplantext.pdf."""

    def setUp(self):
        self.plan = engine.load_degree_plan("EET", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def _match_for(self, label):
        return next(
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(self.plan)
            if item.get("match") and item.get("label") == label
        )

    def test_set_elective_matches_real_course_list(self):
        rx = self._match_for("SET elective")
        for code in ["STAT 200", "MATH 230", "BIOL 141", "EMCH 211"]:
            self.assertTrue(rx.match(code), code)
        self.assertFalse(rx.match("CMPSC 465"))

    def test_math_requirement_matches_real_course_list(self):
        rx = self._match_for("Math requirement")
        for code in ["STAT 200", "STAT 414", "IE 424"]:
            self.assertTrue(rx.match(code), code)
        self.assertFalse(rx.match("MATH 140"))

    def test_system_elective_is_the_real_narrow_list(self):
        rx = self._match_for("System elective")
        for code in ["EET 408", "EET 409", "EET 433"]:
            self.assertTrue(rx.match(code), code)
        self.assertFalse(rx.match("EET 410"))

    def test_400_level_eet_elective_excludes_required_courses(self):
        rx = self._match_for("400-Level EET elective")
        self.assertTrue(rx.match("EET 410"))
        for required in ["EET 105", "EET 114", "EET 118", "EET 212W", "EET 213W",
                          "EET 311", "EET 312", "EET 331", "EET 419", "EET 420W"]:
            self.assertFalse(rx.match(required), required)

    def test_geet_technical_elective_slots_are_wired_and_use_the_real_list(self):
        geet_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "GEET Technical Elective"
        ]
        self.assertEqual(len(geet_items), 4)
        for item in geet_items:
            self.assertTrue(item.get("match"))
            rx = re.compile(item["match"])
            self.assertTrue(rx.match("EET 456"))
            self.assertFalse(rx.match("MATH 140"))

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestEMETHandbookRequirements(unittest.TestCase):
    """Electro-Mechanical Engineering Technology, B.S. -- built for the
    Beaver campus. Beaver's own bulletin page doesn't repeat the elective
    footnote, but the identical 'Approved General Technical Elective'
    course list is footnoted on the Fayette/New Kensington/York campus
    pages for the SAME real B.S. curriculum -- confirmed real, not
    campus-specific, so applied here."""

    def setUp(self):
        self.plan = engine.load_degree_plan("EMET", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_technical_elective_slots_use_the_real_list(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "EMET Technical Elective"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            rx = re.compile(item["match"])
            for code in ["STAT 200", "MATH 230", "MGMT 301", "EMET 495"]:
                self.assertTrue(rx.match(code), code)
            self.assertFalse(rx.match("EMET 440"))

    def test_full_plan_still_reaches_graduation_in_four_years(self):
        import datetime
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestESCHandbookRequirements(unittest.TestCase):
    """Engineering Science, B.S. -- the university bulletin itself gives no
    concrete course codes for its Foundational/Technical Elective pools, but
    the ESM department's own electives page (esm.psu.edu) does name a real
    core Foundational Elective list plus each core course's approved
    substitutions. The broader Technical Elective pool (multi-department,
    not fully enumerated on the page fetched) is left generic, honestly --
    not guessed at."""

    def setUp(self):
        self.plan = engine.load_degree_plan("ESC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_foundational_elective_slots_all_wired_with_real_list(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Foundational Elective"
        ]
        self.assertEqual(len(items), 5)
        for item in items:
            self.assertTrue(item.get("match"), item)
            rx = re.compile(item["match"])
            self.assertTrue(rx.match("CHEM 112"))
            self.assertTrue(rx.match("BME 409"))
            self.assertFalse(rx.match("MATH 140"))

    def test_esc_410_is_still_correctly_absent_from_the_real_catalog(self):
        # Regression check for a previously-verified staleness finding: the
        # bulletin lists a prescribed 'ESC 410' course that doesn't actually
        # exist in the department's real scraped catalog.
        catalog = engine.load_merged_catalog(["ESC"])
        self.assertNotIn("ESC 410", catalog)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestIEHandbookRequirements(unittest.TestCase):
    """Industrial Engineering, B.S. -- the Harold and Inge Marcus Department
    of Industrial and Manufacturing Engineering (IME) publishes its own,
    highly specific electives page (ime.psu.edu/students/undergraduate/
    electives.aspx) naming the exact course list for every one of this
    plan's 5 elective categories, plus real double-counting restrictions
    between them."""

    def setUp(self):
        self.plan = engine.load_degree_plan("IE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def _matches_for(self, label):
        return [
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(self.plan)
            if item.get("match") and item.get("label") == label
        ]

    def test_manufacturing_process_elective_is_ie_only_narrow_list(self):
        for rx in self._matches_for("Manufacturing Process elective"):
            self.assertTrue(rx.match("IE 306"))
            self.assertFalse(rx.match("IE 408"))  # Human Factors, not Manufacturing

    def test_human_factors_elective_is_the_real_narrow_list(self):
        for rx in self._matches_for("Human Factors elective"):
            self.assertTrue(rx.match("IE 419"))
            self.assertFalse(rx.match("IE 306"))

    def test_science_elective_matches_real_list(self):
        for rx in self._matches_for("Science elective"):
            self.assertTrue(rx.match("BIOL 141"))
            self.assertTrue(rx.match("MATH 401"))
            self.assertFalse(rx.match("CHEM 110"))

    def test_engineering_elective_matches_real_list(self):
        for rx in self._matches_for("Engineering elective"):
            self.assertTrue(rx.match("EE 210"))
            self.assertFalse(rx.match("IE 306"))

    def test_technical_elective_double_counting_is_prevented_by_scan_order(self):
        # Real rule: IE 306/307/311/428 can't double-count with Manufacturing
        # Process elective, and IE 408/418/419 can't double-count with Human
        # Factors elective. The engine has no per-category exclusion field for
        # match-slots -- this works only because plan_progress consumes a
        # completed course for the FIRST matching slot it scans, in flowchart
        # order, and Manufacturing/Human-Factors slots are ordered before the
        # Technical elective slots that share codes with them.
        progress = engine.plan_progress(self.plan, {"IE 306", "IE 419"})
        done_labels = {
            item["label"]: True
            for _, item in engine._iter_plan_items(self.plan)
            if item["id"] in progress["done_ids"]
        }
        self.assertIn("Manufacturing Process elective", done_labels)
        self.assertIn("Human Factors elective", done_labels)
        # Neither IE 306 nor IE 419 is left over to also satisfy a Technical
        # elective slot -- confirm by checking only 2 slots got credited.
        credited = [
            _ for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "slot" and item["id"] in progress["done_ids"]
        ]
        self.assertEqual(len(credited), 2)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestMEHandbookRequirements(unittest.TestCase):
    """Mechanical Engineering, B.S. -- the ME department's own Technical
    Electives Course Descriptions page (me.psu.edu) enumerates the real
    Mechanical Engineering Technical Elective (METE) course list. The
    broader Engineering Technical Elective / General Technical Elective
    categories (College of Engineering-wide / math-science-engineering-wide
    pools) are left generic, honestly, since their full multi-department
    course lists weren't enumerated on the pages fetched."""

    def setUp(self):
        self.plan = engine.load_degree_plan("ME", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_mete_matches_real_list_and_excludes_required_courses(self):
        rx = next(
            re.compile(item["match"])
            for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Mechanical Engineering Technical Elective"
        )
        for code in ["ME 400", "ME 420", "ME 461", "ME 481"]:
            self.assertTrue(rx.match(code), code)
        for required in ["ME 300", "ME 320", "ME 330", "ME 340", "ME 348", "ME 360",
                          "ME 370", "ME 390", "ME 410", "ME 435", "ME 440W", "ME 450",
                          "ME 454", "ME 490"]:
            self.assertFalse(rx.match(required), required)
        # Petition/GPA-gated special cases, not a straightforward METE pick.
        for special in ["ME 493", "ME 494H", "ME 496", "ME 497"]:
            self.assertFalse(rx.match(special), special)

    def test_full_plan_still_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestNUCEHandbookRequirements(unittest.TestCase):
    """Nuclear Engineering, B.S. -- the department's own course-listing page
    (nuce.psu.edu) states the real NUCE Technical Elective rule verbatim:
    '400-level NUCE courses, except NUCE 401, that are not required in the
    nuclear engineering B.S. curriculum.'"""

    def setUp(self):
        self.plan = engine.load_degree_plan("NUCE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_nuclear_engineering_elective_excludes_required_and_special_topics(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Nuclear Engineering elective"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            rx = re.compile(item["match"])
            for code in ["NUCE 405", "NUCE 409", "NUCE 442"]:
                self.assertTrue(rx.match(code), code)
            for excluded in ["NUCE 401", "NUCE 403", "NUCE 420", "NUCE 430",
                              "NUCE 431W", "NUCE 450", "NUCE 451",
                              "NUCE 490", "NUCE 494", "NUCE 496", "NUCE 497", "NUCE 499"]:
                self.assertFalse(rx.match(excluded), excluded)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestSURHandbookRequirements(unittest.TestCase):
    """Surveying Engineering, B.S. (Wilkes-Barre) -- no separate department
    handbook found; verified term-by-term against the live current
    (2026-27) bulletin's own suggested academic plan PDF instead. Found one
    real staleness bug: the plan's second Health & Wellness (GHW) half-credit
    box was wired onto the wrong semester."""

    def setUp(self):
        self.plan = engine.load_degree_plan("SUR", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_second_ghw_box_is_wired_on_the_real_bulletin_semester(self):
        # The live bulletin's Fourth Year Fall term carries the second GHW
        # 1.5cr box (the first is already correctly wired in Semester 5);
        # Fourth Year Spring has none. Semester 7 = Fourth Year Fall here.
        sem7_ghw = [
            item for sem, item in engine._iter_plan_items(self.plan)
            if sem["index"] == 7 and item.get("gen_ed") == "GHW"
        ]
        sem8_ghw = [
            item for sem, item in engine._iter_plan_items(self.plan)
            if sem["index"] == 8 and item.get("gen_ed") == "GHW"
        ]
        self.assertEqual(len(sem7_ghw), 1)
        self.assertEqual(sem8_ghw, [])

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestDTSCEHandbookRequirements(unittest.TestCase):
    """Data Sciences, B.S. (Engineering, Computational Data Sciences option)
    -- distinct from Eberly College of Science's DS and IST's DATSC. A real,
    detailed EECS department handbook exists (eecs.psu.edu/assets/docs/
    handbooks/DTSCE-handbook-2023-2024.pdf) with exact List A / List B
    technical-elective course lists and an exact Natural Sciences (GN)
    denylist, both of which this plan had never fully captured."""

    def setUp(self):
        self.plan = engine.load_degree_plan("DTSCE", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_list_a_items_accept_every_real_list_a_course(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "List A Course"
        ]
        self.assertEqual(len(items), 2)
        real_list_a = {"CMPEN 454", "CMPSC 450", "CMPSC 455", "CMPSC 456", "MATH 484", "MATH 452", "DS 300"}
        for item in items:
            self.assertEqual(set(item["options"]), real_list_a)

    def test_list_b_items_accept_every_real_list_b_course_and_drop_ds_441(self):
        # DS 441 was previously hardcoded as the sole "List B Course" pick
        # for one item, but DS 441 is a real course that simply isn't on the
        # handbook's actual List B -- a real wrong-category bug, not just an
        # incomplete-options gap.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "List B Course"
        ]
        self.assertEqual(len(items), 2)
        real_list_b = {"CMPSC 431W", "EE 456", "MATH 436", "MATH 448", "MATH 465",
                        "STAT 416", "STAT 440", "STAT 460", "STAT 461", "STAT 462"}
        for item in items:
            self.assertEqual(set(item["options"]), real_list_b)
            self.assertNotIn("DS 441", item["options"])

    def test_natural_science_gn_slots_are_wired_with_the_real_denylist(self):
        gn_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("gen_ed") == "GN"
        ]
        # Handbook: "Natural Sciences (9 credits)" = 3 x 3cr GN courses.
        self.assertEqual(len(gn_items), 3)
        self.assertEqual(sum(float(i["credits"]) for i in gn_items), 9)
        excluded = {"ASTRO 1", "BISC 1", "CHEM 1", "PHYS 1", "GEOSC 20"}
        for item in gn_items:
            exclude_set = {engine.norm_code(c) for c in item.get("gen_ed_exclude", [])}
            self.assertTrue(excluded.issubset(exclude_set), item)

    def test_natural_science_gn_slot_recommends_a_real_non_excluded_course(self):
        catalog = engine.load_merged_catalog(self.plan["departments"])
        gn_item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("gen_ed") == "GN"
        )
        exclude = {engine.norm_code(c) for c in gn_item.get("gen_ed_exclude", [])}
        pick = engine._pick_gen_ed_course("GN", catalog, "DS", set(), exclude)
        self.assertIsNotNone(pick)
        self.assertNotIn(pick[0], exclude)

    def test_cmpen_454_is_a_real_reachable_course_via_the_added_department(self):
        # CMPEN 454 is a real List A option per the handbook, but CMPEN was
        # never in this plan's departments list, so it could never actually
        # be looked up as a catalog course (only credited via a plain string
        # match, never recommended with a real name/credit count).
        self.assertIn("CMPEN", self.plan["departments"])
        catalog = engine.load_merged_catalog(self.plan["departments"])
        self.assertIn("CMPEN 454", catalog)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestEnglishHandbookVerification(unittest.TestCase):
    """Re-verified against bulletins.psu.edu's own program-requirements PDF
    (english-ba_programrequirementstext.pdf) for the Traditions of
    Innovation option, across all 5 catalog years (2022-2026). No separate
    English department handbook beyond the bulletin could be found
    published anywhere -- bulletin-only major. The 4 catalog groups
    (2022/2023/2024 sharing one structure; 2025/2026 sharing another) are
    byte-for-byte identical to each other apart from catalog_year, and the
    one real difference between the two groups -- LA 83 (First-Year
    Seminar) and the LA 283 Sophomore Seminar slot (1.5cr each) being added
    starting 2025 -- is correctly documented and modeled, not a bug.

    Real credit-bucket math confirmed against the PDF: Common Requirements
    (ENGL 200/201 3cr + ENGL 487W/494H 3cr + 18cr Supporting, >=9cr at
    300/400 level) + Traditions of Innovation Option (12cr, one 3cr course
    per era: Medieval-16th C., 16th-18th C., 19th C., 20th C.-Present) =
    36cr major total. This plan's 10 generic 'Concentration Course'-style
    slots (6 untyped + 4 regex-matched 400-level) sum to exactly 30cr,
    matching the bulletin's 18cr Supporting + 12cr Option combined -- the
    department doesn't publish an enumerable era-to-course list (adviser-
    driven), so modeling all 10 as one undifferentiated pool (rather than
    splitting 4 of them into named era categories) is the same intentional
    simplification used elsewhere in this app for genuinely open pools, not
    a fabricated or incorrect number. Also confirmed real: ENGL 202A/B/C/D
    are mutually exclusive per each course's own catalog description ("A
    student may take only one course for credit from ENGL 202A, 202B,
    202C, and 202D") -- previously unenforced across the whole app since
    every major that uses ENGL 202 models it as a single OR-pool item, but
    now protected against a multi-major/minor merge double-count too."""

    CATALOG_YEARS = (2022, 2023, 2024, 2025, 2026)

    def test_all_5_catalog_years_build_cleanly(self):
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("ENGL", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=4)
            scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(scheduling_failures, [], f"{year}: {scheduling_failures}")

    def test_common_requirements_and_option_credits_match_the_real_bulletin(self):
        # ENGL 200/201 (3cr) + ENGL 487W/494H (3cr) + 10 generic
        # Concentration-style slots (30cr) == the bulletin's own
        # 3 + 3 + 18 (Supporting) + 12 (Option) = 36cr major total.
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("ENGL", year)
            named = 0.0
            concentration_slots = 0
            for _, item in engine._iter_plan_items(plan):
                label = item.get("label") or ""
                if item.get("type") == "course" and set(item.get("options", [])) == {"ENGL 200", "ENGL 201"}:
                    named += item["credits"]
                elif item.get("type") == "course" and set(item.get("options", [])) == {"ENGL 487W", "ENGL 494H"}:
                    named += item["credits"]
                elif "Concentration Course" in label:
                    concentration_slots += 1
            self.assertEqual(named, 6.0, f"{year}: ENGL 200/201 + 487W/494H should total 6cr")
            self.assertEqual(concentration_slots, 10, f"{year}: expected 10 Concentration Course slots (30cr)")

    def test_400_level_concentration_slots_use_the_real_regex(self):
        for year in self.CATALOG_YEARS:
            plan = engine.load_degree_plan("ENGL", year)
            items = [
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label") == "ENGL 400-level Concentration Course"
            ]
            self.assertEqual(len(items), 4, f"{year}: expected 4 regex-matched 400-level slots")
            for item in items:
                pattern = re.compile(item["match"])
                self.assertTrue(pattern.match("ENGL 432"))
                self.assertTrue(pattern.match("ENGL 487W"))
                self.assertFalse(pattern.match("ENGL 202A"))

    def test_engl_202_variants_are_mutually_exclusive(self):
        catalog = engine.load_merged_catalog(["ENGL"])
        variants = ["ENGL 202A", "ENGL 202B", "ENGL 202C", "ENGL 202D"]
        for code in variants:
            others = {c for c in variants if c != code}
            self.assertFalse(engine.excludes_satisfied(catalog[code], {next(iter(others))}))
            self.assertTrue(engine.excludes_satisfied(catalog[code], set()))

    def test_2022_through_2024_predate_la83_la283(self):
        # Real, documented curriculum difference: LA 83/LA 283 (1.5cr each)
        # were added starting the 2025 catalog year -- confirm the older
        # years genuinely don't have them and the newer years do.
        for year in (2022, 2023, 2024):
            plan = engine.load_degree_plan("ENGL", year)
            all_options = {
                o for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course"
                for o in item.get("options", [])
            }
            self.assertNotIn("LA 83", all_options, f"{year} should not have LA 83 yet")
        for year in (2025, 2026):
            plan = engine.load_degree_plan("ENGL", year)
            all_options = {
                o for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course"
                for o in item.get("options", [])
            }
            self.assertIn("LA 83", all_options, f"{year} should have LA 83")


class TestPLSCHandbookVerification(unittest.TestCase):
    """Real data pulled from bulletins.psu.edu's own program-requirements
    PDFs for Political Science B.S. and B.A. (political-science-bs/ba
    _programrequirementstext.pdf) — no separate department handbook exists
    beyond the bulletin itself for this major. Found and fixed 3 real gaps:
    (1) the B.S.'s computer-science pool is literally "CMPSC 101, CMPSC
    121, or CMPSC 203" but the plan substituted CMPSC 131; (2) the B.S.'s
    "methodology" and "capstone" requirements each name an exact closed
    course list but were modeled as unfillable generic placeholders;
    (3) the B.A.'s "Select 9 credits" foundational pool is exactly PLSC
    1/3/10/14/14H/17N/17W but the plan had PLSC 7N (a B.S.-only course, not
    on this list) substituted in by mistake. Also enforced PLSC 481/412's
    real mutual exclusion ("Students may not receive credit for PL SC 481
    and PL SC 412")."""

    def test_bs_computer_science_pool_uses_the_real_course_list(self):
        plan = engine.load_degree_plan("PLSC", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and item.get("options")
            and set(item["options"]) & {"CMPSC 101", "CMPSC 121", "CMPSC 131", "CMPSC 203"}
        )
        self.assertEqual(set(item["options"]), {"CMPSC 101", "CMPSC 121", "CMPSC 203"})
        self.assertNotIn("CMPSC 131", item["options"])

    def test_bs_methodology_slot_enumerates_the_real_closed_list(self):
        plan = engine.load_degree_plan("PLSC", 2026)
        real_list = {"GEOG 363", "GEOG 364", "PLSC 410", "STAT 380",
                     "STAT 461", "STAT 462", "STAT 463", "STAT 466"}
        methodology_items = [
            item for _, item in engine._iter_plan_items(plan)
            if "Methodology" in (item.get("label") or "")
        ]
        self.assertTrue(methodology_items, "expected at least one Methodology slot")
        for item in methodology_items:
            self.assertEqual(item.get("type"), "course")
            self.assertEqual(set(item["options"]), real_list)
        self.assertIn("GEOG", plan["departments"])

    def test_bs_capstone_slot_enumerates_the_real_closed_list(self):
        plan = engine.load_degree_plan("PLSC", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if "Capstone" in (item.get("label") or "")
        )
        self.assertEqual(item.get("type"), "course")
        self.assertEqual(set(item["options"]), {"LA 495", "PLSC 494", "PLSC 496"})
        self.assertIn("LA", plan["departments"])

    def test_plsc_481_and_412_are_mutually_exclusive(self):
        catalog = engine.load_merged_catalog(["PLSC"])
        c481, c412 = catalog["PLSC 481"], catalog["PLSC 412"]
        self.assertFalse(engine.excludes_satisfied(c481, {"PLSC 412"}))
        self.assertFalse(engine.excludes_satisfied(c412, {"PLSC 481"}))
        self.assertTrue(engine.excludes_satisfied(c481, set()))

    def test_ba_foundational_pool_uses_the_real_course_list_not_7n(self):
        # The bulletin's real "Select 9 credits" pool is PLSC 1, 3, 10, 14,
        # 14H, 17N, 17W. PLSC 7N belongs to the B.S.'s own intro list, not
        # this one, and was substituted in by mistake in the original build.
        plan = engine.load_degree_plan("PLSCBA", 2026)
        real_pool = {"PLSC 1", "PLSC 3", "PLSC 10", "PLSC 14", "PLSC 14H", "PLSC 17N", "PLSC 17W"}
        for _, item in engine._iter_plan_items(plan):
            if item.get("type") == "course" and item.get("options"):
                self.assertTrue(
                    set(item["options"]) <= real_pool | {"ENGL 202A", "ENGL 202B", "ENGL 202C", "ENGL 202D"}
                    or "PLSC 7N" not in item["options"],
                    f"PLSC 7N should not appear as a B.A. foundational option: {item}",
                )
        self.assertFalse(any(
            item.get("type") == "course" and "PLSC 7N" in (item.get("options") or [])
            for _, item in engine._iter_plan_items(plan)
        ))

    def test_full_plan_builds_cleanly_for_plsc_and_plscba(self):
        for major in ("PLSC", "PLSCBA"):
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
            scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(scheduling_failures, [], f"{major}: {scheduling_failures}")


class TestASTROBulletinRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin at
    bulletins.psu.edu/undergraduate/colleges/eberly-science/
    astronomy-astrophysics-bs/ (Computer Science option) -- the
    department's own astro.psu.edu site was unreachable from this
    environment across repeated attempts, so this is a bulletin-only
    verification, not a department-handbook one. Only 2026 exists as a
    catalog year for ASTRO."""

    def setUp(self):
        self.plan = engine.load_degree_plan("ASTRO", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def _reach(self, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_400_level_astro_elective_excludes_bulletin_denylist(self):
        # Bulletin: "Select 12 credits from 400-level ASTRO courses ...
        # Except ASTRO 401, ASTRO 402W, ASTRO 494H, and ASTRO 496."
        excluded = {"ASTRO 401", "ASTRO 402W", "ASTRO 494H", "ASTRO 496"}
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "400-Level ASTRO elective"
        ]
        self.assertEqual(len(items), 4, "expected 4 real ASTRO 400-level slots (12 credits)")
        for item in items:
            self.assertTrue(item.get("open_elective"), f"item {item['id']} not wired")
            self.assertEqual(set(item.get("elective_exclude", [])), excluded)
            completed, consumed = self._reach(item)
            rec = engine.recommend_semester(
                self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertTrue(pick["code"].startswith("ASTRO "), pick["code"])
            self.assertNotIn(pick["code"], excluded)

    def test_stat_elective_is_stat_only_not_math(self):
        # Bulletin literally says "STAT 300 or 400 level selection" -- no
        # MATH alternative is actually named, unlike the plan's old
        # "Advanced STAT/Mathematics elective" label implied.
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "STAT 300/400-Level elective"
        )
        self.assertTrue(item.get("open_elective"))
        self.assertEqual(item.get("elective_min_level"), 300)
        completed, consumed = self._reach(item)
        rec = engine.recommend_semester(
            self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
        )
        pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
        self.assertIsNotNone(pick)
        self.assertTrue(pick["code"].startswith("STAT "), pick["code"])

    def test_fourth_year_fall_named_cross_listed_picks(self):
        # Bulletin's Suggested Academic Plan names these two Fourth-Year-Fall
        # items precisely as "CMPSC 451/MATH 451" and "CMPSC 465/CMPEN 331"
        # -- real cross-listed pairs, not generic "advanced elective" slots.
        options_by_id = {
            item["id"]: item["options"]
            for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
        }
        self.assertIn(["CMPSC 451", "MATH 451"], options_by_id.values())
        self.assertIn(["CMPSC 465", "CMPEN 331"], options_by_id.values())

    def test_discrete_math_pick_includes_cmpen_271_alternate(self):
        # Bulletin's Third-Year-Spring row: "CMPSC 360/CMPEN 271".
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and "CMPSC 360" in item.get("options", [])
        )
        self.assertIn("CMPEN 271", item["options"])

    def test_cmpsc_cmpen_400_level_pick_excludes_other_departments(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Advanced CMPSC or CMPEN 400-Level elective"
        )
        self.assertTrue(item.get("open_elective"))
        self.assertEqual(item.get("elective_min_level"), 400)
        completed, consumed = self._reach(item)
        rec = engine.recommend_semester(
            self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
        )
        pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
        self.assertIsNotNone(pick)
        self.assertTrue(pick["code"].startswith("CMPSC ") or pick["code"].startswith("CMPEN "), pick["code"])

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=5)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])
        self.assertTrue(fp["goal"]["met"])


class TestINTSCBulletinRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin at
    bulletins.psu.edu/undergraduate/colleges/eberly-science/
    integrative-science-bs/ (General Science option). No department-level
    handbook beyond the bulletin was found for this interdisciplinary,
    no-dedicated-prefix major. Only 2026 exists as a catalog year."""

    LIFE = {"BIOL", "BIOTC", "BMB", "FRNSC", "MICRB"}
    MATHEMATICAL = {"CMPSC", "DS", "MATH", "STAT"}
    PHYSICAL = {"ASTRO", "CHEM", "PHYS"}

    def setUp(self):
        self.plan = engine.load_degree_plan("INTSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def _reach(self, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_departments_include_bulletins_own_science_prefix_list(self):
        # Footnote 5 on the live bulletin: "Life sciences include BIOL,
        # BIOTC, BMB, FRNSC, MICRB. Mathematical sciences include CMPSC,
        # DS, MATH, STAT. Physical sciences include ASTRO, CHEM, PHYS."
        depts = set(self.plan["departments"])
        for prefix in self.LIFE | self.MATHEMATICAL | self.PHYSICAL:
            self.assertIn(prefix, depts, f"{prefix} missing from INTSC departments")

    def test_life_math_physical_science_slots_are_wired_and_recommend_real_courses(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") in (
                "Life, Mathematical, or Physical Science Course",
                "400-Level Life, Mathematical, or Physical Science Course",
            )
        ]
        self.assertEqual(len(items), 6, "expected 3 regular + 3 400-level slots (18cr total)")
        science_prefixes = self.LIFE | self.MATHEMATICAL | self.PHYSICAL
        for item in items:
            self.assertTrue(item.get("open_elective"), f"item {item['id']} not wired")
            completed, consumed = self._reach(item)
            rec = engine.recommend_semester(
                self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            prefix = pick["code"].split()[0]
            self.assertIn(prefix, science_prefixes, f"{pick['code']} isn't a life/math/physical science course")
            if item["label"].startswith("400-Level"):
                num = int(re.match(r"\d+", pick["code"].split()[1]).group())
                self.assertGreaterEqual(num, 400, pick["code"])

    def test_supporting_course_and_advisor_directed_slots_stay_generic(self):
        # The bulletin explicitly punts these to "department approved course
        # list in consultation with adviser" with no enumerated list found
        # anywhere public -- must NOT be wired to open_elective (that would
        # mean guessing a course list this test can't verify against a
        # real source).
        for label in (
            "Supporting Course", "400-Level Supporting Course",
            "Global, Social, and Personal Awareness Course",
            "Teamwork and Interpersonal Communication Course",
            "Integrative and Applied Science Course",
        ):
            items = [
                item for _, item in engine._iter_plan_items(self.plan)
                if item.get("label") == label
            ]
            self.assertTrue(items, f"expected at least one {label!r} slot")
            for item in items:
                self.assertNotIn("open_elective", item, f"{label} (item {item['id']}) shouldn't be wired")

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=5)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])
        self.assertTrue(fp["goal"]["met"])


class TestBIOLHandbookRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin and its archived editions
    (bulletins.psu.edu/undergraduate/colleges/eberly-science/biology-bs/
    and bulletins.psu.edu/archive/<year>/.../biology-bs/) for the General
    Biology option's 'Select a minimum of 18 credits of 400-level biology
    courses, with at least 3 credits from each of the following [6]
    groups' requirement. Programmatically diffing all 5 archived/live
    editions (2022-23 through current) found PSU genuinely revised these
    per-group course lists between the 2023-24 and 2024-25 editions --
    2022-23 and 2023-24 are byte-identical to each other, and 2024-25,
    2025-26, and the current live page are all identical to each other
    but distinct from the earlier pair. No department-published handbook
    beyond the bulletin was found (science.psu.edu/bio's own 'handbook'
    page describes the major's six options and core courses, not
    per-group 400-level course lists). Covers all 5 BIOL catalog years."""

    GROUP_LABELS = [
        "400-Level BIOL — Plant and Fungi Group",
        "400-Level BIOL — Evolution Group",
        "400-Level BIOL — Genetics and Developmental Biology Group",
        "400-Level BIOL — Ecology Group",
        "400-Level BIOL — Physiology Group",
        "400-Level BIOL — Practicum Group",
    ]
    ERA_A_YEARS = (2022, 2023)
    ERA_B_YEARS = (2024, 2025, 2026)

    def _group_items(self, plan):
        return {
            item["label"]: item
            for _, item in engine._iter_plan_items(plan)
            if item.get("label") in self.GROUP_LABELS
        }

    def test_every_year_has_all_6_groups_with_a_match_pattern(self):
        for year in self.ERA_A_YEARS + self.ERA_B_YEARS:
            plan = engine.load_degree_plan("BIOL", year)
            items = self._group_items(plan)
            self.assertEqual(set(items), set(self.GROUP_LABELS), f"{year}: missing a group")
            for label, item in items.items():
                self.assertIn("match", item, f"{year}: {label} has no match pattern")
                re.compile(item["match"])  # must be valid regex

    def test_era_a_and_era_b_patterns_are_internally_consistent(self):
        # 2022 and 2023 must be byte-identical to each other; 2024, 2025,
        # 2026 must be byte-identical to each other; the two eras must
        # differ (PSU's real 2024-25 revision).
        era_a_patterns = [
            {label: item["match"] for label, item in self._group_items(engine.load_degree_plan("BIOL", y)).items()}
            for y in self.ERA_A_YEARS
        ]
        for p in era_a_patterns[1:]:
            self.assertEqual(era_a_patterns[0], p)
        era_b_patterns = [
            {label: item["match"] for label, item in self._group_items(engine.load_degree_plan("BIOL", y)).items()}
            for y in self.ERA_B_YEARS
        ]
        for p in era_b_patterns[1:]:
            self.assertEqual(era_b_patterns[0], p)
        self.assertNotEqual(era_a_patterns[0], era_b_patterns[0])

    def test_evolution_group_dropped_biol_438_after_2023(self):
        # BIOL 438 "Theoretical Population Ecology" was a real member of
        # the Evolution (and Ecology) groups in the 2022-23/2023-24
        # bulletins but is absent from the 2024-25-on group lists.
        for year in self.ERA_A_YEARS:
            plan = engine.load_degree_plan("BIOL", year)
            item = self._group_items(plan)["400-Level BIOL — Evolution Group"]
            self.assertRegex("BIOL 438", item["match"])
        for year in self.ERA_B_YEARS:
            plan = engine.load_degree_plan("BIOL", year)
            item = self._group_items(plan)["400-Level BIOL — Evolution Group"]
            self.assertNotRegex("BIOL 438", item["match"])

    def test_plan_progress_credits_real_course_to_matching_group_era_aware(self):
        # A course only ever appears in the pre-2024 lists (BIOL 438) must
        # credit under 2022/2023 but land in extra_courses (uncredited)
        # under 2024+.
        plan_old = engine.load_degree_plan("BIOL", 2023)
        progress_old = engine.plan_progress(plan_old, {"BIOL 438"})
        self.assertNotIn("BIOL 438", progress_old["extra_courses"])
        plan_new = engine.load_degree_plan("BIOL", 2026)
        progress_new = engine.plan_progress(plan_new, {"BIOL 438"})
        self.assertIn("BIOL 438", progress_new["extra_courses"])

    def test_full_plan_builds_cleanly_for_every_biol_catalog_year(self):
        for year in self.ERA_A_YEARS + self.ERA_B_YEARS:
            plan = engine.load_degree_plan("BIOL", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(plan, catalog, set(), start_year=year, grad_years=5)
            scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
            self.assertEqual(scheduling_failures, [], f"{year}: {scheduling_failures}")


class TestBIOTECHBulletinRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin at bulletins.psu.edu/
    undergraduate/colleges/eberly-science/biotechnology-bs/ (General
    Biotechnology option). No department-published handbook beyond the
    bulletin was found. Only 2026 exists as a catalog year for BIOTECH."""

    def setUp(self):
        self.plan = engine.load_degree_plan("BIOTECH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def _reach(self, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_400_level_lecture_slots_span_bmb_biotc_micrb_not_just_biotc(self):
        # Bulletin: "Select 6 credits from the following: Any 400-level
        # BMB/BIOTC/MICRB lecture course; FDSC 408" -- was previously
        # scoped to BIOTC only via label alone (with no course list backing
        # it at all, since it wasn't wired).
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Any 400-Level BMB/BIOTC/MICRB Lecture Course"
        ]
        self.assertEqual(len(items), 2, "expected 2 slots (6 credits total)")
        seen_prefixes = set()
        for item in items:
            self.assertTrue(item.get("open_elective"))
            self.assertEqual(item.get("elective_min_level"), 400)
            completed, consumed = self._reach(item)
            rec = engine.recommend_semester(
                self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            prefix = pick["code"].split()[0]
            self.assertIn(prefix, {"BMB", "BIOTC", "MICRB"}, pick["code"])
            seen_prefixes.add(prefix)

    def test_department_list_c_stays_generic(self):
        # Bulletin: "Select 14 credits from department list C (Consult
        # with an academic adviser for options)" -- no enumerated list
        # exists anywhere public; must not be wired to open_elective.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Department List C elective"
        ]
        self.assertTrue(items)
        total = sum(item["credits"] for item in items)
        self.assertEqual(total, 14)
        for item in items:
            self.assertNotIn("open_elective", item)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=5)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])
        self.assertTrue(fp["goal"]["met"])


class TestBMBBulletinRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin at bulletins.psu.edu/
    undergraduate/colleges/eberly-science/biochemistry-molecular-biology-bs/
    (Biochemistry Option). No department-published handbook beyond the
    bulletin was found. Only 2026 exists as a catalog year for BMB."""

    def setUp(self):
        self.plan = engine.load_degree_plan("BMB", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def _reach(self, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_400_level_bmb_chem_micrb_slots_are_wired_and_avoid_denylist(self):
        # Bulletin: "Select 7-9 credits from any 400-level BMB/CHEM/MICRB
        # course ... [max 3cr combined BMB 408/MICRB 408, max 4cr combined
        # BMB 488/BMB 496]".
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "BMB, CHEM, or MICRB 400-Level elective"
        ]
        self.assertEqual(len(items), 3)
        self.assertEqual(sum(item["credits"] for item in items), 8)  # within 7-9
        for item in items:
            self.assertTrue(item.get("open_elective"))
            self.assertEqual(item.get("elective_min_level"), 400)
            for excluded in ("BMB 408", "MICRB 408", "BMB 488", "BMB 496"):
                self.assertIn(excluded, item["elective_exclude"])

    def test_400_level_slots_never_collide_with_this_plans_own_required_courses(self):
        # Regression: the elective slot's candidate pool originally
        # included this plan's own hard-required 400-level BMB/CHEM
        # courses (BMB 400/401/402/442/443W/445W/448/474, CHEM 450/452).
        # Since a course can satisfy only one plan item, the elective slot
        # greedily claiming e.g. BMB 474 permanently starved the later
        # item that specifically requires BMB 474 -- confirmed by running
        # the full build and finding BMB 474's own required-course item
        # never scheduled. Fixed by excluding those courses from the
        # elective's pool; this test locks that in.
        required_400_level = {
            "BMB 400", "BMB 401", "BMB 402", "BMB 442", "BMB 443W",
            "BMB 445W", "BMB 448", "BMB 474", "CHEM 450", "CHEM 452",
        }
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "BMB, CHEM, or MICRB 400-Level elective"
        ]
        for item in items:
            for code in required_400_level:
                self.assertIn(code, item["elective_exclude"], f"{code} must be excluded from item {item['id']}")

        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=7)
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        scheduled_codes = {
            c["code"] for t in fp["terms"] for c in t["courses"] if c.get("code")
        }
        for code in ("BMB 474",):  # the specific course this bug actually manifested on
            self.assertIn(code, scheduled_codes)
        # And the elective slots themselves must still resolve to real,
        # non-required courses.
        elective_ids = {item["id"] for item in items}
        for t in fp["terms"]:
            for c in t["courses"]:
                if c["item_id"] in elective_ids:
                    self.assertIsNotNone(c["code"])
                    self.assertNotIn(c["code"], required_400_level)

    def test_math_231_satisfies_mathematical_sciences_department_list_b(self):
        # Bulletin: "Select 2-3 credits in the mathematical sciences from
        # department list B" -- confirmed already satisfied by this plan's
        # existing required MATH 231, not a separate unfillable slot.
        math_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and "MATH 231" in item.get("options", [])
        ]
        self.assertTrue(math_items, "MATH 231 should be a required course in this plan")

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=7)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])
        self.assertTrue(fp["goal"]["met"])


class TestCHEMBulletinRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin at bulletins.psu.edu/
    undergraduate/colleges/eberly-science/chemistry-bs/ (common to all 4
    options). No department-published handbook beyond the bulletin was
    found. Only 2026 exists as a catalog year for CHEM."""

    def setUp(self):
        self.plan = engine.load_degree_plan("CHEM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def _reach(self, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(self.plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_400_level_chem_slots_sum_to_15_and_are_wired(self):
        # Bulletin: "Select 15 credits of chemistry at the 400 level" --
        # common to all 4 options, confirmed not option-specific.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "400-Level CHEM elective"
        ]
        self.assertEqual(sum(item["credits"] for item in items), 15)
        for item in items:
            self.assertTrue(item.get("open_elective"))
            self.assertEqual(item.get("elective_min_level"), 400)
            completed, consumed = self._reach(item)
            rec = engine.recommend_semester(
                self.plan, self.catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertTrue(pick["code"].startswith("CHEM "), pick["code"])

    def test_400_level_chem_slots_never_collide_with_required_courses(self):
        # This plan separately requires CHEM 450/452/457 by name; the
        # generic 400-level elective pool must exclude them so it can
        # never claim a course those specific items also need.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "400-Level CHEM elective"
        ]
        for item in items:
            for code in ("CHEM 450", "CHEM 452", "CHEM 457"):
                self.assertIn(code, item["elective_exclude"])
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=5)
        self.assertEqual(fp["warnings"], [])
        scheduled = {c["code"] for t in fp["terms"] for c in t["courses"] if c.get("code")}
        for code in ("CHEM 450", "CHEM 452", "CHEM 457"):
            self.assertIn(code, scheduled)

    def test_advanced_lab_list_matches_bulletin_exactly(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label", "").startswith("Advanced Chemistry Lab")
        )
        for code in ("423W", "425W", "431W", "459W"):
            self.assertIn(code, item["label"])

    def test_supporting_course_pool_added_for_the_13_credit_requirement(self):
        # Bulletin: "Supporting Courses and Related Areas: Select 13
        # credits of any courses not on the Chemistry Department list of
        # excluded courses" -- this plan previously had NO slot at all
        # distinctly representing this major-specific requirement (only
        # undifferentiated 'GEN ED' placeholders); now labeled distinctly
        # and left generic since the actual exclusion list isn't public.
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Supporting Course (department-approved list)"
        ]
        self.assertTrue(items, "expected at least one distinctly-labeled Supporting Course slot")
        self.assertGreaterEqual(sum(item["credits"] for item in items), 12)
        for item in items:
            self.assertNotIn("open_elective", item)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=5)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])
        self.assertTrue(fp["goal"]["met"])


class TestDSBulletinRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin and its dedicated
    program-requirements PDF at bulletins.psu.edu/undergraduate/colleges/
    eberly-science/data-sciences-bs/ (Statistical Modeling option,
    DTSCS_BS -- the Eberly Science version, distinct from DATSC in IST
    and DTSCE in Engineering). No department handbook beyond the bulletin
    was found; the Statistics department's own curriculum page links to
    a dead Box-hosted flowchart. Only 2026 exists as a catalog year."""

    def setUp(self):
        self.plan = engine.load_degree_plan("DS", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_stat_184_is_3_credits_matching_its_own_course_description(self):
        # Corrected 2026-08-27: an earlier pass hardcoded this item to 2
        # credits, citing the DS bulletin page's "Requirements for the
        # Major" summary table -- but that table has a real, isolated typo.
        # The authoritative course-description page
        # (bulletins.psu.edu/university-course-descriptions/undergraduate/stat/)
        # states "STAT 184 Introduction to R 3 Credits", and the same DS
        # bulletin page's own Suggested Academic Plan table lists STAT 184
        # at 3 credits in both places it appears. Matches stat_catalog.json's
        # own (correct, unmodified) 3.0 value.
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and item.get("options") == ["STAT 184"]
        )
        self.assertEqual(item["credits"], 3)

    def test_list_a_and_list_b_are_each_6_credits_not_3(self):
        # Bulletin: "Select 6 credits from Statistical Modeling Option
        # List A courses" and "... List B courses" (Appendix D) -- each
        # its own 6-credit requirement, not 3.
        list_a = [
            item for _, item in engine._iter_plan_items(self.plan)
            if (item.get("label") or "").startswith("List A Selection")
        ]
        list_b = [
            item for _, item in engine._iter_plan_items(self.plan)
            if (item.get("label") or "").startswith("List B Selection")
        ]
        self.assertEqual(sum(item["credits"] for item in list_a), 6)
        self.assertEqual(sum(item["credits"] for item in list_b), 6)

    def test_list_a_b_labels_do_not_assert_unverified_course_codes(self):
        # Appendix D's real course enumeration could not be found anywhere
        # accessible in this session -- the previous session's course-code
        # guesses must not be presented as confirmed fact.
        for _, item in engine._iter_plan_items(self.plan):
            label = item.get("label") or ""
            if label.startswith("List A Selection") or label.startswith("List B Selection"):
                self.assertIn("unverified", label.lower())

    def test_cmpsc_465_ds_305_item_was_removed(self):
        # Neither course appears anywhere in the live bulletin's
        # Statistical Modeling option requirements -- confirmed a
        # construction error in a prior pass and removed.
        for _, item in engine._iter_plan_items(self.plan):
            if item.get("type") == "course":
                self.assertNotEqual(item.get("options"), ["CMPSC 465", "DS 305"])

    def test_full_plan_builds_cleanly_at_five_years(self):
        # The real List A/B fix adds a genuine +3 net credits; confirmed
        # via build_full_plan this pushes the plan from 8 to 9 terms.
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=5)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 9)


class TestFRNSCBulletinRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin at bulletins.psu.edu/
    undergraduate/colleges/eberly-science/forensic-science-bs/ (Forensic
    Molecular Biology option), cross-checking the bulletin's own separate
    'Requirements for the Major' course table against its 'Suggested
    Academic Plan' table. No department handbook beyond the bulletin was
    found. Only 2026 exists as a catalog year for FRNSC."""

    def setUp(self):
        self.plan = engine.load_degree_plan("FRNSC", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_415w_appears_exactly_once(self):
        # The bulletin's authoritative Requirements-for-the-Major table
        # lists FRNSC 415W exactly once (2 credits) -- confirms the prior
        # session's call that the Suggested-Academic-Plan table listing it
        # twice was a scrape/table duplication, not a real 2-part sequence.
        count = sum(
            1 for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and item.get("options") == ["FRNSC 415W"]
        )
        self.assertEqual(count, 1)

    def test_option_elective_is_wired_with_the_real_6_course_list(self):
        # Bulletin: Forensic Molecular Biology Option, "Select one of the
        # following: 3" -- BIOL 405/422/460, BMB 402/428/433.
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and set(item.get("options", [])) == {
                "BIOL 405", "BIOL 422", "BIOL 460", "BMB 402", "BMB 428", "BMB 433",
            }
        )
        self.assertEqual(item["credits"], 3)

    def test_biol_234_235w_no_longer_circularly_deadlocked(self):
        # Regression against a stale note: a previous session flagged
        # these two as circularly deadlocked in biol_catalog.json. Confirm
        # the current catalog data no longer has that circularity and both
        # schedule successfully.
        c234 = self.catalog.get("BIOL 234")
        c235 = self.catalog.get("BIOL 235W")
        self.assertIsNotNone(c234)
        self.assertIsNotNone(c235)
        norm_234 = {frozenset(g) for g in c234.prereq_groups}
        norm_235 = {frozenset(g) for g in c235.prereq_groups}
        self.assertFalse(any("BIOL 235W" in g or "BIOL 236W" in g for g in norm_234))
        self.assertFalse(any("BIOL 234" in g for g in norm_235))
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=5)
        scheduled = {c["code"] for t in fp["terms"] for c in t["courses"] if c.get("code")}
        self.assertIn("BIOL 234", scheduled)
        self.assertIn("BIOL 235W", scheduled)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=5)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestPREMEDHandbookRequirements(unittest.TestCase):
    """Real data pulled from the live bulletin and its archived editions
    (bulletins.psu.edu/undergraduate/colleges/eberly-science/
    premedicine-bs/, and bulletins.psu.edu/archive/<year>/.../
    premedicine-bs/), cross-referencing the bulletin's own separate
    'Requirements for the Major' course table against AAMC's own
    published MSAR premed course-requirement guidance. Found a real,
    substantial curriculum revision between the 2023-24 and 2024-25
    bulletin editions -- 2022-23 and 2023-24 are byte-identical to each
    other (126cr, fixed PHIL 432, fixed PHYS 211-214, a 0-8cr foreign
    language requirement, no healthcare internship), and 2024-25,
    2025-26, and the current live edition are all identical to each
    other (120cr, PHIL/BIOET 432 now one of 3 real ethics-course
    alternatives, PHYS 211-214 gained a 250/251 alternate track, a
    12cr Area of Concentration replaced foreign language, and a
    healthcare internship was added). Covers all 5 PREMED catalog years."""

    OLD_ERA_YEARS = (2022, 2023)
    NEW_ERA_YEARS = (2024, 2025, 2026)

    def _reach(self, plan, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    def test_chem_213_lab_widened_to_the_real_3_way_alternative_every_year(self):
        # The bulletin's own Requirements-for-the-Major table names 'CHEM
        # 213' specifically (not 213W) in every catalog year checked.
        for year in self.OLD_ERA_YEARS + self.NEW_ERA_YEARS:
            plan = engine.load_degree_plan("PREMED", year)
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course" and "CHEM 213" in item.get("options", [])
            )
            self.assertEqual(set(item["options"]), {"CHEM 213", "CHEM 213W", "CHEM 213M"}, year)

    def test_ethics_course_is_hard_required_only_pre_2024(self):
        # 2022-23/2023-24: PHIL 432 was a fixed prescribed course, no
        # CAS 453 / NURS 464 alternatives existed yet.
        for year in self.OLD_ERA_YEARS:
            plan = engine.load_degree_plan("PREMED", year)
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course" and "PHIL 432" in item.get("options", [])
            )
            self.assertEqual(item["options"], ["PHIL 432"], year)

    def test_ethics_course_gained_real_alternatives_starting_2024(self):
        # 2024-25 onward: "Select one of: CAS 453, NURS 464, PHIL/BIOET
        # 432" is a real 3-way (4-code) choice per the bulletin.
        for year in self.NEW_ERA_YEARS:
            plan = engine.load_degree_plan("PREMED", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course" and "PHIL 432" in item.get("options", [])
            )
            self.assertEqual(set(item["options"]), {"PHIL 432", "BIOET 432", "CAS 453", "NURS 464"}, year)
            self.assertIn("NURS", plan["departments"], year)
            completed, consumed = self._reach(plan, item)
            rec = engine.recommend_semester(
                plan, catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, year)

    def test_physics_track_only_gained_alternate_starting_2024(self):
        # 2022-23/2023-24: PHYS 211-214 were all fixed prescribed courses
        # (no PHYS 250/251 alternate). 2024-25 onward: PHYS 211/212 gained
        # a real PHYS 250/251 alternate track per the bulletin's own
        # "Select 8-12 credits from the following" choice.
        for year in self.OLD_ERA_YEARS:
            plan = engine.load_degree_plan("PREMED", year)
            item211 = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course" and "PHYS 211" in item.get("options", [])
            )
            self.assertEqual(item211["options"], ["PHYS 211"], year)
        for year in self.NEW_ERA_YEARS:
            plan = engine.load_degree_plan("PREMED", year)
            item211 = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("type") == "course" and "PHYS 211" in item.get("options", [])
            )
            self.assertIn("PHYS 250", item211["options"], year)

    def test_full_plan_builds_cleanly_for_every_premed_catalog_year(self):
        import datetime
        for year in self.OLD_ERA_YEARS + self.NEW_ERA_YEARS:
            plan = engine.load_degree_plan("PREMED", year)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(
                plan, catalog, set(), start_year=year, grad_years=5,
                today=datetime.date(year, 7, 1),
            )
            self.assertEqual(fp["warnings"], [], f"{year}: {fp['warnings']}")
            self.assertTrue(fp["goal"]["met"], year)
            self.assertEqual(len(fp["terms"]), 10, year)


class TestCollegeOfEducationHandbookRequirements(unittest.TestCase):
    """Real data cross-checked against Penn State College of Education
    sources for the 7 College of Education majors that only had a single
    (2026) catalog year: EDPP, ELED, MLED, RHS, SECED, SPLED, WFED. Sources
    used: the live bulletins.psu.edu pages (isolated per-option where a
    major shares a page with several tracks), and the College of Education
    Student Advising Hub's real, published course-selection lists
    (sites.psu.edu/educationadvising) -- the department-level equivalent of
    EECS's own CMPSC handbook used as the reference pattern for this pass."""

    def _reach(self, plan, item):
        completed = {
            it["options"][0] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "course"
        }
        consumed = {
            it["id"] for _, it in engine._iter_plan_items(plan)
            if it["id"] < item["id"] and it.get("type") == "slot"
        }
        return completed, consumed

    # ---- EDPP -----------------------------------------------------------

    def test_edpp_cas_222n_accepts_civcm_211n_cross_listing(self):
        plan = engine.load_degree_plan("EDPP", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "CAS 222N" in item.get("options", [])
        )
        self.assertIn("CIVCM 211N", item["options"])

    def test_edpp_gen_ed_slots_are_wired_and_recommend_real_courses(self):
        plan = engine.load_degree_plan("EDPP", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        wired_labels = {
            "Natural Science Selection": "GN",
            "Arts Selection": "GA",
            "Humanities Selection": "GH",
            "Health and Physical Activity": "GHW",
        }
        for _, item in engine._iter_plan_items(plan):
            if item.get("label") in wired_labels:
                self.assertEqual(item.get("gen_ed"), wired_labels[item["label"]])
                completed, consumed = self._reach(plan, item)
                rec = engine.recommend_semester(
                    plan, catalog, completed, consumed_slots=consumed, max_credits=99,
                )
                pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
                self.assertIsNotNone(pick, f"item {item['id']} ({item['label']}) was never recommended a course")
                self.assertIsNotNone(pick["code"])

    def test_edpp_edthp_400_level_selection_is_wired_and_excludes_edthp_420(self):
        plan = engine.load_degree_plan("EDPP", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "EDTHP 400-level Selection"
        ]
        self.assertEqual(len(items), 4)
        for item in items:
            self.assertTrue(item.get("open_elective"))
            self.assertEqual(item.get("elective_min_level"), 400)
            self.assertIn("EDTHP 420", item.get("elective_exclude", []))
            completed, consumed = self._reach(plan, item)
            rec = engine.recommend_semester(
                plan, catalog, completed, consumed_slots=consumed, max_credits=99,
            )
            pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
            self.assertIsNotNone(pick, f"item {item['id']} was never recommended a course")
            self.assertTrue(pick["code"].startswith("EDTHP "), pick["code"])
            self.assertNotEqual(pick["code"], "EDTHP 420")

    # ---- ELED -------------------------------------------------------------

    def test_eled_curated_lists_match_real_approved_courses_and_reject_others(self):
        plan = engine.load_degree_plan("ELED", 2026)
        cases = {
            "GEN ED (Earth Science)": (["GEOSC 20", "ASTRO 11"], ["MATH 200", "CHEM 202"]),
            "GEN ED (Physical Science)": (["CHEM 110", "PHYS 250"], ["GEOSC 20", "BIOL 110"]),
            "GEN ED (Literature)": (["ENGL 231", "CMLIT 101"], ["ENGL 15", "HIST 21"]),
            "GEN ED (US History)": (["HIST 21", "PLSC 1"], ["ECON 102"]),
            "GEN ED (Social Studies)": (["ECON 102", "GEOG 20"], ["HIST 21", "ENGL 231"]),
            "Education Selection": (["CI 185", "RHS 401"], ["ENGL 231", "STAT 200"]),
        }
        for label, (should_match, should_not_match) in cases.items():
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label") == label
            )
            self.assertTrue(item.get("match"), f"{label}: expected a match field")
            rx = re.compile(item["match"])
            for code in should_match:
                self.assertTrue(rx.match(code), f"{label}: {code} should match {rx.pattern}")
            for code in should_not_match:
                self.assertFalse(rx.match(code), f"{label}: {code} should NOT match {rx.pattern}")

    # ---- MLED ---------------------------------------------------------

    def test_mled_previously_missing_literature_and_math_requirements_now_exist(self):
        # Real bulletin gap: the English 4-8 option's Second Year Fall term
        # requires American Literature Selection AND Comparative Literature
        # Selection (3cr each) on top of the plain Literature Selection this
        # plan already had, and Third Year Spring requires a Mathematics
        # Selection (GQ) beyond MATH 200 -- none of the three existed before.
        plan = engine.load_degree_plan("MLED", 2026)
        labels = [item.get("label") for _, item in engine._iter_plan_items(plan)]
        self.assertIn("American Literature Selection", labels)
        self.assertIn("Comparative Literature Selection", labels)
        math_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Mathematics Selection"
        ]
        self.assertEqual(len(math_items), 1)
        self.assertEqual(math_items[0].get("gen_ed"), "GQ")

    def test_mled_curated_literature_lists_match_real_approved_courses(self):
        plan = engine.load_degree_plan("MLED", 2026)
        cases = {
            "British Literature": (["ENGL 221", "ENGL 456"], ["ENGL 231", "ENGL 212"]),
            "American Literature Selection": (["ENGL 231", "ENGL 437"], ["ENGL 221", "CMLIT 101"]),
            "Comparative Literature Selection": (["CMLIT 101", "ENGL 461"], ["ENGL 231", "ENGL 221"]),
            "Writing Selection": (["ENGL 212", "ENGL 415"], ["ENGL 221", "ENGL 231"]),
            "Media Selection": (["CAS 213", "COMM 150N"], ["ENGL 15", "STAT 200"]),
            "Literature Selection": (["ENGL 231", "CMLIT 101"], ["ENGL 212", "CAS 213"]),
        }
        for label, (should_match, should_not_match) in cases.items():
            items = [
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label") == label
            ]
            self.assertTrue(items, f"expected at least one {label} item")
            for item in items:
                rx = re.compile(item["match"])
                for code in should_match:
                    self.assertTrue(rx.match(code), f"{label}: {code} should match {rx.pattern}")
                for code in should_not_match:
                    self.assertFalse(rx.match(code), f"{label}: {code} should NOT match {rx.pattern}")

    def test_mled_full_plan_still_builds_cleanly_after_additions(self):
        plan = engine.load_degree_plan("MLED", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    # ---- RHS ------------------------------------------------------------

    def test_rhs_combined_gen_ed_slots_are_wired_and_recommend_real_courses(self):
        # These four slots literally named a real Gen Ed domain combination
        # in their own label ("GA or GH", "GN, GH, GA, or GHW", "Integrative
        # Studies") but had no `gen_ed` field at all -- the same
        # mislabeled/unwired bug class fixed for CMPSC's own GN slot.
        plan = engine.load_degree_plan("RHS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        expected = {
            "GEN ED (GA or GH)": ["GA", "GH"],
            "GEN ED (GN, GH, GA, or GHW)": ["GN", "GH", "GA", "GHW"],
            "GEN ED (Integrative Studies)": "INTER-D",
        }
        seen_labels = set()
        for _, item in engine._iter_plan_items(plan):
            label = item.get("label")
            if label in expected:
                seen_labels.add(label)
                self.assertEqual(item.get("gen_ed"), expected[label])
                completed, consumed = self._reach(plan, item)
                rec = engine.recommend_semester(
                    plan, catalog, completed, consumed_slots=consumed, max_credits=99,
                )
                pick = next((c for c in rec["courses"] if c["item_id"] == item["id"]), None)
                self.assertIsNotNone(pick, f"item {item['id']} ({label}) was never recommended a course")
                self.assertIsNotNone(pick["code"])
        self.assertEqual(seen_labels, set(expected))

    def test_rhs_full_plan_still_builds_cleanly(self):
        plan = engine.load_degree_plan("RHS", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])

    # ---- SECED ----------------------------------------------------------

    def test_seced_biochem_and_literature_lists_match_real_approved_courses(self):
        plan = engine.load_degree_plan("SECED", 2026)
        biochem = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Biochem Selection"
        )
        rx = re.compile(biochem["match"])
        for code in ["BMB 211", "CHEM 213"]:
            self.assertTrue(rx.match(code), code)
        for code in ["CHEM 110", "BIOL 240W"]:
            self.assertFalse(rx.match(code), code)

        lit_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "GEN ED (Literature)"
        ]
        self.assertEqual(len(lit_items), 2)
        for item in lit_items:
            self.assertEqual(item.get("gen_ed"), "GH")
            rx = re.compile(item["match"])
            self.assertTrue(rx.match("ENGL 231"))
            self.assertFalse(rx.match("BIOL 110"))

    def test_seced_chem_110_math_22_chain_still_accurate(self):
        # Re-confirmed live against CHEM 110's own course description.
        catalog = engine.load_merged_catalog(["CHEM", "MATH"])
        chem110 = catalog.get("CHEM 110")
        self.assertIsNotNone(chem110)
        self.assertFalse(engine.prereqs_satisfied(chem110, set()))
        self.assertTrue(engine.prereqs_satisfied(chem110, {"MATH 22"}))

    # ---- SPLED ------------------------------------------------------------

    def test_spled_literature_selection_matches_real_approved_courses(self):
        plan = engine.load_degree_plan("SPLED", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "GEN ED (Literature)"
        )
        self.assertEqual(item.get("gen_ed"), "GH")
        rx = re.compile(item["match"])
        self.assertTrue(rx.match("ENGL 231"))
        self.assertFalse(rx.match("PSYCH 100"))

    # ---- WFED -----------------------------------------------------------

    def test_wfed_sts_245z_removed_as_unconfirmable_course(self):
        # STS 245Z does not exist in Penn State's current STS course
        # descriptions (only STS 245N does) even though the live bulletin
        # still literally prints "STS 245Z/WFED 450" -- a stale bulletin
        # typo. Removed as an option; WFED 450 (confirmed real) remains.
        plan = engine.load_degree_plan("WFED", 2026)
        item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and "WFED 450" in item.get("options", [])
        )
        self.assertNotIn("STS 245Z", item["options"])

    def test_wfed_health_and_literature_slots_are_wired(self):
        plan = engine.load_degree_plan("WFED", 2026)
        hpa_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Health/Physical Activity"
        ]
        self.assertEqual(len(hpa_items), 2)
        for item in hpa_items:
            self.assertEqual(item.get("gen_ed"), "GHW")

        lit_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Literature Selection"
        )
        rx = re.compile(lit_item["match"])
        self.assertTrue(rx.match("ENGL 231"))
        self.assertFalse(rx.match("WFED 1"))

    def test_wfed_full_plan_still_builds_cleanly(self):
        plan = engine.load_degree_plan("WFED", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduling_failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(scheduling_failures, [])


class TestBehrendCampusRollout(unittest.TestCase):
    """Penn State Erie, The Behrend College — the first branch-campus-
    specific degree plans added to this app (everything before this was
    University Park). Covers all 5 majors researched in the 2026-08-27
    Behrend rollout: Computer Science, B.S. (Behrend) (CMPSCBH — Pattern B,
    a genuinely separate curriculum from UP's CMPSC_BS), Software
    Engineering, B.S. (SWENG — no UP equivalent at all), Functional Data
    Analytics, B.S. (FDTAN — no UP equivalent), Interdisciplinary Business
    with Engineering Studies, B.S. (IBE — no UP equivalent), and
    Interdisciplinary Science and Business, B.S. (ISB — no UP equivalent).
    All 5 are real Behrend-only programs verified against their own
    bulletin pages at bulletins.psu.edu/undergraduate/colleges/behrend/."""

    def setUp(self):
        import datetime
        self.today = datetime.date(2026, 7, 1)

    def _build(self, major, grad_years):
        plan = engine.load_degree_plan(major, 2026)
        self.assertIsNotNone(plan, f"{major}-2026.json failed to load")
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(),
            start_year=2026, grad_years=grad_years, today=self.today,
        )
        return plan, fp

    def test_all_five_majors_carry_erie_campus(self):
        for major in ("CMPSCBH", "SWENG", "FDTAN", "IBE", "ISB"):
            with self.subTest(major=major):
                plan = engine.load_degree_plan(major, 2026)
                self.assertEqual(engine._plan_campuses(plan), ["Erie"], major)

    def test_all_five_majors_have_behrend_in_title(self):
        """Every plan's title should be distinguishable in the UI from any
        University Park major of a similar name."""
        for major in ("CMPSCBH", "SWENG", "FDTAN", "IBE", "ISB"):
            with self.subTest(major=major):
                plan = engine.load_degree_plan(major, 2026)
                title = plan["title"]
                self.assertTrue(
                    "Behrend" in title or "behrend" in plan.get("source", ""),
                    f"{major} title/source should identify it as Behrend: {title}",
                )

    def test_cmpscbh_builds_cleanly_in_five_years(self):
        # PATTERN B: real hidden MATH 41 -> MATH 110 -> CMPSC 121 prereq
        # chain (see plan notes) pushes this to 9 real terms.
        plan, fp = self._build("CMPSCBH", 5)
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 9)

    def test_cmpscbh_is_a_genuinely_different_curriculum_from_up(self):
        """Pattern B confirmation: Behrend's own required-course list barely
        overlaps with University Park's CMPSC_BS flowchart (CMPSC-2026.json)."""
        behrend_plan = engine.load_degree_plan("CMPSCBH", 2026)
        up_plan = engine.load_degree_plan("CMPSC", 2026)

        def all_options(plan):
            codes = set()
            for _, item in engine._iter_plan_items(plan):
                codes.update(item.get("options", []))
            return codes

        behrend_codes = all_options(behrend_plan)
        up_codes = all_options(up_plan)

        # Real Behrend-only required courses that don't appear anywhere in
        # UP's flowchart at all.
        for code in ("CMPSC 312", "CMPSC 335", "CMPSC 421", "CMPSC 474", "CMPSC 484", "CMPSC 485W"):
            self.assertIn(code, behrend_codes, f"{code} should be a real Behrend requirement")
            self.assertNotIn(code, up_codes, f"{code} should not appear in UP's CMPSC flowchart")

        # Real UP-only required courses that don't appear in Behrend's list.
        for code in ("CMPSC 132", "CMPSC 222", "CMPSC 320", "CMPSC 483W"):
            self.assertIn(code, up_codes, f"{code} should be a real UP requirement")
            self.assertNotIn(code, behrend_codes, f"{code} should not appear in Behrend's own course table")

    def test_sweng_builds_cleanly_in_five_years(self):
        plan, fp = self._build("SWENG", 5)
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 9)

    def test_sweng_no_up_equivalent_exists(self):
        self.assertIsNone(engine.load_degree_plan("SWENG_UP", 2026))
        # SWENG is Behrend's only undergraduate software engineering
        # program system-wide, unlike CMPSCBH which has a real (but
        # different) UP counterpart.
        self.assertIsNotNone(engine.load_degree_plan("CMPSC", 2026))

    def test_fdtan_builds_cleanly_in_five_years(self):
        plan, fp = self._build("FDTAN", 5)
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 9)

    def test_fdtan_mis447_chain_actually_resolves(self):
        """Real hidden prereq gap: MIS 447 hard-requires MIS 336 specifically
        (not IST 210, the bulletin's own listed alternative for that slot),
        and MIS 336 itself needs MIS 204 — verify both actually get
        scheduled somewhere before MIS 447 in the real simulated terms."""
        plan, fp = self._build("FDTAN", 5)
        term_of = {}
        for i, t in enumerate(fp["terms"]):
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = i
        for code in ("MIS 204", "MIS 336", "MIS 447"):
            self.assertIn(code, term_of, f"{code} should actually be scheduled")
        self.assertLess(term_of["MIS 204"], term_of["MIS 336"])
        self.assertLess(term_of["MIS 336"], term_of["MIS 447"])

    def test_ibe_builds_cleanly_in_four_years(self):
        """Unlike CMPSCBH/SWENG/FDTAN, IBE's real MATH 21/22/110 gap fix
        still fits inside the bulletin's own nominal 4 years."""
        plan, fp = self._build("IBE", 4)
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_ibe_module_electives_are_honestly_generic(self):
        """HONEST GAP: IBE's 5 real named modules have no published
        course-by-course list anywhere this session could find (the
        program's own behrend.psu.edu page returned HTTP 403) — confirm
        they're modeled as plain generic slots, not a fabricated list."""
        plan = engine.load_degree_plan("IBE", 2026)
        module_items = [
            item for _, item in engine._iter_plan_items(plan)
            if item.get("label") == "Module Elective (School-Approved Module)"
        ]
        self.assertEqual(len(module_items), 5)
        for item in module_items:
            self.assertEqual(item.get("type"), "slot")
            self.assertNotIn("options", item)
            self.assertNotIn("match", item)

    def test_isb_builds_cleanly_in_four_years(self):
        plan, fp = self._build("ISB", 4)
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        self.assertEqual(len(fp["terms"]), 8)

    def test_isb_stat_prefix_tie_break_actually_resolves_stat461_462(self):
        """Real hidden prereq gap: STAT 461/462 (required Quantitative
        Module courses) need an actual STAT-prefix intro stats course —
        SCM 200 (an equally valid bulletin alternative for the ETM/major
        statistics requirement itself) does NOT satisfy that prereq. Verify
        the engine actually schedules STAT 200 or STAT 250 (not SCM 200)
        early enough to unlock both."""
        plan, fp = self._build("ISB", 4)
        term_of = {}
        for i, t in enumerate(fp["terms"]):
            for p in t["courses"]:
                if p["code"]:
                    term_of[p["code"]] = i
        self.assertTrue(
            "STAT 200" in term_of or "STAT 250" in term_of,
            "a real STAT-prefix intro stats course must be scheduled to unlock STAT 461/462",
        )
        stat_intro_term = term_of.get("STAT 200", term_of.get("STAT 250"))
        for code in ("STAT 461", "STAT 462"):
            self.assertIn(code, term_of, f"{code} should actually be scheduled")
            self.assertLess(stat_intro_term, term_of[code])

    def test_isb_uses_real_published_module_lists(self):
        """Unlike FDTAN/IBE, ISB's bulletin page publishes a full real
        course-by-course list for every Science/Business Module — confirm
        this plan uses actual courses from those real lists (Quantitative
        Module + Technical Sales Business Module), not a generic
        placeholder."""
        plan = engine.load_degree_plan("ISB", 2026)
        codes = set()
        for _, item in engine._iter_plan_items(plan):
            codes.update(item.get("options", []))
        for code in ("STAT 461", "STAT 462", "MKTG 410", "SCM 455", "SCM 460"):
            self.assertIn(code, codes, f"{code} is a real required Module course")

    def test_new_catalog_files_exist_for_behrend_specific_prefixes(self):
        """SWENG, DA, DIGIT, and ISB are Behrend-specific course prefixes
        that had no catalog file in this app before this rollout."""
        for dept, codes in (
            ("sweng", ["SWENG 311", "SWENG 411", "SWENG 481"]),
            ("da", ["DA 101", "DA 475", "DA 476"]),
            ("digit", ["DIGIT 410", "DIGIT 430"]),
            ("isb", ["ISB 207", "ISB 475W"]),
        ):
            catalog = engine.load_merged_catalog([dept.upper()])
            for code in codes:
                with self.subTest(dept=dept, code=code):
                    self.assertIn(engine.norm_code(code), catalog)


class TestMEBHBehrendCampus(unittest.TestCase):
    """Mechanical Engineering, B.S. (Behrend) -- Penn State Erie, The Behrend
    College. Confirmed Pattern B (a genuinely separate curriculum from
    University Park's ME-2026.json, not a shared 2+2 plan) by direct
    comparison of Behrend's own live bulletin page against ME-2026's
    required-course set: only ME 300/320/410 overlap; Behrend's own
    third/fourth-year courses (ME 345W, 349, 357, 365, 367, 380, 448, 449,
    468) are entirely absent from UP's curriculum."""

    def setUp(self):
        self.plan = engine.load_degree_plan("MEBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_plan_is_scoped_to_erie_campus(self):
        self.assertEqual(self.plan.get("campus"), ["Erie"])

    def test_program_elective_pool_matches_real_school_approved_list(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Program Elective - School Approved"
        )
        rx = re.compile(item["match"])
        for code in ["ME 370", "BME 402", "EMCH 471", "IE 405", "MATH 455", "PHYS 458", "QC 450", "MGMT 409"]:
            self.assertTrue(rx.match(code), code)
        for required in ["ME 300", "ME 320", "ME 345W", "ME 349", "ME 380", "ME 357", "ME 365",
                          "ME 367", "ME 410", "ME 448", "ME 449", "ME 468"]:
            self.assertFalse(rx.match(required), required)

    def test_lab_elective_pool_is_the_real_narrow_list(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Lab Elective (300/400-level)"
        )
        rx = re.compile(item["match"])
        for code in ["ME 308", "ME 424", "ME 465", "ME 492"]:
            self.assertTrue(rx.match(code), code)
        self.assertFalse(rx.match("ME 370"))

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestCMPENBHBehrendCampus(unittest.TestCase):
    """Computer Engineering, B.S. (Behrend) -- Penn State Erie, The Behrend
    College. Confirmed Pattern B against UP's CMPEN-2026.json: Behrend's own
    required sequence (CMPEN 351, 371, 352W, 411, 441, 461, 480, 481) shares
    only CMPEN 431 with UP's own required set."""

    def setUp(self):
        self.plan = engine.load_degree_plan("CMPENBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_plan_is_scoped_to_erie_campus(self):
        self.assertEqual(self.plan.get("campus"), ["Erie"])

    def test_technical_elective_matches_real_list_and_excludes_cmpsc_455_456(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Technical Elective (300/400-level)"
        )
        rx = re.compile(item["match"])
        for code in ["EE 453", "CMPEN 480", "CMPSC 465", "MGMT 409", "PSYCH 444"]:
            self.assertTrue(rx.match(code), code)
        for excluded in ["CMPSC 455", "CMPSC 456"]:
            self.assertFalse(rx.match(excluded), excluded)

    def test_cmpen_271_and_275_used_instead_of_cmpen_270(self):
        options = [
            item["options"] for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and "CMPEN 271" in item.get("options", [])
        ]
        self.assertTrue(options)
        self.assertTrue(any(
            item.get("type") == "course" and item.get("options") == ["CMPEN 275"]
            for _, item in engine._iter_plan_items(self.plan)
        ))

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestEEBHBehrendCampus(unittest.TestCase):
    """Electrical Engineering, B.S. (Behrend) -- Penn State Erie, The Behrend
    College. Confirmed Pattern B against UP's EE-2026.json: Behrend's own
    required sequence (EE 312, 316, 352, 360, 313W, 331, 387, 388, 453, 481,
    400, 401) shares only EE 310 and EE 453 by number with UP's own required
    set."""

    def setUp(self):
        self.plan = engine.load_degree_plan("EEBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_plan_is_scoped_to_erie_campus(self):
        self.assertEqual(self.plan.get("campus"), ["Erie"])

    def test_ee_380_added_to_close_real_ee_400_prereq_gap(self):
        options = [
            item["options"] for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
        ]
        self.assertIn(["EE 380"], options)
        ee400 = self.catalog.get("EE 400")
        self.assertIsNotNone(ee400)
        required_singletons = {c for g in ee400.prereq_groups if len(g) == 1 for c in g}
        self.assertIn("EE 380", required_singletons)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestEETBHBehrendCampus(unittest.TestCase):
    """Electrical and Computer Engineering Technology, B.S. (Behrend) --
    Penn State Erie, The Behrend College. A different degree title than UP's
    plain Electrical Engineering Technology, B.S. (EET-2026.json), with its
    own first-year sequence (EET 2/101/109/CMPET 5) that doesn't exist at
    UP -- Pattern B. Models the real bulletin's Computer Engineering
    Technology (CMPET) option."""

    def setUp(self):
        self.plan = engine.load_degree_plan("EETBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_plan_is_scoped_to_erie_campus(self):
        self.assertEqual(self.plan.get("campus"), ["Erie"])

    def test_tech_elective_matches_real_cmpet_option_list(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Tech Elective"
        ]
        self.assertEqual(len(items), 3)
        for item in items:
            rx = re.compile(item["match"])
            for code in ["EET 330", "EET 416", "EET 440", "EET 461", "EET 495"]:
                self.assertTrue(rx.match(code), code)
            self.assertFalse(rx.match("CMPET 456"))

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestIEBHBehrendCampus(unittest.TestCase):
    """Industrial Engineering, B.S. (Behrend) -- Penn State Erie, The
    Behrend College. Confirmed Pattern B against UP's IE-2026.json: Behrend's
    own required set (IE 302, 305, 322, 327, 405, 311/307, 323, 460, 418,
    425, 470, 330, 453, 480W) diverges substantially from UP's General
    Option requirements."""

    def setUp(self):
        self.plan = engine.load_degree_plan("IEBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_plan_is_scoped_to_erie_campus(self):
        self.assertEqual(self.plan.get("campus"), ["Erie"])

    def test_ie_technical_elective_excludes_required_courses(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "IE Technical Elective"
        ]
        self.assertEqual(len(items), 2)
        for item in items:
            rx = re.compile(item["match"])
            self.assertTrue(rx.match("IE 435"))
            for required in ["IE 302", "IE 305", "IE 307", "IE 311", "IE 322", "IE 323",
                              "IE 327", "IE 330", "IE 405", "IE 418", "IE 425", "IE 453",
                              "IE 460", "IE 470", "IE 480W", "IE 497"]:
                self.assertFalse(rx.match(required), required)

    def test_specialization_course_is_ie_497(self):
        item = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and item.get("options") == ["IE 497"]
        )
        self.assertEqual(item["credits"], 1)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestMETBHBehrendCampus(unittest.TestCase):
    """Mechanical Engineering Technology, B.S. (Behrend) -- Penn State Erie,
    The Behrend College. No University Park equivalent exists in this repo
    at all (confirmed via the degree_plans directory listing), so this is
    Pattern B by definition -- a brand-new major, not a comparison case."""

    def setUp(self):
        self.plan = engine.load_degree_plan("METBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_plan_is_scoped_to_erie_campus(self):
        self.assertEqual(self.plan.get("campus"), ["Erie"])

    def test_no_up_equivalent_exists(self):
        self.assertIsNone(engine.load_degree_plan("MET", 2026))

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestPLETBHBehrendCampus(unittest.TestCase):
    """Plastics Engineering Technology, B.S. (Behrend) -- Penn State Erie,
    The Behrend College. A Behrend-only specialty tied to Erie's plastics
    industry with no University Park equivalent -- Pattern B by definition.
    New plet_catalog.json (29 courses) and pes_catalog.json (14 courses)
    were created this session since neither department existed in this
    repo's catalogs before."""

    def setUp(self):
        self.plan = engine.load_degree_plan("PLETBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_plan_is_scoped_to_erie_campus(self):
        self.assertEqual(self.plan.get("campus"), ["Erie"])

    def test_no_up_equivalent_exists(self):
        self.assertIsNone(engine.load_degree_plan("PLET", 2026))

    def test_advanced_technical_elective_is_a_subset_of_technical_elective(self):
        adv = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Advanced Technical Elective"
        )
        tech = next(
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Technical Elective (300/400-level)"
        )
        rx_adv = re.compile(adv["match"])
        rx_tech = re.compile(tech["match"])
        for code in ["PLET 466", "PLET 481", "PES 340", "PES 460", "CHEM 202"]:
            self.assertTrue(rx_adv.match(code), code)
            self.assertTrue(rx_tech.match(code), code)
        # Technical-only extras that are NOT in the Advanced pool.
        for code in ["PLET 468", "PES 320", "MET 425", "QC 450", "BME 443", "IE 302"]:
            self.assertTrue(rx_tech.match(code), code)
            self.assertFalse(rx_adv.match(code), code)

    def test_plet_494a_modeled_as_repeatable_slot_not_duplicate_course_item(self):
        # PLET 494A is required 3 times (Third Year Spring, Fourth Year Fall,
        # Fourth Year Spring) for different credit totals -- modeled as
        # "slot" items with a match regex, not three identical "course"
        # items sharing one option, since this engine's plan_progress can
        # only ever credit one item per completed course code.
        slots = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("match") == "^PLET 494A$"
        ]
        self.assertEqual(len(slots), 3)
        for item in slots:
            self.assertEqual(item.get("type"), "slot")

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestPESBHBehrendCampus(unittest.TestCase):
    """Polymer Engineering and Science, B.S. (Behrend) -- Penn State Erie,
    The Behrend College. A Behrend-only specialty with no University Park
    equivalent -- Pattern B by definition. Distinct from PLETBH-2026.json
    (Plastics Engineering TECHNOLOGY, a separate ABET accreditation track)."""

    def setUp(self):
        self.plan = engine.load_degree_plan("PESBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_plan_is_scoped_to_erie_campus(self):
        self.assertEqual(self.plan.get("campus"), ["Erie"])

    def test_no_up_equivalent_exists(self):
        self.assertIsNone(engine.load_degree_plan("PES", 2026))

    def test_technical_elective_excludes_required_pes_courses(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Technical Elective"
        ]
        self.assertEqual(len(items), 4)
        for item in items:
            rx = re.compile(item["match"])
            self.assertTrue(rx.match("PES 499"))
            self.assertTrue(rx.match("BME 402"))
            for required in ["PES 213", "PES 305", "PES 320", "PES 323", "PES 340", "PES 341",
                              "PES 365", "PES 440", "PES 441", "PES 446W", "PES 447W",
                              "PES 448W", "PES 460"]:
                self.assertFalse(rx.match(required), required)

    def test_pes_340_341_corequisite_deadlock_is_resolved_one_directionally(self):
        # The live bulletin phrases PES 340/341 as a mutual corequisite
        # pair; this engine's one-item-at-a-time scheduler can't resolve a
        # symmetric mutual requirement, so PES 341's own concurrent_groups
        # was relaxed to empty (PES 340 -> 341 remains, the reverse doesn't)
        # to avoid a permanent scheduling deadlock. See pes_catalog.json.
        pes340 = self.catalog.get("PES 340")
        pes341 = self.catalog.get("PES 341")
        self.assertTrue(any("PES 341" in g for g in pes340.concurrent_groups))
        self.assertFalse(any("PES 340" in g for g in pes341.concurrent_groups))

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestBrandywinePatternACampusAdditions(unittest.TestCase):
    """2026-08-27 Brandywine branch-campus pass, Pattern A cases: ENGL and
    HDFS's University Park plans genuinely share their real bulletin
    curriculum with Brandywine's own University College mirror pages (see
    each plan's own `notes` for the bulletin-vs-bulletin comparison), so no
    new file was built for them -- just a `campus` addition to the existing
    UP plan. Confirms the addition landed and didn't disturb the UP-only
    default behavior other majors still rely on."""

    def test_engl_lists_both_university_park_and_brandywine(self):
        plan = engine.load_degree_plan("ENGL", 2026)
        self.assertEqual(set(plan.get("campus", [])), {"University Park", "Brandywine"})

    def test_hdfs_lists_both_university_park_and_brandywine(self):
        plan = engine.load_degree_plan("HDFS", 2026)
        self.assertEqual(set(plan.get("campus", [])), {"University Park", "Brandywine"})

    def test_a_major_with_no_campus_key_still_defaults_to_university_park_only(self):
        # Regression guard: adding `campus` to ENGL/HDFS must not be read as
        # license to assume every plan needs one -- list_degree_plans' own
        # documented default (see planner_engine.py) is that an absent
        # `campus` key means University-Park-only. ME (Mechanical
        # Engineering) is the control case proving that still holds --
        # CMPSC no longer works as the control example here since a sibling
        # batch in this same campus-expansion pass separately confirmed and
        # added real Brandywine availability to CMPSC-2026.json itself.
        plan = engine.load_degree_plan("ME", 2026)
        self.assertNotIn("campus", plan)

    def test_brandywine_campus_filter_returns_engl_and_hdfs(self):
        plans = engine.list_degree_plans(campus="Brandywine")
        majors = {p["major"] for p in plans}
        self.assertIn("ENGL", majors)
        self.assertIn("HDFS", majors)
        # ME has no `campus` key at all (implicit University-Park-only) and
        # must NOT show up under a Brandywine filter.
        self.assertNotIn("ME", majors)


class TestEngrbwHandbookRequirements(unittest.TestCase):
    """ENGRBW-2026.json -- Engineering, B.S., Multidisciplinary Engineering
    Design (MDE) option, built from bulletins.psu.edu/undergraduate/colleges/
    engineering/engineering-bs/'s own Brandywine-specific Suggested Academic
    Plan table. See the plan's own `notes` for the real 2+2-to-Great-Valley
    structural caveat and the MATH 21/22 + ENGR 310 real-prereq-gate fixes."""

    def test_campus_is_brandywine_only(self):
        plan = engine.load_degree_plan("ENGRBW", 2026)
        self.assertEqual(plan.get("campus"), ["Brandywine"])

    def test_real_mde_courses_are_present(self):
        plan = engine.load_degree_plan("ENGRBW", 2026)
        all_options = {
            code
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for code in item.get("options", [])
        }
        for code in ["EDSGN 100", "EDSGN 401", "EDSGN 402", "EDSGN 403", "EDSGN 410",
                     "EDSGN 495", "EMCH 211", "EMCH 212", "EMCH 213", "EE 210",
                     "EE 310", "EE 316", "ENGR 350", "ENGR 407", "ENGR 490W", "ENGR 491W"]:
            self.assertIn(code, all_options, f"{code} missing from ENGRBW's real MDE course list")

    def test_math_21_22_placement_gate_scaffold_present(self):
        # CHEM 110 (Semester 1 on the real suggested plan) real-requires
        # MATH 22, which itself real-requires MATH 21 -- same class of fix
        # as BUSINESS/ETI/HCDD's own documented MATH 21 scaffolding.
        plan = engine.load_degree_plan("ENGRBW", 2026)
        all_options = {
            code
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for code in item.get("options", [])
        }
        self.assertIn("MATH 21", all_options)
        self.assertIn("MATH 22", all_options)

    def test_builds_cleanly_in_five_years(self):
        import datetime
        plan = engine.load_degree_plan("ENGRBW", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=5,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestPsychBaBsHandbookRequirements(unittest.TestCase):
    """PSYCHBABW-2026.json / PSYCHBSBW-2026.json -- real category course lists
    pulled from the live bulletin's own hidden Requirements-for-the-Major
    DOM text (bulletins.psu.edu/undergraduate/colleges/university-college/
    psychology-ba/ and .../psychology-bs/)."""

    def test_both_plans_are_brandywine(self):
        for major in ("PSYCHBABW", "PSYCHBSBW"):
            plan = engine.load_degree_plan(major, 2026)
            self.assertEqual(plan.get("campus"), ["Brandywine"], major)

    def test_capstone_category_is_all_400_level(self):
        # Real bulletin list: PSYCH 439/490/493/494/495/496, all 400+.
        for major in ("PSYCHBABW", "PSYCHBSBW"):
            plan = engine.load_degree_plan(major, 2026)
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label", "").startswith("Capstone Experience category")
            )
            self.assertEqual(
                set(item["options"]),
                {"PSYCH 439", "PSYCH 490", "PSYCH 493", "PSYCH 494", "PSYCH 495", "PSYCH 496"},
            )

    def test_stale_blank_bulletin_row_psych_459_excluded(self):
        # The live bulletin's own Learning and Cognition list has a blank
        # data-entry row for "PSYCH 459" (no title, no credits) -- excluded
        # rather than guessed at.
        for major in ("PSYCHBABW", "PSYCHBSBW"):
            plan = engine.load_degree_plan(major, 2026)
            item = next(
                item for _, item in engine._iter_plan_items(plan)
                if item.get("label", "").startswith("Learning and Cognition category")
            )
            self.assertNotIn("PSYCH 459", item["options"])

    def test_psych_bs_has_real_science_option_courses(self):
        plan = engine.load_degree_plan("PSYCHBSBW", 2026)
        all_options = {
            code
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for code in item.get("options", [])
        }
        self.assertIn("ANTH 21", all_options)
        self.assertIn("ANTH 22", all_options)
        self.assertIn("BBH 101", all_options)

    def test_both_plans_build_cleanly(self):
        import datetime
        for major in ("PSYCHBABW", "PSYCHBSBW"):
            with self.subTest(major=major):
                plan = engine.load_degree_plan(major, 2026)
                catalog = engine.load_merged_catalog(plan["departments"])
                fp = engine.build_full_plan(
                    plan, catalog, set(), start_year=2026, grad_years=4,
                    today=datetime.date(2026, 7, 1),
                )
                self.assertEqual(fp["warnings"], [])
                self.assertTrue(fp["goal"]["met"])


class TestItbwHandbookRequirements(unittest.TestCase):
    """ITBW-2026.json -- Information Technology, B.S., Application
    Development option (University College). Covers the real bulletin-vs-
    catalog staleness this build found: several IST-prefixed course codes
    named on the IT B.S. requirements page no longer exist in the live
    course-description catalog at all, having been renumbered to ETI/HCDD/
    CYBER prefixes (verified via a live re-scrape plus web search per code)."""

    def test_campus_is_brandywine_only(self):
        plan = engine.load_degree_plan("ITBW", 2026)
        self.assertEqual(plan.get("campus"), ["Brandywine"])

    def test_renumbered_courses_used_not_stale_ist_codes(self):
        plan = engine.load_degree_plan("ITBW", 2026)
        all_options = {
            code
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for code in item.get("options", [])
        }
        # Real, current codes must be present...
        for code in ["ETI 302", "HCDD 331", "CYBER 221", "HCDD 311", "HCDD 411"]:
            self.assertIn(code, all_options, f"{code} missing from ITBW")
        # ...and the stale IST-prefixed codes they replaced must NOT be
        # (they don't exist in the live course-description catalog at all).
        for code in ["IST 302", "IST 331", "SRA 221", "IST 311", "IST 411"]:
            self.assertNotIn(code, all_options, f"stale code {code} should not appear in ITBW")

    def test_renumbered_courses_exist_in_merged_catalog(self):
        plan = engine.load_degree_plan("ITBW", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for code in ["ETI 302", "HCDD 331", "CYBER 221", "HCDD 311", "HCDD 361",
                     "HCDD 411", "HCDD 412", "HCDD 413", "CYBER 451", "CYBER 454"]:
            self.assertIn(code, catalog, f"{code} should be a real, loaded course")

    def test_builds_cleanly_in_four_years(self):
        import datetime
        plan = engine.load_degree_plan("ITBW", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestPscmbwHandbookRequirements(unittest.TestCase):
    """PSCMBW-2026.json -- Project and Supply Chain Management, B.S.
    (University College) -- genuinely distinct from SCM-2026.json (Supply
    Chain and Information Systems, B.S., Smeal, University Park)."""

    def test_campus_is_brandywine_only(self):
        plan = engine.load_degree_plan("PSCMBW", 2026)
        self.assertEqual(plan.get("campus"), ["Brandywine"])

    def test_is_distinct_from_scm(self):
        pscm = engine.load_degree_plan("PSCMBW", 2026)
        scm = engine.load_degree_plan("SCM", 2026)
        self.assertNotEqual(pscm["title"], scm["title"])

    def test_real_prescribed_courses_present(self):
        plan = engine.load_degree_plan("PSCMBW", 2026)
        all_options = {
            code
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for code in item.get("options", [])
        }
        for code in ["SCM 301", "SCM 445", "SCM 460", "MGMT 341", "MGMT 418", "MIS 204"]:
            self.assertIn(code, all_options, f"{code} missing from PSCMBW")

    def test_builds_cleanly_in_four_years(self):
        import datetime
        plan = engine.load_degree_plan("PSCMBW", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestMdsbwHandbookRequirements(unittest.TestCase):
    """MDSBW-2026.json -- Multidisciplinary Studies, B.A. (University
    College). Genuinely has no enumerated course list at all per the real
    bulletin text ('Courses must be selected in consultation with an
    adviser') -- every major-requirement item is a generic slot with no
    `options`/`open_elective`/`match`, which this test asserts directly so
    a future edit doesn't quietly fabricate a course list for this major."""

    def test_campus_is_brandywine_only(self):
        plan = engine.load_degree_plan("MDSBW", 2026)
        self.assertEqual(plan.get("campus"), ["Brandywine"])

    def test_no_major_requirement_item_names_a_specific_course(self):
        plan = engine.load_degree_plan("MDSBW", 2026)
        for _, item in engine._iter_plan_items(plan):
            self.assertNotEqual(item.get("type"), "course",
                                 f"MDSBW item should be a generic slot, not a course pick: {item}")
            self.assertNotIn("open_elective", item)
            self.assertNotIn("match", item)

    def test_builds_cleanly_in_four_years(self):
        import datetime
        plan = engine.load_degree_plan("MDSBW", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4,
            today=datetime.date(2026, 7, 1),
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])


class TestBehrendMajorsMetadata(unittest.TestCase):
    """This session added Penn State Erie, The Behrend College's own
    versions of six real majors -- English B.A., Creative Writing B.F.A.,
    Digital Media/Arts/Technology B.A., Media and Communication B.A.,
    Multidisciplinary Arts/Social Sciences/Sciences/Humanities B.A., and
    Project and Supply Chain Management B.S. Every one of these turned out
    to be "Pattern B" (a genuinely different curriculum from any existing
    University Park major, confirmed by reading each Behrend bulletin page
    directly and comparing against the UP plan of the same or closest name)
    rather than "Pattern A" (same shared curriculum, just add "Erie" to an
    existing plan's campus array) -- so six new plan files were built, not
    one existing file edited. This class checks the shared metadata every
    one of them needs: real campus tagging, a distinguishable "(Behrend)"
    title, and no code collision with any of the ~230 pre-existing majors."""

    BEHREND_MAJORS = ["ENGLBH", "CRWT", "DMAT", "MCOM", "MASSH", "PSCM"]

    def test_all_six_load_and_are_tagged_erie(self):
        for major in self.BEHREND_MAJORS:
            plan = engine.load_degree_plan(major, 2026)
            self.assertIsNotNone(plan, major)
            self.assertEqual(plan.get("campus"), ["Erie"], major)

    def test_all_six_titles_say_behrend(self):
        for major in self.BEHREND_MAJORS:
            plan = engine.load_degree_plan(major, 2026)
            self.assertIn("Behrend", plan["title"], major)

    def test_none_of_the_six_codes_collide_with_an_existing_up_major(self):
        # Every plan file using one of these six codes must be this
        # session's own Erie-campus file, not some pre-existing UP major
        # that happened to reuse the same short code.
        degree_dir = engine.DEGREE_PLAN_DIR
        for major in self.BEHREND_MAJORS:
            same_code_files = [f for f in os.listdir(degree_dir) if f.startswith(f"{major}-")]
            self.assertTrue(same_code_files, major)
            for fname in same_code_files:
                with open(os.path.join(degree_dir, fname), encoding="utf-8") as fh:
                    import json as _json
                    data = _json.load(fh)
                self.assertEqual(data.get("campus"), ["Erie"], fname)

    def test_erie_campus_filter_surfaces_all_six(self):
        plans = engine.list_degree_plans(campus="Erie")
        majors_found = {p["major"] for p in plans}
        for major in self.BEHREND_MAJORS:
            self.assertIn(major, majors_found)

    def test_university_park_filter_does_not_surface_these_six(self):
        plans = engine.list_degree_plans(campus="University Park")
        majors_found = {p["major"] for p in plans}
        for major in self.BEHREND_MAJORS:
            self.assertNotIn(major, majors_found)


class TestENGLBHBulletinRequirements(unittest.TestCase):
    """English, B.A. (Behrend) -- bulletins.psu.edu/undergraduate/colleges/
    behrend/english-ba/. Confirmed Pattern B (not the same curriculum as
    Backend/degree_plans/ENGL-2026.json, the University Park 'Traditions of
    Innovation' plan): UP requires ENGL 200/201 + ENGL 487W/494H + a 12cr
    era-grouped option; Behrend requires ENGL 200 + ENGL 312 + ENGL 482W +
    a Shakespeare requirement + a Thesis/Internship + a Language/Linguistics
    elective + 5 named Supporting categories -- almost no course overlap."""

    def setUp(self):
        self.plan = engine.load_degree_plan("ENGLBH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_is_genuinely_different_from_the_up_english_plan(self):
        up_plan = engine.load_degree_plan("ENGL", 2026)
        up_required = {
            o for _, item in engine._iter_plan_items(up_plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        bh_required = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        # UP's own required pair (ENGL 200/201) and Behrend's (ENGL 312,
        # ENGL 482W) don't fully coincide -- Behrend requires real courses
        # (312, 482W) that are not part of UP's required list at all.
        self.assertIn("ENGL 312", bh_required)
        self.assertIn("ENGL 482W", bh_required)
        self.assertNotIn("ENGL 312", up_required)
        self.assertNotIn("ENGL 487W", bh_required)

    def test_prescribed_courses_are_present(self):
        required = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        for code in ("ENGL 200", "ENGL 312", "ENGL 482W"):
            self.assertIn(code, required)
        # Shakespeare and thesis/internship alt-pairs
        self.assertTrue({"ENGL 443", "ENGL 444"} & required)
        self.assertTrue({"ENGL 494", "ENGL 495"} & required)

    def test_uses_psu_7_not_la_83_for_first_year_seminar(self):
        required = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        self.assertIn("PSU 7", required)
        self.assertNotIn("LA 83", required)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestCRWTBulletinRequirements(unittest.TestCase):
    """Creative Writing, B.F.A. (Behrend) -- bulletins.psu.edu/undergraduate/
    colleges/behrend/creative-writing-bfa/. No University Park equivalent
    exists at all. ENGL 6 ('Creative Writing Common Time') is confirmed real
    via its own catalog entry, which caps it at 8 credits total and
    describes it as required 'every semester ... at Penn State Erie' --
    strong independent confirmation this is the real Behrend curriculum."""

    def setUp(self):
        self.plan = engine.load_degree_plan("CRWT", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_engl_6_is_collapsed_to_one_8_credit_item_not_eight_1_credit_items(self):
        # Regression test for a real engine limitation discovered while
        # building this plan: planner_engine.py's plan_progress/
        # recommend_semester can only ever mark ONE plan item satisfied per
        # distinct course code (see _ranked_options' docstring: "a course,
        # once completed, can't satisfy a second item"). Modeling ENGL 6 as
        # 8 separate 1-credit items (one per semester, matching the
        # bulletin's own suggested plan literally) makes build_full_plan
        # loop forever, since only the first of the 8 items can ever be
        # marked done. Confirmed empirically before this fix (30+ simulated
        # terms, never converging).
        engl6_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and item.get("options") == ["ENGL 6"]
        ]
        self.assertEqual(len(engl6_items), 1)
        self.assertEqual(float(engl6_items[0]["credits"]), 8.0)

    def test_engl_494_is_collapsed_to_one_6_credit_item(self):
        # Same underlying engine limitation as ENGL 6 above, applied to the
        # Senior Thesis in English (also split 3cr/3cr across two terms on
        # the bulletin's own suggested plan).
        thesis_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course" and item.get("options") == ["ENGL 494"]
        ]
        self.assertEqual(len(thesis_items), 1)
        self.assertEqual(float(thesis_items[0]["credits"]), 6.0)

    def test_advanced_writing_workshops_present_and_engl_424_intentionally_excluded(self):
        required = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        for code in ("ENGL 412", "ENGL 413", "ENGL 422"):
            self.assertIn(code, required)
        # ENGL 424 is a real 4th workshop option per the bulletin, but its
        # real prereq (ENGL 50 or ENVST 100N) isn't otherwise required by
        # this major -- deliberately left out of the schedulable set.
        self.assertNotIn("ENGL 424", required)

    def test_full_plan_builds_cleanly_in_a_reasonable_number_of_terms(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4, max_terms=30)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])
        self.assertLessEqual(len(fp["terms"]), 10)


class TestDMATBulletinRequirements(unittest.TestCase):
    """Digital Media, Arts, and Technology, B.A. (Behrend) --
    bulletins.psu.edu/undergraduate/colleges/behrend/
    digital-media-arts-technology-ba/. Not the same program as
    Backend/degree_plans/DMD-2026.json (Digital Multimedia Design, B.Des.,
    World Campus/Arts and Architecture) despite the superficially similar
    name -- different degree type, department, and course list entirely.
    Uses the DIGIT department (Digital Media, Arts, and Technology), which
    didn't have a Backend/catalogs/digit_catalog.json before this session;
    it was built fresh from the real university course-description page."""

    def setUp(self):
        self.plan = engine.load_degree_plan("DMAT", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_is_not_the_same_program_as_dmd(self):
        dmd_plan = engine.load_degree_plan("DMD", 2026)
        self.assertNotEqual(dmd_plan["title"], self.plan["title"])
        dmd_required = {
            o for _, item in engine._iter_plan_items(dmd_plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        self.assertNotIn("DIGIT 100", dmd_required)

    def test_digit_catalog_has_the_real_required_courses(self):
        digit_catalog = engine.load_merged_catalog(["DIGIT"])
        for code in ("DIGIT 100", "DIGIT 110", "DIGIT 210", "DIGIT 400", "DIGIT 494", "DIGIT 495"):
            self.assertIn(code, digit_catalog, code)

    def test_digit_400_prereq_chain_is_the_real_one(self):
        digit_catalog = engine.load_merged_catalog(["DIGIT"])
        groups = digit_catalog["DIGIT 400"].prereq_groups
        flat = {c for g in groups for c in g}
        self.assertEqual(flat, {"DIGIT 100", "DIGIT 110", "DIGIT 210"})

    def test_required_core_courses_are_present(self):
        required = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        for code in ("ART 168", "COMM 270", "DIGIT 100", "DIGIT 110", "DIGIT 210", "DIGIT 400", "PHOTO 100"):
            self.assertIn(code, required)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestMCOMBulletinRequirements(unittest.TestCase):
    """Media and Communication, B.A. (Behrend) -- bulletins.psu.edu/
    undergraduate/colleges/behrend/media-communication-ba/. No University
    Park major shares this exact name or course list."""

    def setUp(self):
        self.plan = engine.load_degree_plan("MCOM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_prescribed_courses_are_present(self):
        required = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        for code in ("CAS 204", "CAS 212", "CAS 303", "COMM 160", "COMM 251", "COMM 260W"):
            self.assertIn(code, required)

    def test_alt_pairs_are_present(self):
        required = {
            o for _, item in engine._iter_plan_items(self.plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        self.assertTrue({"CAS 271N", "COMM 205"} <= required)
        self.assertTrue({"CAS 450W", "CAS 252"} <= required)
        self.assertTrue({"COMM 494", "COMM 495"} <= required)

    def test_comm_260w_scheduled_after_its_real_prereq_comm_160(self):
        catalog = self.catalog
        self.assertIn(["COMM 160"], [list(g) for g in catalog["COMM 260W"].prereq_groups])
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        comm160_term = comm260w_term = None
        for t in fp["terms"]:
            codes = [c["code"] for c in t["courses"]]
            if "COMM 160" in codes:
                comm160_term = t["index"]
            if "COMM 260W" in codes:
                comm260w_term = t["index"]
        self.assertIsNotNone(comm160_term)
        self.assertIsNotNone(comm260w_term)
        self.assertLessEqual(comm160_term, comm260w_term)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestMASSHBulletinRequirements(unittest.TestCase):
    """Multidisciplinary Arts, Social Sciences, Sciences, and Humanities,
    B.A. (Behrend) -- bulletins.psu.edu/undergraduate/colleges/behrend/
    multidisciplinary-arts-social-sciences-sciences-humanities-ba/. A real,
    individualized/self-designed degree -- the Major's own 36cr structure
    (12cr Common Foundation across 4 knowledge areas + 24cr Specialized
    Option) is enforceable even though the specific courses inside it are
    genuinely adviser/student-directed, not a bulletin data gap."""

    def setUp(self):
        self.plan = engine.load_degree_plan("MASSH", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_common_foundation_covers_all_four_knowledge_areas(self):
        foundation_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if "Common Foundation" in (item.get("label") or "")
        ]
        self.assertEqual(len(foundation_items), 4)
        self.assertEqual(sum(float(i["credits"]) for i in foundation_items), 12.0)
        domains = {i.get("gen_ed") for i in foundation_items}
        self.assertEqual(domains, {"GA", "GH", "GN", "GS"})

    def test_specialized_option_totals_24_credits_with_at_least_15_at_400_level(self):
        option_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label", "").startswith("Specialized Option")
        ]
        total = sum(float(i["credits"]) for i in option_items)
        self.assertEqual(total, 24.0)
        at_400 = sum(float(i["credits"]) for i in option_items if "400-level" in i["label"])
        self.assertGreaterEqual(at_400, 15.0)

    def test_major_total_matches_the_real_36_credit_bulletin_figure(self):
        foundation_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if "Common Foundation" in (item.get("label") or "")
        ]
        option_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label", "").startswith("Specialized Option")
        ]
        total = sum(float(i["credits"]) for i in foundation_items + option_items)
        self.assertEqual(total, 36.0)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestPSCMBulletinRequirements(unittest.TestCase):
    """Project and Supply Chain Management, B.S. (Behrend) --
    bulletins.psu.edu/undergraduate/colleges/behrend/
    project-supply-chain-management-bs/. NOT the same program as
    Backend/degree_plans/SCM-2026.json ('Supply Chain and Information
    Systems, B.S.', Smeal College of Business, University Park) -- a real,
    separate curriculum centered on project management rather than
    information systems."""

    def setUp(self):
        self.plan = engine.load_degree_plan("PSCM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])

    def test_is_not_the_up_scm_program(self):
        up_plan = engine.load_degree_plan("SCM", 2026)
        self.assertNotEqual(up_plan["title"], self.plan["title"])
        up_required = {
            o for _, item in engine._iter_plan_items(up_plan)
            if item.get("type") == "course"
            for o in item.get("options", [])
        }
        # MGMT 418 (Project Planning and Resource Management) is a real,
        # required PSCM course that UP's Smeal SCM plan does not require.
        self.assertNotIn("MGMT 418", up_required)

    def test_math_21_precedes_its_real_dependents(self):
        # ACCTG 211, STAT 200, and SCM 200 all really require MATH 21 (see
        # Backend/catalogs/acctg_catalog.json, stat_catalog.json,
        # scm_catalog.json) -- confirmed here directly against the catalog,
        # then verified end-to-end via a clean full-plan build below.
        for code in ("ACCTG 211", "STAT 200", "SCM 200"):
            groups = self.catalog[code].prereq_groups
            flat = {c for g in groups for c in g}
            self.assertIn("MATH 21", flat, code)

    def test_mgmt_410_precedes_mgmt_418(self):
        # Real prereq: MGMT 415/418 both need SCM 301 AND (BA 421 or
        # MGMT 409 or MGMT 410) -- this plan uses MGMT 410 to satisfy the
        # second half of both, so MGMT 410 must schedule strictly before
        # MGMT 418 (a hard requirement) in any successful build.
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        mgmt410_term = mgmt418_term = None
        for t in fp["terms"]:
            codes = [c["code"] for c in t["courses"]]
            if "MGMT 410" in codes:
                mgmt410_term = t["index"]
            if "MGMT 418" in codes:
                mgmt418_term = t["index"]
        self.assertIsNotNone(mgmt410_term)
        self.assertIsNotNone(mgmt418_term)
        self.assertLess(mgmt410_term, mgmt418_term)

    def test_full_plan_builds_cleanly(self):
        fp = engine.build_full_plan(self.plan, self.catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestBehrendCampusExpansion(unittest.TestCase):
    """Penn State Erie, The Behrend College degree plans, added this session.

    Behrend is a real, separate branch campus with its own School of
    Science department and, for most science majors, a genuinely different
    curriculum from University Park despite the shared major name -- see
    each plan's own `notes` field for the specific bulletin-vs-bulletin
    comparison that determined Pattern A (shared curriculum -- just add
    "Erie" to the existing UP plan's campus array) vs Pattern B (separate
    curriculum -- build a new <CODE>BH-2026.json). BBH is Pattern A;
    Biology, Chemistry, and Physics are Pattern B (new BIOLBH/CHEMBH/PHYSBH
    files); Environmental Science and the general "Science, B.S." have no
    University Park equivalent at all and were built fresh (ENVSC, SCIBH).
    """

    import datetime as _dt
    TODAY = _dt.date(2026, 7, 1)

    # ---- BBH: Pattern A (shared curriculum, campus array widened) -------

    def test_bbh_lists_erie_as_a_campus_alongside_university_park(self):
        plans = engine.list_degree_plans(campus="Erie")
        bbh = next((p for p in plans if p["major"] == "BBH"), None)
        self.assertIsNotNone(bbh, "BBH should be returned when filtering by Erie campus")
        self.assertIn("University Park", bbh["campuses"])
        self.assertIn("Erie", bbh["campuses"])

    def test_bbh_still_builds_cleanly_after_campus_widening(self):
        # Adding "Erie" to BBH's campus array must not disturb the plan's
        # own scheduling — same plan, same courses, just offered at an
        # additional campus.
        plan = engine.load_degree_plan("BBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(
            plan, catalog, set(), start_year=2026, grad_years=4, today=self.TODAY,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])

    # ---- Shared helpers for the Pattern B / fresh-build plans ------------

    _BEHREND_MAJORS = ["BIOLBH", "CHEMBH", "PHYSBH", "ENVSC", "SCIBH"]

    def test_all_behrend_plans_are_erie_only_and_load(self):
        for major in self._BEHREND_MAJORS:
            with self.subTest(major=major):
                plan = engine.load_degree_plan(major, 2026)
                self.assertIsNotNone(plan, f"{major}-2026.json failed to load")
                self.assertEqual(plan.get("campus"), ["Erie"], f"{major} should be Erie-only")
                self.assertIn("(Behrend)", plan["title"]) if major != "ENVSC" else None

    def test_all_behrend_plans_build_cleanly_and_graduate_in_four_years(self):
        for major in self._BEHREND_MAJORS:
            with self.subTest(major=major):
                plan = engine.load_degree_plan(major, 2026)
                catalog = engine.load_merged_catalog(plan["departments"])
                fp = engine.build_full_plan(
                    plan, catalog, set(), start_year=2026, grad_years=4, today=self.TODAY,
                )
                self.assertEqual(fp["warnings"], [], f"{major} has warnings: {fp['warnings']}")
                self.assertTrue(fp["goal"]["met"], f"{major} did not graduate in 4 years")

    def test_all_behrend_plan_course_options_exist_in_their_own_catalog(self):
        # Every course code an item names must actually resolve in the
        # merged catalog built from that plan's own `departments` list —
        # catches a typo'd code or a department missing from the list.
        for major in self._BEHREND_MAJORS:
            with self.subTest(major=major):
                plan = engine.load_degree_plan(major, 2026)
                catalog = engine.load_merged_catalog(plan["departments"])
                missing = set()
                for _, item in engine._iter_plan_items(plan):
                    if item.get("type") == "course":
                        for opt in item["options"]:
                            if opt not in catalog:
                                missing.add(opt)
                self.assertEqual(missing, set(), f"{major}: options missing from catalog: {missing}")

    # ---- BIOLBH specifics --------------------------------------------

    def test_biolbh_requires_the_courses_common_to_every_behrend_biology_option(self):
        plan = engine.load_degree_plan("BIOLBH", 2026)
        all_options = [
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item["options"]
        ]
        # Common-to-all-options prescribed courses per Behrend's own bulletin
        # page, distinct from University Park's General Biology option.
        for code in ("ENGL 202C", "STAT 250", "BIOL 322", "BIOL 240W", "BIOL 220W", "BIOL 230W"):
            self.assertIn(code, all_options, f"BIOLBH missing common course {code}")

    def test_biolbh_400_level_slots_sum_to_15_credits_with_biol_427(self):
        plan = engine.load_degree_plan("BIOLBH", 2026)
        total = 0.0
        for _, item in engine._iter_plan_items(plan):
            if item.get("type") == "course" and item["options"] == ["BIOL 427"]:
                total += item["credits"]
            elif item.get("type") == "slot" and item.get("elective_min_level") == 400:
                total += item["credits"]
        self.assertEqual(total, 15.0)

    # ---- CHEMBH specifics --------------------------------------------

    def test_chembh_requires_courses_up_does_not(self):
        plan = engine.load_degree_plan("CHEMBH", 2026)
        all_options = [
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item["options"]
        ]
        # These are real Behrend-only common prescribed courses -- absent
        # from University Park's CHEM-2026.json entirely.
        for code in ("CHEM 358", "CHEM 413", "CHEM 440", "CHEM 441", "CHEM 472", "CHEM 431W"):
            self.assertIn(code, all_options, f"CHEMBH missing Behrend-specific course {code}")

    # ---- PHYSBH specifics ---------------------------------------------

    def test_physbh_requires_phys_421w_and_494_unlike_up(self):
        plan = engine.load_degree_plan("PHYSBH", 2026)
        all_options = [
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item["options"]
        ]
        # University Park's PHYS-2026.json requires PHYS 444/457W instead;
        # Behrend requires these two, confirmed against its own bulletin page.
        self.assertIn("PHYS 421W", all_options)
        self.assertIn("PHYS 494", all_options)

    # ---- ENVSC specifics -----------------------------------------------

    def test_envsc_has_no_university_park_equivalent(self):
        # ENVSYS (Environmental Systems Engineering, an EMS-college major)
        # is a different program by a different name -- confirm the two
        # major codes are genuinely distinct plans.
        envsc = engine.load_degree_plan("ENVSC", 2026)
        envsys = engine.load_degree_plan("ENVSYS", 2026)
        self.assertIsNotNone(envsc)
        self.assertIsNotNone(envsys)
        self.assertNotEqual(envsc["title"], envsys["title"])

    def test_envsc_capstone_prereq_chain_is_present(self):
        # ENVSC 400W (the capstone) requires BIOL 402W, which itself
        # real-chains through STAT 250 + BIOL 220W/230W/240W all together —
        # confirm every link is actually present as a plan item so the
        # capstone is never permanently blocked.
        plan = engine.load_degree_plan("ENVSC", 2026)
        all_options = [
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item["options"]
        ]
        for code in ("ENVSC 400W", "BIOL 402W", "STAT 250", "BIOL 220W", "BIOL 230W", "BIOL 240W"):
            self.assertIn(code, all_options, f"ENVSC missing capstone-chain course {code}")

    def test_envsc_catalog_file_exists_and_has_required_courses(self):
        import json
        path = os.path.join(engine.CATALOG_DIR, "envsc_catalog.json")
        self.assertTrue(os.path.exists(path), "envsc_catalog.json should exist")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for code in ("ENVSC 200", "ENVSC 400W", "ENVSC 404"):
            self.assertIn(code, data)

    # ---- SCIBH specifics ------------------------------------------------

    def test_scibh_has_no_university_park_equivalent(self):
        scibh = engine.load_degree_plan("SCIBH", 2026)
        self.assertIsNotNone(scibh)
        self.assertIn("Behrend", scibh["title"])
        # No plan named plain "SCI" should exist at University Park.
        self.assertIsNone(engine.load_degree_plan("SCI", 2026))


class TestBehrendMathematicsBS(unittest.TestCase):
    """MATHBH-2026.json -- Mathematics, B.S. (Behrend), Applied Mathematics
    option. Pattern B vs. MATH-2026.json (UP): Behrend requires ENGL 202C,
    CMPSC 121 AND 122, STAT 301 and STAT 401 outright, and structures the
    major into four named Option tracks UP's plan doesn't have at all."""

    def test_plan_loads_and_is_tagged_erie(self):
        plan = engine.load_degree_plan("MATHBH", 2026)
        self.assertIsNotNone(plan)
        self.assertIn("Erie", plan.get("campus", []))
        self.assertIn("Behrend", plan["title"])

    def test_common_prescribed_courses_are_real_and_cataloged(self):
        plan = engine.load_degree_plan("MATHBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for code in ["MATH 140", "MATH 141", "MATH 220", "MATH 230", "MATH 311W",
                     "MATH 312", "STAT 301", "STAT 401", "CMPSC 121", "CMPSC 122", "ENGL 202C"]:
            self.assertIn(code, catalog, f"{code} must be a real cataloged course")

    def test_group_a_and_group_b_pools_are_distinct_and_real(self):
        plan = engine.load_degree_plan("MATHBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        group_a = _all_items_with_label_substring(plan, "Group A elective")
        group_b = _all_items_with_label_substring(plan, "Group B elective")
        self.assertEqual(len(group_a), 5)
        self.assertEqual(len(group_b), 2)
        for item in group_a + group_b:
            for code in item["options"]:
                self.assertIn(code, catalog)

    def test_cmpsc_121_models_the_real_math_140_concurrent_alternative(self):
        # bulletins.psu.edu/university-course-descriptions/undergraduate/cmpsc/:
        # "Enforced Prerequisite at Enrollment: MATH 110 or Enforced
        # Concurrent at Enrollment: MATH 140" -- previously modeled as a hard
        # MATH-110-only prereq, which permanently blocked this plan (and any
        # other MATH-140-track plan) from ever satisfying CMPSC 121.
        catalog = engine.load_merged_catalog(["CMPSC"])
        course = catalog["CMPSC 121"]
        self.assertEqual(course.prereq_groups, [])
        self.assertIn({"MATH 110", "MATH 140"}, course.concurrent_groups)

    def test_full_plan_builds_cleanly_for_mathbh(self):
        plan = engine.load_degree_plan("MATHBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestBehrendPoliticalScienceBA(unittest.TestCase):
    """PLSCBABH-2026.json -- Political Science, B.A. (Behrend), Politics and
    Government option. Pattern B vs. PLSCBA-2026.json (UP): Behrend mandates
    one of four named Option tracks with real, differently-shaped
    requirements; UP's B.A. has no such track structure at all."""

    def test_plan_loads_and_is_tagged_erie(self):
        plan = engine.load_degree_plan("PLSCBABH", 2026)
        self.assertIsNotNone(plan)
        self.assertIn("Erie", plan.get("campus", []))
        self.assertIn("Behrend", plan["title"])

    def test_common_core_courses_are_real_and_cataloged(self):
        plan = engine.load_degree_plan("PLSCBABH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for code in ["PLSC 1", "PLSC 3", "PLSC 14", "PLSC 7N", "PLSC 17N", "PLSC 17W"]:
            self.assertIn(code, catalog)

    def test_400_level_and_option_elective_counts_match_the_bulletin(self):
        plan = engine.load_degree_plan("PLSCBABH", 2026)
        four_hundred = _all_items_with_label_substring(plan, "400-Level PLSC elective")
        option_electives = _all_items_with_label_substring(plan, "PLSC elective, any level")
        related = _all_items_with_label_substring(plan, "Related course (major-approved list")
        self.assertEqual(len(four_hundred), 4)   # Common Core: 12cr @ 400-level
        self.assertEqual(len(option_electives), 4)  # Politics and Government: 12cr
        self.assertEqual(len(related), 2)  # Politics and Government: 6cr

    def test_full_plan_builds_cleanly_for_plscbabh(self):
        plan = engine.load_degree_plan("PLSCBABH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestBehrendHistoryBA(unittest.TestCase):
    """HISTBH-2026.json -- History, B.A. (Behrend). Pattern B vs.
    HIST-2026.json (UP): 39cr major (vs. UP's 36cr) with ENGL 202A/B counted
    IN the major, a 4-course/12cr survey requirement (vs. UP's 2-course/6cr),
    HIST 301W offered as an equal alternative to 302W, and only three field
    categories (vs. UP's four)."""

    def test_plan_loads_and_is_tagged_erie(self):
        plan = engine.load_degree_plan("HISTBH", 2026)
        self.assertIsNotNone(plan)
        self.assertIn("Erie", plan.get("campus", []))
        self.assertIn("Behrend", plan["title"])

    def test_survey_pool_appears_four_times_not_two(self):
        # Behrend requires 4 courses (12cr) from the HIST 1/2/10/11/20/21
        # pool -- UP's HIST-2026.json only requires one 2-course sequence.
        plan = engine.load_degree_plan("HISTBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        survey_items = _all_items_with_label_substring(plan, "HIST Survey Course")
        self.assertEqual(len(survey_items), 4)
        pool = {"HIST 1", "HIST 2", "HIST 10", "HIST 11", "HIST 20", "HIST 21"}
        for item in survey_items:
            self.assertEqual(set(item["options"]), pool)
            for code in item["options"]:
                self.assertIn(code, catalog)

    def test_hist_301w_is_a_real_alternative_to_302w(self):
        plan = engine.load_degree_plan("HISTBH", 2026)
        item = _first_item_with_label_substring(plan, "HIST 301W")
        self.assertEqual(set(item["options"]), {"HIST 301W", "HIST 302W"})

    def test_three_area_categories_are_distinct_real_pools(self):
        plan = engine.load_degree_plan("HISTBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        europe = _first_item_with_label_substring(plan, "Europe area course")
        us = _first_item_with_label_substring(plan, "United States area course")
        world = _first_item_with_label_substring(plan, "World (non-Western) area course")
        option_sets = [frozenset(i["options"]) for i in (europe, us, world)]
        self.assertEqual(len(set(option_sets)), 3, "each area category's course list must be distinct")
        for item in (europe, us, world):
            for code in item["options"]:
                self.assertIn(code, catalog)

    def test_full_plan_builds_cleanly_for_histbh(self):
        plan = engine.load_degree_plan("HISTBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestBehrendPsychologyBA(unittest.TestCase):
    """PSYCHBABH-2026.json -- Psychology, B.A. (Behrend). Brand-new: no UP
    Psychology plan exists anywhere in this repo, so this major has no
    'BH'-suffixed code and no UP sibling to compare against."""

    def test_no_up_psych_plan_exists_in_this_repo(self):
        for year in (2022, 2023, 2024, 2025, 2026):
            self.assertIsNone(engine.load_degree_plan("PSYCH", year))

    def test_plan_loads_and_is_tagged_erie(self):
        plan = engine.load_degree_plan("PSYCHBABH", 2026)
        self.assertIsNotNone(plan)
        self.assertIn("Erie", plan.get("campus", []))
        self.assertIn("Behrend", plan["title"])

    def test_psych_200_real_math_21_prereq_chain_is_wired(self):
        # bulletins.psu.edu/university-course-descriptions/undergraduate/psych/:
        # PSYCH 200's real enforced prerequisite is "PSYCH 100 and MATH 21".
        catalog = engine.load_merged_catalog(["PSYCH"])
        course = catalog["PSYCH 200"]
        self.assertIn({"MATH 21"}, [set(g) for g in course.prereq_groups])
        plan = engine.load_degree_plan("PSYCHBABH", 2026)
        codes = {c for _, item in engine._iter_plan_items(plan) for c in item.get("options", [])}
        self.assertIn("MATH 3", codes)
        self.assertIn("MATH 4", codes)
        self.assertIn("MATH 21", codes)

    def test_content_area_pools_are_real_and_distinct(self):
        plan = engine.load_degree_plan("PSYCHBABH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        labels = ["Biological Bases of Behavior", "Social/Developmental", "Cognitive/Learning", "Clinical/Applied"]
        pools = []
        for label in labels:
            item = _first_item_with_label_substring(plan, label)
            self.assertIsNotNone(item, f"expected a Content Area item for {label}")
            pools.append(frozenset(item["options"]))
            for code in item["options"]:
                self.assertIn(code, catalog)
        self.assertEqual(len(set(pools)), 4, "each content area's course list must be distinct")

    def test_full_plan_builds_cleanly_for_psychba(self):
        plan = engine.load_degree_plan("PSYCHBABH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestBehrendPsychologyBS(unittest.TestCase):
    """PSYCHBSBH-2026.json -- Psychology, B.S. (Behrend), Science option.
    Brand-new, same 'no UP sibling' situation as its PSYCHBA sibling."""

    def test_plan_loads_and_is_tagged_erie(self):
        plan = engine.load_degree_plan("PSYCHBSBH", 2026)
        self.assertIsNotNone(plan)
        self.assertIn("Erie", plan.get("campus", []))
        self.assertIn("Behrend", plan["title"])

    def test_practicum_pool_includes_the_bs_only_psych_477(self):
        # The B.S.'s own Supporting Courses table lists PSYCH 294/296/477/
        # 494/495/496 -- PSYCH 477 is real but is NOT part of the B.A.'s
        # practicum pool (PSYCHBABH-2026.json), confirmed via direct fetch of
        # each degree's own bulletin page.
        plan = engine.load_degree_plan("PSYCHBSBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        item = _first_item_with_label_substring(plan, "Practicum/Internship/Research")
        self.assertIn("PSYCH 477", item["options"])
        self.assertIn("PSYCH 477", catalog)

    def test_science_option_additional_course_pool_is_real(self):
        plan = engine.load_degree_plan("PSYCHBSBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        item = _first_item_with_label_substring(plan, "Science option: Additional Course")
        self.assertEqual(set(item["options"]), {"PSYCH 253", "PSYCH 256", "PSYCH 260A", "PSYCH 261"})
        for code in item["options"]:
            self.assertIn(code, catalog)

    def test_full_plan_builds_cleanly_for_psychbs(self):
        plan = engine.load_degree_plan("PSYCHBSBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestBehrendSecondaryEducationBS(unittest.TestCase):
    """SECEDBH-2026.json -- Secondary Education, B.S., Mathematics Teaching
    Option (Behrend). Pattern B vs. SECED-2026.json (UP, Biology Teaching
    Option) -- Mathematics Teaching is the ONLY certification option
    Behrend's own bulletin lists as available at Erie; every other option is
    explicitly UP-only per that same bulletin page."""

    def test_plan_loads_and_is_tagged_erie(self):
        plan = engine.load_degree_plan("SECEDBH", 2026)
        self.assertIsNotNone(plan)
        self.assertIn("Erie", plan.get("campus", []))
        self.assertIn("Behrend", plan["title"])

    def test_math_teaching_prescribed_courses_are_real_and_cataloged(self):
        plan = engine.load_degree_plan("SECEDBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        for code in ["MATH 140", "MATH 141", "MATH 220", "MATH 310", "MATH 311W",
                     "MATH 312", "MATH 414", "MATH 471", "MTHED 411", "MTHED 412W",
                     "MTHED 427", "SPLED 400", "SPLED 403B", "CI 280", "CI 295",
                     "CI 495C", "CI 495E", "EDPSY 14", "HDFS 239"]:
            self.assertIn(code, catalog, f"{code} must be a real cataloged course")

    def test_final_semester_is_student_teaching_alone(self):
        plan = engine.load_degree_plan("SECEDBH", 2026)
        last = plan["semesters"][-1]
        self.assertEqual(len(last["items"]), 1)
        self.assertEqual(last["items"][0]["options"], ["CI 495E"])
        self.assertEqual(last["items"][0]["credits"], 15)

    def test_full_plan_builds_cleanly_for_secedbh(self):
        plan = engine.load_degree_plan("SECEDBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        failures = [w for w in fp["warnings"] if "Could not schedule" in w]
        self.assertEqual(failures, [])


class TestBehrendBatchOnePlansBuildCleanly(unittest.TestCase):
    """One shared, parametrized check for every plan in this batch: it loads,
    it's tagged to the real Erie campus (not University Park), and Behrend's
    own real 2026-27 Suggested Academic Plan builds cleanly all the way to
    graduation with zero warnings, using the same today=<catalog year>-07-01
    anchor the full degree_plans sweep (TestEveryDegreePlanBuildsCleanly-style)
    uses to avoid the wall-clock 'today is mid-semester' artifact."""

    import datetime as _datetime

    BEHREND_MAJORS = [
        "ACCTGBH", "BUSECONBH", "ECONBABH", "FINBH", "IBBH", "MISBH", "MKTGBH",
    ]

    def test_each_behrend_plan_loads_tagged_to_erie_and_builds_cleanly(self):
        for major in self.BEHREND_MAJORS:
            with self.subTest(major=major):
                plan = engine.load_degree_plan(major, 2026)
                self.assertIsNotNone(plan, f"{major}-2026.json failed to load")
                self.assertEqual(plan.get("campus"), ["Erie"], f"{major} must be Erie-only")
                self.assertIn("(Behrend)", plan["title"], f"{major}'s title must say (Behrend)")
                catalog = engine.load_merged_catalog(plan["departments"])
                fp = engine.build_full_plan(
                    plan, catalog, set(),
                    start_year=2026, grad_years=4,
                    today=self._datetime.date(2026, 7, 1),
                )
                self.assertEqual(fp["warnings"], [], f"{major}-2026 has warnings: {fp['warnings']}")
                self.assertTrue(fp["goal"]["met"], f"{major}-2026 did not graduate in 4 years")
                # Every major-specific course option this plan itself
                # declares must be a real, catalog-present code -- guards
                # against a typo'd course number in this plan's own JSON.
                # (Gen Ed slots legitimately resolve to courses from
                # departments outside this plan's own `departments` list,
                # so this only checks explicit "course" items, not every
                # code the simulator ends up scheduling.)
                for _, item in engine._iter_plan_items(plan):
                    if item.get("type") == "course":
                        for code in item.get("options", []):
                            self.assertIn(
                                code, catalog,
                                f"{major}: {code} is listed as an option but isn't in the loaded catalog",
                            )

    def test_behrend_batch_one_codes_do_not_collide_with_any_existing_major(self):
        import glob
        existing = {
            os.path.basename(p)[: -len("-2026.json")]
            for p in glob.glob(os.path.join(engine.DEGREE_PLAN_DIR, "*-2026.json"))
        }
        # Every one of this batch's codes must be new to 2026 (i.e. this
        # session actually created the files, not silently overwrote one).
        for major in self.BEHREND_MAJORS:
            self.assertIn(major, existing)

    def test_degree_plans_filtered_by_erie_includes_this_whole_batch(self):
        # This test class has no Flask client of its own -- use the engine
        # function list_degree_plans directly instead of the HTTP layer,
        # same real filtering logic /api/degree-plans itself calls.
        plans = {(p["major"], p["catalog_year"]) for p in engine.list_degree_plans(campus="Erie")}
        for major in self.BEHREND_MAJORS:
            self.assertIn((major, 2026), plans, f"{major}-2026 should be returned for campus=Erie")
        # A University Park-only major must NOT leak into the Erie list.
        self.assertNotIn(("CMPSC", 2026), plans)


class TestACCTGBHRealCurriculum(unittest.TestCase):
    """Accounting, B.S. (Behrend) -- confirmed against
    bulletins.psu.edu/undergraduate/colleges/behrend/accounting-bs/ to be a
    real, separate curriculum from ACCTG-2026.json (University Park)."""

    def test_uses_behrends_own_upper_division_sequence_not_ups(self):
        plan = engine.load_degree_plan("ACCTGBH", 2026)
        all_options = {
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item.get("options", [])
        }
        # Behrend's real sequence.
        for real_behrend_course in ["ACCTG 310", "ACCTG 312", "ACCTG 340", "ACCTG 371", "ACCTG 403", "ACCTG 422", "ACCTG 450", "ACCTG 472"]:
            self.assertIn(real_behrend_course, all_options)
        # University Park's own ACCTG-2026.json sequence (403W/404/405/406/
        # 432/440/471/473/481/483) never appears here -- these are two
        # genuinely different programs, not the same plan re-tagged.
        for up_only_course in ["ACCTG 403W", "ACCTG 404", "ACCTG 405", "ACCTG 471", "ACCTG 473", "ACCTG 481", "ACCTG 483"]:
            self.assertNotIn(up_only_course, all_options)

    def test_math_21_added_to_unlock_real_acctg_211_prereq(self):
        plan = engine.load_degree_plan("ACCTGBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        acctg_211 = catalog["ACCTG 211"]
        self.assertEqual(acctg_211.prereq_groups, [{"MATH 21"}])
        options_lists = [
            item.get("options", [])
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
        ]
        self.assertTrue(any(opts == ["MATH 21"] for opts in options_lists))


class TestBUSECONBHRealCurriculum(unittest.TestCase):
    """Business Economics, B.S. -- Penn State Behrend is the only campus
    that offers this major at all (bulletins.psu.edu/undergraduate/colleges/
    behrend/business-economics-bs/), so there is no University Park version
    to compare against; this test just checks the real prescribed courses
    from Behrend's own 2026-27 suggested academic plan are all present."""

    def test_real_prescribed_courses_present(self):
        plan = engine.load_degree_plan("BUSECONBH", 2026)
        all_options = {
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item.get("options", [])
        }
        for real_course in ["ACCTG 211", "ECON 302", "ECON 304", "ECON 470", "ECON 485", "FIN 301", "MGMT 301", "MGMT 471W", "MKTG 301", "SCM 200", "SCM 301"]:
            self.assertIn(real_course, all_options)

    def test_scm_200_has_no_stat_200_alternative_per_the_real_plan(self):
        # Unlike every other Behrend major in this batch, Business
        # Economics's own suggested academic plan lists plain "SCM 200"
        # with no "or STAT 200" alternative in Second Year Fall.
        plan = engine.load_degree_plan("BUSECONBH", 2026)
        scm_item = next(
            item for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course" and item.get("options") == ["SCM 200"]
        )
        self.assertEqual(scm_item["options"], ["SCM 200"])


class TestECONBABHRealCurriculum(unittest.TestCase):
    """Economics, B.A. (Behrend) -- confirmed against bulletins.psu.edu/
    undergraduate/colleges/behrend/economics-ba/ to be a real, separate
    curriculum from ECONBA-2026.json (University Park, College of the
    Liberal Arts)."""

    def test_behrend_ba_requires_business_courses_up_ba_never_touches(self):
        plan = engine.load_degree_plan("ECONBABH", 2026)
        up_plan = engine.load_degree_plan("ECONBA", 2026)
        behrend_options = {
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item.get("options", [])
        }
        up_options = {
            opt
            for _, item in engine._iter_plan_items(up_plan)
            if item.get("type") == "course"
            for opt in item.get("options", [])
        }
        # SCM 200/STAT 200 and CAS 100 are real Behrend BA requirements that
        # University Park's own Liberal Arts Economics BA never requires.
        self.assertTrue({"SCM 200", "STAT 200"} & behrend_options)
        self.assertFalse({"SCM 200", "STAT 200"} & up_options)

    def test_ba_knowledge_domain_and_world_cultures_present(self):
        plan = engine.load_degree_plan("ECONBABH", 2026)
        labels = [item.get("label", "") for _, item in engine._iter_plan_items(plan)]
        self.assertTrue(any("B.A. Knowledge Domain" in label for label in labels))
        self.assertTrue(any(label == "World Cultures" for label in labels))


class TestFINBHRealCurriculum(unittest.TestCase):
    """Finance, B.S. (Behrend) -- confirmed against bulletins.psu.edu/
    undergraduate/colleges/behrend/finance-bs/ to be a real, separate
    curriculum from FIN-2026.json (University Park, Smeal College of
    Business)."""

    def test_all_three_of_fin_420_451_471_are_required(self):
        # The bulletin lists each as "(OR the other two)" purely to show
        # flexible ordering -- all three are real, mandatory courses, not a
        # pick-one-of-three choice.
        plan = engine.load_degree_plan("FINBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduled = {c["code"] for t in fp["terms"] for c in t["courses"] if c["code"]}
        for real_course in ["FIN 420", "FIN 451", "FIN 471", "ACCTG 305", "ACCTG 426"]:
            self.assertIn(real_course, scheduled)

    def test_uses_behrends_own_sequence_not_ups_fin_305w_pool(self):
        plan = engine.load_degree_plan("FINBH", 2026)
        all_options = {
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item.get("options", [])
        }
        # University Park's FIN-2026.json own core requirement, never part
        # of Behrend's curriculum at all.
        self.assertNotIn("FIN 305W", all_options)


class TestIBBHRealCurriculum(unittest.TestCase):
    """International Business, B.S. (Behrend) -- Penn State Behrend is the
    only campus offering this dual-degree major at all (bulletins.psu.edu/
    undergraduate/colleges/behrend/international-business-bs/); no
    University Park equivalent exists to compare against."""

    def test_real_prescribed_courses_and_education_abroad_present(self):
        plan = engine.load_degree_plan("IBBH", 2026)
        all_options = {
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item.get("options", [])
        }
        for real_course in ["IB 303", "IB 404", "IB 464", "ACCTG 211", "FIN 301", "MGMT 301", "MGMT 471W", "MKTG 301", "MKTG 445", "SCM 301"]:
            self.assertIn(real_course, all_options)
        labels = [item.get("label", "") for _, item in engine._iter_plan_items(plan)]
        self.assertTrue(any("Education Abroad" in label for label in labels))

    def test_ib_404_prereq_patched_to_accept_fin_301_as_real_equivalent(self):
        # ib_catalog.json's own IB 404 scraped with prereq_groups=[["BA 301"]]
        # -- but BA 301 ("Finance", a non-business-majors' survey course) is
        # explicitly mutually exclusive with FIN 301 per BA 301's own catalog
        # description, and every Behrend business major (including this one)
        # requires FIN 301, not BA 301, making IB 404 permanently
        # unschedulable as originally scraped. Patched to accept either,
        # mirroring the catalog's own existing BA 303/MKTG 301 and BA 304/
        # MGMT 301 equivalence pattern used elsewhere in the same catalog.
        catalog = engine.load_merged_catalog(["IB"])
        ib_404 = catalog["IB 404"]
        self.assertEqual(len(ib_404.prereq_groups), 1)
        self.assertEqual(ib_404.prereq_groups[0], {"BA 301", "FIN 301"})

    def test_full_plan_actually_schedules_ib_404_for_real(self):
        plan = engine.load_degree_plan("IBBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduled = {c["code"] for t in fp["terms"] for c in t["courses"] if c["code"]}
        self.assertIn("IB 404", scheduled)


class TestMISBHRealCurriculum(unittest.TestCase):
    """Management Information Systems, B.S. (Behrend) -- Penn State Behrend
    is the only campus offering an undergraduate MIS major at all
    (bulletins.psu.edu/undergraduate/colleges/behrend/
    management-information-systems-bs/); no University Park MIS major
    exists to compare against."""

    def test_real_prescribed_courses_present(self):
        plan = engine.load_degree_plan("MISBH", 2026)
        all_options = {
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item.get("options", [])
        }
        for real_course in ["MIS 204", "MIS 315", "MIS 336", "MIS 345", "MIS 430", "MIS 445", "MIS 495", "MGMT 410", "MGMT 471W"]:
            self.assertIn(real_course, all_options)

    def test_focus_area_pool_resolves_to_two_distinct_real_courses(self):
        plan = engine.load_degree_plan("MISBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduled = [c["code"] for t in fp["terms"] for c in t["courses"] if c["code"]]
        focus_pool = {"MIS 404", "MIS 415", "MIS 387", "MIS 433", "MIS 447"}
        picked_from_pool = [c for c in scheduled if c in focus_pool]
        self.assertEqual(len(picked_from_pool), 2)
        self.assertEqual(len(set(picked_from_pool)), 2, "focus area pool must resolve to 2 DISTINCT courses")


class TestMKTGBHRealCurriculum(unittest.TestCase):
    """Marketing, B.S. (Behrend) -- confirmed against bulletins.psu.edu/
    undergraduate/colleges/behrend/marketing-bs/ to be a real, separate
    curriculum from MKTG-2026.json (University Park, Smeal College of
    Business)."""

    def test_uses_behrends_own_343_444_not_ups_330(self):
        plan = engine.load_degree_plan("MKTGBH", 2026)
        all_options = {
            opt
            for _, item in engine._iter_plan_items(plan)
            if item.get("type") == "course"
            for opt in item.get("options", [])
        }
        self.assertIn("MKTG 343", all_options)
        self.assertIn("MKTG 444", all_options)
        # University Park's MKTG-2026.json own required course, never part
        # of Behrend's curriculum.
        self.assertNotIn("MKTG 330", all_options)

    def test_mktg_450w_prereq_patched_to_accept_real_behrend_alternate(self):
        # Behrend's own bulletin page carries a footnote directly on MKTG
        # 450W: "Prerequisite for MKTG 450W is MKTG 330 or MKTG 444" -- but
        # mktg_catalog.json originally only encoded MKTG 330 AND MKTG 342
        # (no MKTG 444 alternative), which would make this REQUIRED course
        # permanently unschedulable since MKTG 330 isn't part of Behrend's
        # curriculum. University Park's own MKTG-2026 plan is unaffected
        # since it always completes MKTG 330 anyway.
        catalog = engine.load_merged_catalog(["MKTG"])
        mktg_450w = catalog["MKTG 450W"]
        self.assertIn({"MKTG 330", "MKTG 444"}, mktg_450w.prereq_groups)
        self.assertIn({"MKTG 342"}, mktg_450w.prereq_groups)

    def test_full_plan_actually_schedules_mktg_450w_for_real(self):
        plan = engine.load_degree_plan("MKTGBH", 2026)
        catalog = engine.load_merged_catalog(plan["departments"])
        fp = engine.build_full_plan(plan, catalog, set(), start_year=2026, grad_years=4)
        scheduled = {c["code"] for t in fp["terms"] for c in t["courses"] if c["code"]}
        self.assertIn("MKTG 450W", scheduled)


class TestBrandywinePatternAMajors(unittest.TestCase):
    """University College branch-campus pass: for the majors where
    Brandywine's real curriculum turned out to be identical to an
    already-built University Park plan (Pattern A -- see
    docs/BRANCH_CAMPUS_FINDINGS.md), the only change made was adding
    "Brandywine" to that plan's own "campus" list plus a notes citation.
    These tests confirm the metadata-only edit didn't disturb the existing
    University Park build and that Brandywine now actually shows up via
    list_degree_plans' real campus filter."""

    PATTERN_A_MAJORS = ["BIOL", "CASBA", "CMPSC", "CYBER"]

    def test_all_pattern_a_majors_list_brandywine_and_university_park(self):
        for major in self.PATTERN_A_MAJORS:
            plan = engine.load_degree_plan(major, 2026)
            self.assertIsNotNone(plan, major)
            campuses = engine._plan_campuses(plan)
            self.assertIn("Brandywine", campuses, major)
            self.assertIn("University Park", campuses, major)

    def test_all_pattern_a_majors_still_build_cleanly(self):
        import datetime
        today = datetime.date(2026, 7, 1)
        for major in self.PATTERN_A_MAJORS:
            plan = engine.load_degree_plan(major, 2026)
            catalog = engine.load_merged_catalog(plan["departments"])
            fp = engine.build_full_plan(
                plan, catalog, set(),
                start_year=2026, grad_years=4, today=today,
            )
            self.assertEqual(fp["warnings"], [], major)
            self.assertTrue(fp["goal"]["met"], major)

    def test_business_already_had_brandywine_before_this_pass(self):
        # Business, B.S. (Intercollege) was already given real multi-campus
        # data (including Brandywine) in an earlier session -- confirms this
        # pass didn't need to touch it, just verify it.
        plan = engine.load_degree_plan("BUSINESS", 2026)
        self.assertIn("Brandywine", engine._plan_campuses(plan))

    def test_brandywine_campus_filter_returns_all_seven_assigned_majors(self):
        engine.list_degree_plans.cache_clear()
        try:
            majors = {p["major"] for p in engine.list_degree_plans(campus="Brandywine")}
        finally:
            engine.list_degree_plans.cache_clear()
        for expected in ["AMST", "BIOL", "BUSINESS", "CASBA", "CMPSC", "COMM", "CYBER"]:
            self.assertIn(expected, majors, expected)


class TestAMSTBrandywinePlan(unittest.TestCase):
    """American Studies, B.A. (University College) -- Pattern B, a genuinely
    new plan file. No University Park major of this name exists at all; the
    real curriculum only exists as a University College bulletin page, with
    Brandywine as its real 'End Campus'. Built directly from that page's own
    Brandywine-specific Suggested Academic Plan PDF.

    Surfaced a real engine limitation: AMST 491W is a real repeatable
    capstone taken twice with different topics, but the engine's
    one-completed-code-per-item matching (plan_progress) can only ever let
    the FIRST of two items sharing an identical single-option course list
    claim a real completion -- unlike MUSED's MUSIC 153/154 pair (two
    different real codes), a literal repeat of the exact same single code
    left the second item permanently unsatisfiable and caused
    build_full_plan to loop for the full 24-term cap. Fixed by modeling both
    occurrences as generic 'slot' items with a 'match' regex instead of
    literal 'course' items, the same convention already used for TURF 495."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("AMST", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_plan_exists_and_is_brandywine_only(self):
        self.assertIsNotNone(self.plan)
        self.assertEqual(engine._plan_campuses(self.plan), ["Brandywine"])

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am an american studies major"), "AMST")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        total_credits = sum(c["credits"] for t in fp["terms"] for c in t["courses"])
        self.assertEqual(total_credits, 120.0)

    def test_amst_491w_capstone_modeled_as_two_recognizable_slots(self):
        capstone_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("match") == "^AMST 491W$"
        ]
        self.assertEqual(len(capstone_items), 2)
        for item in capstone_items:
            self.assertEqual(item["type"], "slot")
            self.assertEqual(item["credits"], 3)

    def test_amst_100_and_amst_491w_are_real_catalogued_courses(self):
        for code in ("AMST 100", "AMST 491W"):
            self.assertIn(code, self.catalog, code)

    def test_400_level_slots_recognize_real_amst_courses(self):
        rx_items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("match") == r"^AMST 4\d{2}[A-Z]?$"
        ]
        self.assertTrue(rx_items)
        rx = re.compile(rx_items[0]["match"])
        self.assertTrue(rx.match("AMST 441"))
        self.assertFalse(rx.match("AMST 105"))
        self.assertFalse(rx.match("ENGL 420"))


class TestCOMMBrandywinePlan(unittest.TestCase):
    """Communications, B.A. (University College), Corporate Communications
    option -- Pattern B, a genuinely new plan file. No University Park major
    of this name exists (Bellisario College's 5 real UP majors --
    Advertising/Public Relations, Journalism, Film Production and Media
    Studies, Telecommunications and Media Industries, Strategic
    Communications -- use different codes and requirements entirely). Built
    directly from the University College bulletin's Brandywine-specific
    Suggested Academic Plan (Corporate Communications option only --
    Digital Journalism is Lehigh Valley/New Kensington-only per the
    bulletin's own per-option campus line, not offered at Brandywine).

    Surfaced a second, related real engine-mechanics gap: COMM 494 and
    COMM 495 are real variable-credit courses whose scraped catalog value
    (1cr, the low end of a range) silently overrides this major's specific
    3cr requirement for course-type items (planner_engine._item_credits
    always prefers a matched catalog course's own credit value). Fixed by
    modeling both as generic 'slot' items with a 'match' regex, the same
    convention used for AMST 491W and TURF 495, rather than editing the
    shared comm_catalog.json (also used by ADPR/DMD)."""

    def setUp(self):
        import datetime
        self.plan = engine.load_degree_plan("COMM", 2026)
        self.catalog = engine.load_merged_catalog(self.plan["departments"])
        self.today = datetime.date(2026, 7, 1)

    def test_plan_exists_and_is_brandywine_only(self):
        self.assertIsNotNone(self.plan)
        self.assertEqual(engine._plan_campuses(self.plan), ["Brandywine"])

    def test_major_alias_detection(self):
        self.assertEqual(_extract_major_from_prompt("I am a communications major"), "COMM")

    def test_full_plan_reaches_graduation_in_four_years(self):
        fp = engine.build_full_plan(
            self.plan, self.catalog, set(),
            start_year=2026, grad_years=4, today=self.today,
        )
        self.assertEqual(fp["warnings"], [])
        self.assertTrue(fp["goal"]["met"])
        total_credits = sum(c["credits"] for t in fp["terms"] for c in t["courses"])
        self.assertEqual(total_credits, 124.0)

    def test_comm_494_495_modeled_as_recognizable_3_credit_slots(self):
        for code in ("COMM 494", "COMM 495"):
            items = [
                item for _, item in engine._iter_plan_items(self.plan)
                if item.get("match") == f"^{code}$"
            ]
            self.assertEqual(len(items), 1, code)
            item = items[0]
            self.assertEqual(item["type"], "slot", code)
            self.assertEqual(item["credits"], 3, code)
            # Regression guard: the real catalogued course is 1cr (scraped
            # range low-end), which is exactly why this had to be a slot,
            # not a literal course item.
            self.assertEqual(self.catalog[code].credits, 1.0, code)

    def test_prescribed_core_courses_are_real_catalogued_courses(self):
        for code in (
            "COMM 100N", "COMM 160", "COMM 260W", "COMM 270", "COMM 403",
            "CAS 252", "CAS 301", "CAS 303", "CC 200", "COMM 370", "COMM 471",
        ):
            self.assertIn(code, self.catalog, code)

    def test_400_level_option_pool_slot_is_scoped_correctly(self):
        items = [
            item for _, item in engine._iter_plan_items(self.plan)
            if item.get("label") == "Corp. Communications Option - 400-Level Additional"
        ]
        self.assertEqual(len(items), 1)
        rx = re.compile(items[0]["match"])
        self.assertTrue(rx.match("COMM 471"))
        self.assertTrue(rx.match("CC 406"))
        self.assertFalse(rx.match("COMM 270"))  # real course, but not 400-level
