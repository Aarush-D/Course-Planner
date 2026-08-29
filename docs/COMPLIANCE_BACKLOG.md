# Backlog — Deferred Items to Revisit

A single reference list of everything that's been intentionally set aside, across every part of this
project — compliance follow-ups, data/feature work, and infrastructure decisions. Nothing here is started;
this exists so it doesn't fall through the cracks. Update this file (don't create a new one) as items get
resolved or new ones come up.

---

## Compliance & legal

Triggered by `docs/COMPLIANCE_AUDIT.md` — revisit each when its trigger condition happens, not before.

- **Real legal review of the Privacy Policy and Terms of Service.** Both are grounded in an honest audit of
  what the app actually does, but neither has been reviewed by an actual attorney. Do this before treating
  either as legally sufficient, and definitely before any real commercial launch.
- **GDPR applicability** — revisit if the app ever targets or monitors EU users at real scale (real accounts,
  behavioral tracking). Not triggered today: no accounts, no tracking.
- **CCPA/CPRA applicability** — revisit if the project ever has real revenue/user-count that could cross
  California's coverage thresholds. Not triggered today.
- **Business formation (LLC/corporation, tax ID, registration)** — only relevant if this becomes a real
  commercial product. A decision for Aarush/Suryansh/Justin with real advisors, not something to resolve in
  code.
- **`.venv/` git history cleanup** — removing it from *current* tracking (done) doesn't purge the old blobs
  from git history or the GitHub remote. A full purge needs `git filter-repo`/BFG, which rewrites every
  commit hash and breaks existing clones — a real, invasive decision that needs explicit sign-off, not
  something to do unilaterally. No real secret was found inside it, so this is a repo-hygiene/size issue,
  not an urgent security one.
- **Full accessibility audit** — current state is a partial pass (every input labeled, icon buttons have
  aria-labels). A real WCAG pass (axe-core/Lighthouse, keyboard-nav walkthrough, screen-reader test) hasn't
  been done.
- **Full security pen-test pass** — core checks are done (no exposed secrets, no SQL/command injection
  surface, CORS restricted, debug mode off by default, live-tested the Ollama integration). Not yet done:
  XSS-injection attempts into the chat textbox, rate-limit/spam abuse testing, and a real dependency
  vulnerability scan (`pip-audit`, `npm audit`).
- **Revisit Database Security and Authentication sections of the compliance audit** — **now triggered**
  (Supabase + advisor accounts landed, see below). `docs/COMPLIANCE_AUDIT.md` sections 5 and 6 still read
  "N/A — nothing exists yet," which is no longer true; rewrite them to describe the real RLS policies
  (`supabase/migrations/0001_advisor_workspace.sql`) and Supabase Auth setup. Not yet done — flagged here so
  it doesn't sit stale.

## Infrastructure — when to actually add it, not before

- ~~**Supabase** (hosted Postgres + auth)~~ — **Done.** Real student accounts still don't exist (students
  never sign up), but the trigger condition was met by the two-way advisor workspace instead: advisor
  accounts (Supabase Auth) plus persisted review requests/comments/meeting proposals
  (`supabase/migrations/0001_advisor_workspace.sql`). Frontend talks to Supabase directly
  (`Frontend/src/services/supabase.service.ts`, `review-request.service.ts`) — Flask/`planner_engine.py`
  stay untouched. This is the first real database and first real accounts anywhere in the project; see the
  now-triggered compliance-audit item above.
- **Upstash (Redis)** — earns its place for rate-limiting/caching once the backend is actually deployed
  publicly and facing real concurrent traffic. Don't add it speculatively.
- **Ollama Cloud vs. self-hosted** — cloud mode is wired up and tested (`OLLAMA_API_KEY` env var). The free
  tier caps at **1 concurrent generation** (verified live, not just from docs) — fine for light/spread-out
  usage, a real bottleneck under any burst of simultaneous users. Revisit (Pro plan, or a different hosted
  provider) once real concurrent load is a live problem, not before.
