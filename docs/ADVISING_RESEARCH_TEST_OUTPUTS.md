# Real Test Outputs — Advising-Failure Scenarios

Read `docs/ADVISING_RESEARCH_FINDINGS.md` and `docs/ADVISING_RESEARCH_COVERAGE.md` first. This file is the
receipts: real requests sent to the actual running backend (not mocked, not hand-written) for scenarios
modeled directly on the research findings, with the real responses. All captured 2026-08-24 against the
code at commit `bdb20335`.

## A — Student believes they're done, but isn't (FAMU / UP Baguio pattern)

**Setup:** CMPSC major, started 2022, 4-year goal, 14 real completed courses (enough to sound
plausible as "everything").
**Prompt:** *"Am I ready to graduate? I think I've taken everything."*

> Hi there! Based on the facts, you've completed 13 out of 41 requirements for your CMPSC major plan,
> which means you're not on track to graduate in four years. You still have some locked courses that
> require prerequisites to be met. Additionally, you'll need to take more than six courses this spring to
> get back on track. It looks like you'd need five extra terms to graduate by Spring 2026. Let's work
> together to identify the missing pieces and create a plan to help you achieve your graduation goal!

Real underlying facts: `doneItems: 13/41`, `creditsDone: 44.0/126.0`, `goal.met: False`, deadline pushed
to Spring 2026. This is the direct opposite of the FAMU/UP Baguio failure — the gap surfaces the moment
it's asked about, not the week of commencement.

## B — Stale-curriculum-data check (outdated gen-ed checklist pattern)

Same major (CMPSC), same empty completed list, two different `start_year` values.

**B1 — started 2021:**
```
0/42 requirements complete on the CMPSC 2026 plan, not on track for a 4-year graduation.
...
⚠ Graduating in 4 years (by Spring 2025) doesn't fit — 8 extra term(s) needed.
```

**B2 — started 2026:**
```
0/42 requirements complete on the CMPSC 2026 plan, not on track for a 4-year graduation.
...
⚠ Graduating in 4 years (by Spring 2030) doesn't fit — 1 extra term(s) needed.
```

The deadline (Spring 2025 vs. Spring 2030) genuinely changes with the real year the student started —
not a cosmetic label, an actual different computed result. This is what "always synced to the current
requirement set" means concretely, not just as a claim.

## C — Consistency check (Penn State's "four advisers" / Rutgers "conflicting guidance" pattern)

Identical request sent twice, as two separate sessions, no shared state.
**Prompt (both):** *"What should I take next semester?"*, CMPSC, started 2024, completed CMPSC 131/132,
MATH 140/141.

Real underlying facts, both requests: `doneItems: 4/41`, `creditsDone: 14.0/126.0`,
`goal.deadline: Spring 2028`, `goal.met: False` — byte-identical between C1 and C2. The prose phrasing
varies turn to turn by design; the substance never does.

**This scenario is also where a real bug was found and fixed — see the next section.**

## The bug this testing pass actually found and fixed

Before the fix, C1's phrased reply read:

```
* ENGL 15 (3 cr) to unlock future courses
* GEN ED (3 cr) to fulfill one of the Semester 1 requirement slots
* GEN ED (3 cr) to fulfill another Semester 1 requirement slot
* PHYS 211 (4 cr) as an Entrance-to-Major requirement and to unlock more courses
```

