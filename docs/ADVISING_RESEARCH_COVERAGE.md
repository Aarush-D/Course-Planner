# How Course Planner Maps to Real Advising Failures

Read `docs/ADVISING_RESEARCH_FINDINGS.md` first — this file goes through each category of real,
documented advising failure found there and says honestly what this program currently does about it:
already covered by design, covered by a fix made specifically during this research pass, or a real,
acknowledged gap. Nothing here is a marketing claim — each line is checked against actual code or
actual test output (`docs/ADVISING_RESEARCH_TEST_OUTPUTS.md`).

## "Signed off, then reversed weeks before graduation" (UP Baguio, FAMU, McKnight)

**Structurally addressed, not just mitigated.** A human advisor's sign-off is a one-time snapshot that
can go stale or simply be wrong and not get re-checked. This program has no equivalent of "sign-off" —
`plan_progress()` and `build_full_plan()` (`Backend/planner_engine.py`) recompute a student's actual
status from scratch, against the real requirement list, every single time a plan is requested. There is
no stored "you're cleared" state that can silently drift out of sync with reality.

Scenario A in the test-outputs file is a direct stress test of this exact failure mode: a student who
believes they're nearly done ("I think I've taken everything") is told plainly, with the real numbers,
that they are not — 13/41 requirements, not on track, 5 extra terms needed. That's the answer a student
in the FAMU/UP Baguio situation needed to hear months earlier, not the week of commencement.

## "27 students blocked by an outdated gen-ed checklist" (stale curriculum data)

**Directly addressed by design.** The whole project's real-data-only discipline — 230 degree-plan files
scraped from PSU's live bulletin, one JSON file per (major, catalog year), the `catalog_year`/`start_year`
fields threading through every request — exists specifically so a plan reflects the requirements that
were actually real for the year a student started, not a document someone forgot to update.

Scenario B in the test-outputs file proves this isn't just an unused field: the same major, same empty
completed list, but a different `start_year` produces genuinely different real requirements and a
different graduation deadline (Spring 2025 for a 2021 start vs. Spring 2030 for a 2026 start). The
catalog-year distinction actually changes behavior; it isn't decorative.

## "One credit hour short due to advisor error" / missed a specific requirement

**Directly addressed.** Credit totals and requirement counts (`progress['creditsDone']`,
`progress['totalCredits']`, per-category breakdowns) are exact arithmetic over the real plan JSON, not a
human's mental tally. There's no mechanism by which a single requirement could be silently forgotten the
way FAMU's adviser forgot one — every item in every semester of the plan is checked, every time.

## "Am I on track to graduate?" / "What should I take?" — the ~50% never-answered gap

**This is the single most directly on-point finding, and it drove two real fixes made during this same
session.** The Inside Higher Ed survey found only ~52% of students were ever told whether they were on
track, and only ~55% were advised on required course sequences — meaning roughly half were never given a
straight answer to the two most basic planning questions a student can ask.

Before this research pass, this program's own chat had a real version of the same gap: asking "what
should I take next semester?" got a bare count ("6 courses recommended...") instead of an actual answer,
and asking "why can't I take CMPSC 465?" got whatever 3 courses happened to be in a generic blocked-list,
not necessarily the one actually asked about. Both are now fixed (`_is_asking_next_courses`,
`_is_asking_why_blocked` in `Backend/app.py`) — see Scenarios D and E in the test-outputs file for the
real, current answers to exactly the two questions the survey found students weren't getting.

## Advisor churn / conflicting guidance (Penn State's own "four advisers" story, Rutgers)

**Directly addressed by construction, not policy.** `build_full_plan`/`recommend_semester` are pure
functions of (major, catalog year, completed courses, settings) — the same inputs always produce the same
real output. There is no equivalent of "advisor A" vs. "advisor B" giving different answers, because
there's no second source of truth to disagree with the first.

Scenario C in the test-outputs file tests this directly: the identical question, asked in two separate
requests, produces byte-identical underlying facts (`progress`, `goal`, the real course list) both times
— confirmed by inspecting the raw JSON, not just eyeballing the prose. The *phrasing* varies turn to turn
(deliberately — see the reply-variety work earlier this session), but the substance never does. That's
the direct answer to "it didn't always feel like I was getting the right information."

## Transfer credit issues (Aggie, Hechinger, Complete College America)

**A real, acknowledged, partial gap — not claiming this is solved.** `/api/transfer-credit` (Scenario F)
correctly ranks real nearby Pennsylvania community colleges by distance, using real institution and
coordinate data — that part is genuine and already useful. But `courses_covered_count` is honestly always
`0` in the current response: actual course-equivalency mapping (does *this specific* community college
course really transfer as *this specific* PSU course) isn't built yet, because PSU's LionPATH Transfer
Credit Tool has no public API to scrape from (`Backend/app.py`'s own docstring says so directly). This
matches exactly the gap the research surfaced — transfer students are the group most hurt by advising
that requires individualized work advisors skip — and it's already tracked as open work in
`docs/COMPLIANCE_BACKLOG.md` rather than quietly ignored.

## Minor/double-major complications (implied by the transfer-credit and gen-ed findings' broader theme of "individualized situations get worse advising")

**Addressed with an honest "I don't know, please confirm" instead of a guess.** A double major or minor
is exactly the kind of individualized case that's easy for a rushed advisor (or a naive chatbot) to get
wrong by assuming instead of asking. `_detect_unconfirmed_major_mentions` catches a chat message like "I
want to double major in MATH and ECON" when only MATH could actually be set from the text, and the
program asks the student to confirm rather than silently picking one — see Scenario G. This is the
opposite of the FAMU/UP Baguio failure mode: instead of confidently asserting something that might be
wrong, it says plainly what it doesn't know.

## Wrong-major / wasted-credits pattern (Complete College America's 136.5-credit finding)

**Addressed for the specific case of an undecided student**, via the newly-built major-exploration mode
(`/api/explore-majors`) — see Scenario H. A student who says they don't know their major yet gets asked a
real narrowing question instead of being pushed into picking something (or nothing) blind; once they
share real interests, suggestions are grounded only in the real major list (`_real_majors_summary`),
never invented. This doesn't solve the credit-waste problem for a student who's *already* picked the
wrong major and needs to switch — that's a genuinely different, harder problem (which courses transfer
between two majors' requirements) not yet built.

## What this pass did NOT find evidence for either way

Being honest about the limits of this research: the Reddit inaccessibility (see the findings file) means
there's no PSU-specific, student-voice evidence beyond the one Daily Collegian article. The legal cases
(Sain, Scott) are high-school NCAA-eligibility cases, not college degree-planning — included as
reinforcing precedent for "wrong information about requirements causes real harm," not as a direct PSU
advising complaint. If genuine Reddit access becomes available, re-running this research specifically
against r/PennStateUniversity remains a real, valuable follow-up (tracked in
`docs/COMPLIANCE_BACKLOG.md`).