- **Deploying the backend + frontend publicly** — gunicorn/Procfile are ready (see the Tier-0 scaling work).
  Not yet deployed anywhere. See the hosting-cost writeup for the actual free-tier plan when this happens.
- **Public/integration API** (a documented, stable `/api/*` surface for external tools to build against —
  a real advisor dashboard, another student-built app, or a university's own system pulling a plan) —
  earns its place once there's an actual external consumer asking for it, not before. Today's `/api/*`
  routes (`Backend/app.py`) are internal, shaped around this frontend's own needs, with no versioning,
  auth/API-key scheme, or rate-limiting for third-party callers — all of that is real design work
  (versioning strategy, what a caller authenticates as without real user accounts, docs) that shouldn't be
  guessed at speculatively. Flagged from the Aug 2026 competitive-landscape report as a way to close the
  "no advisor workspace" gap without building a full collaboration UI ourselves — e.g. a read-only
  plan-share endpoint another tool could embed. (A lighter-weight version of exactly this shipped
  without a backend API at all — see the `?shared=` read-only link below, which encodes the whole
  plan state client-side since `/api/plan` is stateless. This entry is still real: a proper API is a
  different, more capable thing than a URL token.)
- ~~**Missing `404.html` SPA fallback for GitHub Pages.**~~ — **Done.** `Frontend/public/404.html`
  (copied into every build via `angular.json`'s new `assets` entry) redirects a fresh/direct hit on
  any deep route to the app root with the intended path folded into a `?redirect=` query string;
  `Frontend/index.html`'s own pre-boot script restores it via `history.replaceState` before Angular's
  router reads the location. Verified locally (dev server) with both a top-level and a nested path;
  a real GitHub Pages check still worth doing once this is live, since `ng serve` never reproduces the
  underlying 404 in the first place. Landed as groundwork for the advisor-workspace routes
  (`/advisor/login`, `/advisor/dashboard`, `/advisor/review/:id`) needing real bookmarkable paths.

## Academic data / feature work

- **Course-quality/difficulty signal in Recommendations — genuinely blocked, both paths ruled out
  (researched Aug 2026, not just deferred).** (a) RateMyProfessor's Terms of Service explicitly
  prohibit scraping/automated access, with an active cease-and-desist enforcement history against
  people who've tried anyway — not worth the legal/reputational risk. (b) Penn State's own
  SRTE/SEEQ course-evaluation results (`rateteaching.psu.edu`) are confidential personnel records,
  gated behind institutional login *and* admin-granted access — not obtainable by an unofficial
  third-party tool at all, not just hard to get. No workaround exists for either; Recommendations
  keeps ranking by prereq-unlock centrality only (`score_recommendations` in `planner_engine.py`)
  until a real, legitimately-licensed data source turns up.
- **Re-run the real-advising-failures research against Reddit specifically** once access is available —
  the 2026-08-24 pass (`docs/ADVISING_RESEARCH_FINDINGS.md`) could not reach reddit.com/r/PennStateUniversity
  at all (confirmed blocked, not just unproductive) and substituted student-newspaper/legal/higher-ed-research
  sources instead. Real, but not the PSU-specific student-voice source originally requested.
- **Transfer credit course-equivalency mapping** — `/api/transfer-credit` honestly returns
  `courses_covered_count: 0` for every college today; only the real distance-ranking half is built (see
  `docs/ADVISING_RESEARCH_COVERAGE.md` for how this maps to real student complaints about transfer advising).
  Blocked on the same LionPATH Transfer Credit Tool public-API gap as the item below.
- ~~**ALEKS math placement exam**~~ — **Done (2026-08-25).** Real score bands (30/46/61/76) pulled from
  bulletins.psu.edu's Mathematics Placement PDF; `detect_math_placement`/`math_placement_satisfied`/
  `expand_math_placement` in `planner_engine.py` waive developmental math (MATH 21/22/26/41) once a higher
  math course is completed or a real ALEKS score/high-school-calculus mention proves it unnecessary — a
  placement score alone never waives the actual target course (MATH 110/140+), only real completed credit
  does. Also fixed: MATH 3/MATH 4 were wrongly required in ~150+ plans despite being explicitly
  non-degree-applicable per their own bulletin description.
- **Grade-minimum ("C or higher required") tracking — DEFERRED, explicitly, until the transcript feature is
  revisited.** Several CMPSC courses (CMPEN 270/331, CMPSC 121/131, 122/132, 221, 311, 360, 461, 464, 465,
  473 — per the real EECS department handbook) require a C or higher to graduate, not just a passing grade;
  this is true for other majors too, not just CMPSC. The app has no grade field anywhere today — `completed`
  is a flat set of course codes with no notion of what grade was earned, so this can't be enforced or even
  displayed right now. Aarush's explicit call (2026-08-26): skip this for now, but build it together with a
  future transcript-upload pass rather than as a standalone feature — a real transcript already shows the
  grade per course, so parsing one is the natural place to also capture it, instead of asking a student to
  separately re-enter grades that were already typed into the completed-courses list. Revisit both together.
- **High school / transfer credit intake** — AP courses, A-Levels, existing college credit transfers, and
  CLEP exams, and how each maps onto real degree-plan course codes. Check what the existing "Transferred
  courses" page already handles before scoping new work — likely partial overlap.
- **LionPATH enrollment hand-off (Aarush's idea, 2026-08-26)** — a button on a student's plan that takes
  their recommended next-semester courses and either imports them into LionPATH directly or opens LionPATH
  positioned to enroll in them, instead of the student re-typing course codes into LionPATH by hand.
  Completely unscoped — no research yet into whether LionPATH exposes any integration surface a third-party
  app could use (a real API, a deep-link/URL scheme into the enrollment/shopping-cart flow, or nothing at
  all). Needs a real research pass before any design work: this may turn out to be blocked entirely if
  LionPATH has no public integration point, the same kind of wall already hit with the Transfer Credit Tool
  (see the transfer-credit item above).
- **Branch campus Phase 2** — extend the Phase 1 metadata-only campus pass (done for one major) to the rest
  of the ~230 existing degree-plan files. Sized to the number of files, not the number of campuses — see
  `docs/BRANCH_CAMPUS_FINDINGS.md` §5 for the full phased plan.
- **Branch campus Phase 3** — first "Pattern B" major (a campus with a genuinely different curriculum, not
  just the same courses), needs a wholesale new `degree_plans` file, not just metadata.
- **Branch campus Phase 4** — course-offering honesty pass, once Phases 1–3 establish which courses are real
  at which campuses.
- **Graduate programs** — scoped in `docs/GRAD_AND_WORLD_CAMPUS_FINDINGS.md` Part 1; recommended first phase
  identified there but not built. The real list of ~175 majors + 13 minors PSU actually offers at University
  Park (bulletin-sourced, with 11 medicine-adjacent programs individually verified after catching Hershey-only
  programs hiding with no campus suffix in the raw directory) now lives in
  `docs/GRAD_UNIVERSITY_PARK_MAJORS_MINORS.md`, for whenever that build-out starts.
- **World Campus** — scoped in the same doc, Part 2; the "no University Park offering" mislabeling issue was
  already fixed as part of the multi-campus schema work, but the recommended first phase (year-vs-semester
  granularity for at least one real World Campus program) isn't built yet.
- **Smeal College of Business minor-declaration restrictions** — researched, no bulletin-published evidence
  found (same pattern as an earlier Data Science/CMPSC finding). No further action pending unless Aarush
  finds a different source.
- **Sub-quota / cross-category rules within a single requirement bucket — not modeled, same reason as
  grade-minimums (no fine-grained tracking of *which specific* course satisfied a bucket).** Found during
  the 2026-08-27 Arts and Architecture handbook-verification pass across all 16 College of Arts and
  Architecture majors (ACTING, AED, ARCHBARCH, ARTH, DAMD, DMD, GD, LARCH, MUSED, MUSIC, MUSICBM, MUSTECH,
  MUSTHEA, PPHOTO, THEA, THEABFA). Examples: Art History's 'Additional Courses' must include one Western and
  one non-Western pick specifically (the engine models the real 12-course list but doesn't enforce the
  Western/non-Western split); Art History's 'Support Course Geographic Area' needs one course from 3 of 4
  geographic categories (modeled as open-pool ARTH-only, category-blind); Art History's Supporting Courses
  need at least 12cr at 400+ level with ARTH 495 excluded from that sub-count; Music, B.M. (Keyboard)'s
  Ensemble credits need 2 of 8 to specifically be MUSIC 193/194. None of these are silently ignored — each is
  documented in the relevant plan JSON's own `notes` field — but revisit alongside the grade-minimum work
  above if the schema ever grows a real "why did this course count here" audit trail.
- **College of Health and Human Development handbook verification (2026-08-27)** — done for all 9 majors
  (BBH, CSD, HDFS, HM, HPA, KINES, NROSCI, NUTR, RPTM), same depth as the CMPSC pass. No separate
  department handbook was publicly accessible for any of them (BBH's and HM's do exist but return 403 —
  PSU-authenticated only); every fix instead traces to the live bulletin's own Suggested Academic Plan
  footnotes or a real, public department "supporting courses" / "elective options" page. Two follow-ups
  worth revisiting if these departments' catalog scraping ever gets refreshed:
  - HPA's and RPTM's "Supporting Course" pools were wired to a deliberately bounded real subset (only
    codes within departments those plans already load), not the full department-published list, which
    spans 25-40+ departments this app doesn't scrape catalogs for (HPA: hhd.psu.edu/hpa/supporting-courses;
    RPTM: hhd.psu.edu/rptm/undergraduate/supporting-courses, Commercial Recreation and Tourism Management
    section). Widening either requires scraping those extra departments first.
  - RPTM 433W's real bulletin prereq is "RPTM 356 and a 3-credit course in statistics" — RPTM 356 does not
    exist in the current catalog at all (confirmed, not just missing from a plan) and is left permanently
    unenforced in rptm_catalog.json; only the statistics half was added. If PSU ever republishes what
    RPTM 356 became, fix the catalog entry properly instead of guessing at it.
  - A handful of real, bulletin/department-page-sourced course codes across BBH, CSD, HDFS, HM, KINES,
    NROSCI, and NUTR have no matching entry in this app's own scraped catalogs (scraping gaps, not bulletin
    errors — e.g. BIOL 246W, FOR 201, HDFS 210Z) and are left as unchecked fallback options in their plans.
    A catalog re-scrape for BIOL/FDSC/GEOG/HDFS/SOC/SPAN/WMNST/etc. would let these resolve properly.
- ~~**`_pick_open_elective` doesn't recognize honors/non-honors course pairs as duplicates**~~ — **Done
  (2026-08-27).** Found verifying MATH/PHYS/PLANET/STAT against their real handbooks/bulletins: a student who
  already completed MATH 220 (Matrices) could still be recommended MATH 220H (Honors Matrices) by an
  open_elective "Supporting Course" slot, since `completed` was matched by exact code only. Fixed generally
  in `planner_engine.py` via `_honors_base_code`/`_is_effectively_completed` (strips a bare trailing "H"
  after the course number — PSU's consistent honors-section marker, unlike W/N/Y suffixes which denote a
  genuinely different course), shared by both `_pick_open_elective` (broad catalog search) and
  `_ranked_options` (a single item's own curated option list, e.g. a plan explicitly offering "MATH 220 or
  MATH 220H" as alternatives elsewhere while a different, already-completed item covers one of them).
- **MICRB Elective List A/B/C and PLANET's "Application Area"-style course-by-course membership isn't
  published anywhere public** — the live bulletin (bulletins.psu.edu) names these categories and their
  credit totals precisely, but not their actual member courses; the department's own internal
  handbook/checksheet with the full lists wasn't found on a public page (unlike Mathematics's own
  science.psu.edu/math/undergraduate/math-major/supporting-courses page, which is public and was used).
  These slots' credit totals were corrected to match the bulletin, but they remain generic unfillable
  placeholders rather than open_elective/match picks — wiring them without the real list risked
  recommending courses far outside the field (observed and fixed for PHYS's own Supporting Course during
  this same pass). Revisit if the real list ever surfaces (e.g. a student shares their department
  checksheet, or the department publishes one).
- **"All N credits must come from one track" coherence rule for Application Focus / Supporting Course
  slots — not enforced, across every College of IST major (AIMA, CYBER, ETI, HCDD, IEC, SRA, DATSC).** Real
  handbook/bulletin verification pass (2026-08-27) wired these slots with a `match` regex covering the union
  of every named track's real courses (same mechanism as CMPSC's curated technical-elective lists), which
  correctly credits/validates a student's real completed coursework — but the engine has no per-plan
  "these N items must all pick from the same track" constraint, so nothing stops it from mixing tracks
  across a major's 3-4 Application Focus items. Same class of deferral as CMPSC's grade-minimum tracking:
  real, known, and not silently ignored, but out of scope for a plan-JSON-only fix.