Four items. The real plan for this exact student has **six**: ENGL 15, three separate GEN ED slots (not
two), PHYS 211, and First-Year Seminar. The LLM phrasing pass — even with an explicit "name every one of
those courses" instruction — silently dropped First-Year Seminar entirely and under-counted the Gen Ed
slots. This is exactly the FAMU-adviser failure mode ("overlooked that she still needed a required
course"), just occurring at the level of a single chat reply instead of an institutional sign-off.

The fix (`_next_sem_fully_covered`, `_build_next_sem_detail_block` in `Backend/app.py`) checks whether the
phrased reply actually names every real course *and* gets duplicate-slot counts right, and deterministically
appends the guaranteed-complete list when it doesn't. Re-running the identical C1/C2 requests after the fix:

**C1 after the fix** (LLM phrasing plus the guaranteed block):
```
Hi there! Based on your CMPSC 2024 plan, I recommend taking the following courses next semester:

* ENGL 15 (3 cr) to unlock future courses
* GEN ED (3 cr) for one of your general education requirements
* GEN ED (3 cr) for another general education requirement
* PHYS 211 (4 cr) as an Entrance-to-Major requirement and to unlock more future courses
* First-Year Seminar (1 cr) to fulfill a requirement
...

For Spring 2027 (17 credits), you need:
  • ENGL 15 (3 cr) — Semester 1 on the CMPSC flowchart; unlocks 104 future courses.
  • GEN ED (3 cr) — Semester 1 requirement slot — pick any course satisfying it.
  • GEN ED (3 cr) — Semester 1 requirement slot — pick any course satisfying it.
  • PHYS 211 (4 cr) — Semester 2 on the CMPSC flowchart; Entrance-to-Major requirement; unlocks 58 future courses.
  • GEN ED (3 cr) — Semester 2 requirement slot — pick any course satisfying it.
  • First-Year Seminar (1 cr) — Semester 2 requirement slot — pick any course satisfying it.
```

**C2 after the fix:** the LLM phrasing again dropped First-Year Seminar on its own — confirming this
isn't a one-off fluke, it's a real, repeatable LLM reliability gap — but the deterministic block was
correctly appended both times, so the student sees the complete, correct list regardless. Full regression
test: `Backend/tests.py`'s `TestNextSemCoverageGuarantee`, including a live end-to-end check against
`/api/plan` (not just the unit-level string check).

## D — "Am I on track to graduate?" (the Inside Higher Ed ~48%-never-answered question)

**Setup:** CMPSC, started 2024, only CMPSC 131 completed.
**Prompt:** *"Am I on track to graduate in 4 years?"*

> Hi there! According to your plan, you've completed 1/41 requirements for the CMPSC 2024 plan, which
> means you're not on track for a 4-year graduation. You still have some prerequisites locked, such as
> MATH 230, PHYS 212, and CMPSC 311. To graduate in 4 years, you'll need to take additional courses or
> consider summer classes. Let's work together to get you back on track!

A direct, specific answer — the exact thing roughly half the students in the IHE survey said they never
got from a human advisor.

## E — "Why can't I take X?" (specific-course question, real prerequisite data)

**Setup:** CMPSC, started 2024, nothing completed.
**Prompt:** *"Why can't I take CMPSC 465?"*

> Hi there! I understand you're interested in taking CMPSC 465, but I need to let you know that you
> haven't completed the necessary prerequisites yet. According to our records, you still need to take
> either CMPSC 122 or CMPSC 132 and either CMPSC 360 or MATH 311W before being eligible for CMPSC 465.
> Let's focus on getting those requirements met first!
>
> CMPSC 465 (Data Structures and Algorithms) — needs: CMPSC 122 or CMPSC 132; CMPSC 360 or MATH 311W.
> You haven't completed that yet.

Both of CMPSC 465's real prerequisite groups (`{CMPSC 132, CMPSC 122}` and `{CMPSC 360, MATH 311W}`,
confirmed directly from the scraped catalog) are named correctly — this is the kind of specific,
verifiable eligibility answer the NCAA-eligibility legal cases (Sain, Scott) show real harm comes from
getting wrong.

## F — Transfer credit (honest partial-coverage check)

**Request:** `POST /api/transfer-credit`, zip `16801`, courses `["CMPSC 131", "MATH 140"]`.

Real response: 16 real Pennsylvania community colleges returned, ranked by real distance (nearest:
Pennsylvania Highlands Community College, 59.4 miles). Every college's `courses_covered_count` is
honestly `0` — actual course-equivalency data isn't built yet (no public API for LionPATH's Transfer
Credit Tool). The distance ranking is real and useful; the equivalency claim is not yet made, matching
the honest gap described in the coverage file — not overstated, not hidden.

## G — Ambiguous double major (confirm-instead-of-guess check)

**Prompt:** *"I want to double major in MATH and ECON"* — no major set previously.

> Hi there! I'm happy to help you explore double majoring in MATH and ECON. However, I want to confirm
> that you're aware that adding a second major requires a separate plan, which we can discuss further if
> you'd like. Currently, your MATH 2026 plan has 0/43 requirements complete and is not on track for a
> 4-year graduation, so we'll need to work together to get back on track. Let's focus on the Spring 2027
> recommendations first.
>
> Just to confirm — you also mentioned ECON. I only set MATH for now, since I can't add a second major
> or minor from chat text alone. Was that meant as one? Pick it from the Major/Minors fields above if so,
> and I'll fold it into your plan.

Only MATH gets set; ECON is flagged and confirmed rather than silently assumed either way.

## H — Undecided-major exploration (wrong-major/wasted-credit pattern)

**H1 — first turn**, prompt *"I don't know what major I want"*:
> I'm excited to help you explore your options! Since you haven't decided on a major yet, let's start by
> asking: What subjects do you enjoy learning about or exploring in your free time?

**H2 — second turn**, prompt *"I like math and building things, not really people-facing work"*:
> It sounds like you enjoy problem-solving and hands-on work! With that in mind, I think you might
> appreciate the Engineering College's options. For instance, the Biomedical Engineering major combines
> math and building skills with a focus on medical applications. Alternatively, the Computer Science
> major could be a good fit, as it involves both logical thinking and creative problem-solving. Would you
> like to learn more about either of these majors or explore other options?

Both suggested majors (Biomedical Engineering, Computer Science) are real majors from the actual PSU
catalog (`_real_majors_summary`) — the exploration prompt is instructed never to suggest one that isn't.

---

## Reproducing this

The scenario script lives at
`/private/tmp/claude-501/-Users-aarush-Course-Planner/c6575a38-cd40-4972-8f9c-58bd98693dab/scratchpad/advising_scenarios.py`
(session scratch space, not committed to the repo) — run it against a live `python app.py` on port 5001
to regenerate all of the above. The permanent, repo-tracked regression coverage for the bug this pass
found is `Backend/tests.py`'s `TestNextSemCoverageGuarantee`, `TestNextCoursesQuestion`, and
`TestSpecificCourseQuestion` classes, which run as part of the normal test suite.