- **AIMA's "Support Course — Technical/Application" slots have no enumerated course list anywhere** (checked
  both the live bulletin and ist.psu.edu's own `aima-major-requirements` advising page) — unlike every other
  IST major touched in the same pass, there's no real source to build a `match` list or a department
  allowlist from without fabricating one. Revisit if/when EECS or IST publishes a real AIMA handbook with
  this granularity (the same kind of document that made CMPSC's own verification possible).
- **B.S., Security and Risk Analysis is no longer enrolling new University Park students as of July 1,
  2025** (per ist.psu.edu's own "Important Update" banner on the major's page) — students admitted fall 2025
  or earlier can still complete it under unchanged requirements, so `SRA-2026.json` is left intact and
  accurate for them, but the major shouldn't be offered as a live choice to a new/incoming student in this
  app's UI. No UI-level enrollment-status gating exists today for any major; revisit if this app ever needs
  to distinguish "real degree, but closed to new students" from "actively enrolling."
- **Smeal "Two-Piece Sequence" requirement — DEFERRED, engine has no mechanism for it.** ACCTG/CIE/FIN's
  2022-23 and 2023-24 catalog years require a "Smeal Two-Piece Sequence" for their Supporting Courses /
  Related Area requirement: pick ONE themed category (e.g. Business Law, Finance, Real Estate, Marketing)
  from ugstudents.smeal.psu.edu's real per-major Degree Requirements page, then complete BOTH of that
  category's two named courses — a fundamentally different shape than the flat "any N courses from one big
  list" Business Breadth format those same majors switched to starting 2024-25 (wired via the `match`
  mechanism, see `TestSmealBusinessCoreHandbookRequirements` in `tests.py`). Left as an unenforced
  placeholder for those two years rather than modeled incorrectly as a flat list — each affected item's own
  `notes` field documents this. Would need a new plan-item shape (something like
  `"two_piece_categories": [{"label": ..., "courses": [...]}, ...]`, satisfied once both courses in any ONE
  category are completed) plus matching `_pick_*` / `plan_progress` support in `planner_engine.py`.

## Team / attribution

- **Full last names for Suryansh and Justin** — currently credited as "Suryansh S." and "Justin H." (first
  initial only, per Aarush's instruction) across all showcase materials (digital doc, leave-behind, poster,
  landing page). Update everywhere consistently once/if full names are wanted, and note their specific
  contribution areas as those become concrete.
