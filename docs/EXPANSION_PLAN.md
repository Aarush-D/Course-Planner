# Course Planner Expansion Plan

Status tracker + technical design for scaling the planner beyond CMPSC/Premedicine,
adding historical catalog years, and a new flowchart view. Written 2026-07-17.

Update the **Status** column as work lands. Each ✅ row should correspond to a real
git commit — that's the checkpoint discipline this plan is built around.

## Status at a glance

| # | Feature | Status |
|---|---|---|
| 1 | All PSU majors — discovery + build pipeline | 📝 Planned (this doc); 2 of ~194 majors built |
| 2 | Catalog-year back-referencing (2022–2026) | ✅ Done — CMPSC and PREMED, all 5 years |
| 3 | Chat-based start-year override | ✅ Done |
| 4 | Gen Ed fulfillment guidance | ⛔ Blocked — needs more info from Aarush |
| 5 | Transfer Credit Tool integration | 🚧 Distance ranking + schema + 1 real record shipped; scaling coverage needs more data from Aarush |
| 6 | Flowchart semester-by-semester view (toggle) | ✅ Done |

---

## 1. All PSU Majors

### User story

> As a Penn State student in **any** major — not just Computer Science or
> Premedicine — I want to tell the planner what I'm studying and get the same
> prerequisite-safe, graduation-guaranteed plan that CMPSC and Premed students
> get today, built from my actual college's real bulletin requirements.

### Research findings

Surveyed `bulletins.psu.edu/undergraduate/colleges/` — Penn State organizes
majors under ~13 academic colleges (plus branch campuses, which mostly offer
subsets of the same majors). A scripted crawl of each college page found:

| College | Majors found |
|---|---|
| Liberal Arts | 58 |
| Arts and Architecture | 21 |
| Engineering | 20 |
| Earth and Mineral Sciences | 17 |
| Eberly College of Science | 17 |
| Agricultural Sciences | 16 |
| Smeal Business | 10 |
| Education | 9 |
| Health and Human Development | 9 |
| Information Sciences and Technology | 8 |
| Bellisario Communications | 7 |
| Nursing | 1 |
| Intercollege | 1 (+ individualized B.Phil.) |
| **Total** | **~194** (a lower bound — some degree-suffix patterns like `-bsw`, `-bph`, `-bsba` weren't in the discovery regex; the real number is likely closer to PSU's publicly cited 275+ once minors/campus-specific variants are counted) |

Every major page follows the same URL shape:
`bulletins.psu.edu/undergraduate/colleges/{college-slug}/{major-slug}-{degree-suffix}/`
(`-bs`, `-ba`, `-bfa`, `-barch`, `-bdes`, `-bla`, `-bme`, `-bm`, `-bma`, `-bae`, `-bsn`, ...).

Crucially — confirmed while building Premedicine — **most non-Engineering majors
publish their own "Suggested Academic Plan" tab directly on the bulletin page**
(`#suggestedacademicplantextcontainer` in the DOM), a semester-by-semester course
table just like Engineering's PDF flowcharts, but as scrapeable HTML with no PDF
parsing required. Engineering majors are the outlier: they additionally publish a
polished PDF flowchart (`advising.engr.psu.edu/assets/flow-charts/{MAJOR}-{year}.pdf`)
that's nicer for the "unlock map" visuals, but the bulletin's own Suggested Academic
Plan tab exists for them too and is sufficient on its own.

### Architecture (already proven by CMPSC + Premed, no changes needed)

- One `Backend/degree_plans/{MAJOR}-{YEAR}.json` per major+catalog-year.
- One `Backend/catalogs/{DEPT}_catalog.json` per course-prefix department,
  shared across majors that reference the same department (e.g. MATH, PHYS
  are reused by both CMPSC and Premed already).
- `_MAJOR_ALIASES` in `app.py` maps spoken names ("computer science", "premed")
  → the plan's major code.
- `engine.list_degree_plans()` / the `/api/degree-plans` endpoint already scan
  the whole directory — **the frontend dropdown and chat detection need zero
  changes to support a new major**. Dropping in a new `{MAJOR}-{YEAR}.json` +
  its department catalogs is the entire integration surface.

This means the bottleneck isn't code — it's the **data-quality work**: building
each plan correctly from real bulletin data, the way Premed's build surfaced and
fixed three real scraper bugs (dropped single-digit codes, mis-classified
"prerequisite or concurrent" pairs, flattened "A and B" into an OR-group). Every
new major is a chance to hit a new bulletin-formatting edge case, so each one
needs the same rigor: build → simulate the full 4-year plan → assert 0 warnings
→ spot-check a few prereq chains against the live bulletin → regression test.

### Build pipeline (per major)

1. **Discover**: run a crawl script (`Backend/scripts/discover_majors.py`,
   to be written) against each college page, filtering the degree-suffix
   pattern above. Output a `Backend/degree_plans/_catalog_of_majors.json`
   index (name, slug, college, degree type) — this becomes the backlog.
2. **Scrape**: for a chosen major, fetch its bulletin page, extract:
   - Program Requirements tab (course list + credits + prereqs via the
     department catalogs, auto-scraped/cached the same way CMPSC's were)
   - Suggested Academic Plan tab (semester table)
3. **Structure**: convert the semester table into `degree_plans/{MAJOR}-{YEAR}.json`
   — this step still needs a human (or an LLM-assisted pass reviewed by a human)
   because course "options" (cross-listed alternates, legacy codes, "or
   equivalent" footnotes) require judgment, same as CMPSC's `CMPSC 315`/`CMPEN 315`
   cross-listing and Premed's `PHIL 432`/`BIOET 432` cross-listing.
4. **Verify**: add the major to `Backend/tests.py` — full-plan-to-graduation
   with 0 warnings, a couple of targeted prereq-ordering assertions (like the
   `PHYS 213`-after-`PHYS 211` regression test), major-alias detection.
5. **Commit** that one major. Repeat.

### Rollout order (proposed)

Prioritize by likely user demand and reuse of already-built department catalogs:

1. **Phase A — Eberly Science siblings** (reuses BIOL/CHEM/MATH/PHYS/STAT
   catalogs already built for Premed): Biology B.S., Biochemistry & Molecular
   Biology B.S., Chemistry B.S., Statistics B.S. — 4 majors, low marginal cost.
2. **Phase B — Engineering siblings** (reuses CMPSC/CMPEN/MATH/PHYS catalogs):
   Computer Engineering, Electrical Engineering, Mechanical Engineering,
   Civil Engineering — 4 majors.
3. **Phase C — high-enrollment Liberal Arts / Smeal**: Psychology, Economics,
   Political Science, Accounting, Finance, Marketing — 6 majors, new
   department catalogs (PSYCH already exists from Premed; ECON, PLSC, ACCTG,
   FIN, MKTG are new).
4. **Phase D — everything else**, backlog-driven, one college at a time.

This doc's status table gets a row added per major (or per phase) as they land.

---

## 2. Catalog-year back-referencing (2022–2026) — ✅ shipped

### User story

> As a student who started at Penn State in **2022**, **2023**, or **2024** —
> not this year — I want the planner to show me the graduation requirements
> that were actually in effect when I enrolled, since Penn State's own policy
> is that your requirements are locked to your enrollment year, not the
> current catalog.

### Research findings

`bulletins.psu.edu/undergraduate/archive/` maintains full historical bulletins
back to 2018-19, each at `bulletins.psu.edu/archive/{YEAR}-{YEAR+1}/undergraduate/...`
— **same page structure, same tab IDs** (`programrequirementstextcontainer`,
`suggestedacademicplantextcontainer`) as the current bulletin. Confirmed by
loading the 2022-23 archive of both Computer Science and Premedicine: identical
DOM shape, genuinely different (if often similar) requirements year to year.
Existing scraper code needs zero structural changes — only a parameterized
base URL.

One caveat: Engineering's PDF flowchart (`advising.engr.psu.edu/assets/flow-charts/`)
is **not** archived by year — it only ever reflects the current catalog. For
historical CMPSC years, the bulletin's own Suggested Academic Plan tab is the
correct (and only available) source — which is what the discovery above
already established as sufficient for every major anyway.

### Architecture (mechanism already existed, now confirmed sufficient)

`planner_engine.load_degree_plan(major, catalog_year)` already does exact-year
lookup with graceful fallback to the latest available year — this was built
generically from day one, so historical years just need historical JSON files
dropped into `degree_plans/`, no engine changes.

### What shipped

Built `CMPSC-2022.json` .. `CMPSC-2025.json` and `PREMED-2022.json` ..
`PREMED-2025.json` (8 new files, plus the existing `-2026.json` for each —
10 catalog-year files total) from the live archive. This surfaced real
curriculum history, not just cosmetic differences:

- **CMPSC**: stable 2022-23 through 2024-25 (one relabeling: "Foreign
  Language" → "World Language" in 2024-25, same courses), then a genuine
  curriculum overhaul for 2025-26 — `CMPSC 150N`, `222`, `315`/`316`, `320`
  added as requirements; `CMPEN 331`, `CMPSC 311`/`464`/`473` moved from
  fixed requirements into an expanded elective pool; the foreign/world
  language requirement dropped entirely. `CMPSC-2025.json` mirrors the
  current plan (`-2026.json`); `2022`/`2023`/`2024` share the pre-overhaul plan.
- **PREMED**: stable 2022-23/2023-24, stable 2024-25/2025-26, one substantial
  revision between those two periods — total credits dropped 126 → 120,
  `PHIL 432` went from a fixed course to a `PHIL`/`BIOET 432` elective
  (alongside `CAS 453`/`NURS 464`), a new 1-credit Healthcare Internship
  requirement appeared, the foreign-language requirement was replaced by a
  12-credit "Area of Concentration" requirement, and `PHYS 211-214` gained a
  `PHYS 250/251` alternate sequence. `PREMED-2024.json`/`2025.json` mirror
  the current plan (`-2026.json`); `2022`/`2023` share the pre-2024 plan.

Each of the 10 files independently passes the same rigor as the original
builds: `build_full_plan()` with 0 warnings and `goal.met = True` in exactly
8 simulated terms. Two catalog bugs were caught and fixed along the way
(same "exposed by real data" pattern as Premed's original build):

- `CHEM 110`'s "or placement beyond MATH 22" prerequisite was wrongly modeled
  as a strict prior-term prerequisite, needlessly pushing it a semester later
  than PSU's own suggested plan (which pairs it with `MATH 140` in term 1).
  Moved to `concurrent_groups` so a Calc-ready student can take them together.
- A UI bug, not a data bug: the "Started college" / "Graduate in" dropdowns
  in `chatbot.component.ts` were local-only signals with no path for the
  parent's backend-synced `state.startYear`/`gradYears` to flow back down —
  so a chat-corrected start year updated the actual plan (confirmed via the
  API) but the dropdown kept showing the old year. Added `activeStartYear`/
  `activeGradYears` inputs + sync effects, mirroring the pattern `activeMajor`
  already used.
- A related UX bug: the "Major & catalog year" dropdown was combining major
  and year into one dropdown value (`"CMPSC|2022"`), which — now that 5 years
  exist per major — would have shown 10 duplicate-major entries and
  conflicted with the separate "Started college" selector. Split into two
  clean, non-redundant controls: the major dropdown now lists one entry per
  major, and `catalog_year` is no longer sent from the frontend at all —
  `start_year` alone drives it, avoiding a second bug where a remembered
  `catalog_year` would go stale and out-rank future `start_year` changes.

---

## 3. Chat-based start-year override — ✅ shipped

### User story

> As a student filling out the chat, I want to be able to say "oh, I started
> school in 2022" mid-conversation and have the planner switch to 2022's
> requirements immediately, even though I never touched the "Started college"
> dropdown.

### What shipped

- `Backend/app.py`: `_extract_start_year_from_prompt()` — detects a start-verb
  (started/began/enrolled) + a college-word (college/school/university/psu/penn
  state) + a `20XX` year, all within the same clause (reusing the existing
  clause splitter). Requiring all three in one clause avoids false positives
  like "I started CMPSC 131 in Fall 2022" (no college-word present → ignored).
- The detected year **overrides both `start_year` and `catalog_year`**, even
  if the frontend had already synced a different value from a prior response —
  an explicit chat correction always wins.
- The chat reply now opens with a confirmation line: *"Got it — switched to
  the 2022 requirements..."*.
- `Frontend/src/app.component.ts`: the "Started college" / "Graduate in"
  dropdowns now sync from `plan.state.startYear` / `plan.state.gradYears` in
  the response, the same way the major dropdown already synced from
  `plan.state.dept` — so the UI reflects the correction, not just the
  underlying computation.
- Tests: `TestStartYearParsing` (unit-level phrase detection + the
  course-taking false-positive guard) and an API-level test confirming the
  override wins over a stale dropdown-supplied value.

---

## 4. General Education course fulfillment — ⛔ blocked, need more info

### User story (partial — to be completed with Aarush's input)

> As a student with open GEN ED slots in my plan, I want the planner to tell
> me which specific courses satisfy each requirement (GWS, GQ, GN, GS, GHW,
> GA, GH...) instead of just showing a generic "GEN ED (3 cr)" placeholder.

### What's needed before this can be scoped

Penn State's Gen Ed system has several dimensions this doc doesn't have
answers for yet — Aarush said he'll follow up:

- Should recommendations pull from PSU's full Gen Ed course list (thousands
  of course-GenEd mappings across every department), or a curated subset?
- Do we need the "Integrative Studies" combination rules (two courses from
  different Knowledge Domains that share a common theme)?
- Should this respect a student's *declared* Gen Ed focus/theme, or just
  surface anything that satisfies the raw letter-code requirement?
- Where does the GenEd-to-course mapping data live — is there a scrapeable
  PSU source, or does this need to be hand-curated?

Leaving this as a named, tracked requirement rather than guessing at scope.

---

## 5. Transfer Credit Tool integration — 🚧 in progress, blocked on sample data

### User story

> As a student who could take a course at a nearby community college over
> the summer instead of at Penn State, I want the planner to tell me which
> nearby community colleges have that course confirmed as PSU-transferable —
> ranked closest to furthest, or by which college covers the most of my
> remaining courses if I'm looking at several at once — so I can see whether
> it shortens my path to graduation.

### Scope, confirmed with Aarush (2026-07-18)

- **Geography**: Pennsylvania community colleges first; nationwide is a
  planned follow-up once the PA version is working.
- **"Closest" = zip code + straight-line distance.** No external geocoding
  API — a bundled coordinate table instead.
- **Data approach**: pre-built cached dataset (matches how department
  catalogs already work), refreshed periodically — but the refresh isn't on
  a flat calendar; it's prioritized by whichever cached course-acceptance is
  **closest to its expiry date**, per Aarush's spec.

### Research findings

`public.lionpath.psu.edu`'s Transfer Credit Tool (`PE_AD077`) is a stateful
PeopleSoft/Campus Solutions form — full-page POST-backs, no public JSON API.
Its course/institution autocomplete widgets resist automated browser
interaction unusually hard (confirmed across many approaches: direct clicks,
double-clicks, synthetic mousedown/mouseup/click, keyboard nav all failed to
register a selection, even though the dropdown itself renders and is
visible) — likely because the widget only responds to OS-level trusted
keyboard/mouse events, not synthetic ones, combined with a blur-closes-list
race that automated clicks kept losing. **A live per-request scraper against
this tool is not a reliable foundation** — it would be slow (multi-second
PeopleSoft page loads) and can break on any PSU portal update, on top of
being hard to drive at all. This reinforces the "pre-built cache" approach
being the right call, independent of the refresh-cadence request.

The tool does confirm: results carry effective-date ranges ("Multiple
evaluations may display for the same course with different effective
dates" — the "expiry date" Aarush described), and there's a reverse search
mode (pick an institution, see what transfers in) in addition to the
by-PSU-course mode.

Aarush provided the full PA institution list directly from the tool's
"I can't find my institution" autocomplete fallback — this gave canonical
LionPATH institution IDs for every PA school without needing to scrape them.

### What shipped so far

- **`Backend/data/pa_zip_coords.json`** — 1,798 Pennsylvania zip codes with
  real lat/lng, filtered from a public-domain US Census Gazetteer-derived
  dataset (the well-known `erichurst/7882666` gist, itself sourced from
  `census.gov/.../gazetteer-files`). No fabricated coordinates.
- **`Backend/data/pa_community_colleges.json`** — all 16 PA community
  colleges (the 14 traditional members plus the 2 newer regional ones:
  Erie County CC and Northern PA Regional College), each with its real
  LionPATH `institution_id` (from Aarush's list) and main-campus zip/lat/lng.
- **`Backend/transfer_credit.py`**:
  - `haversine_miles()` / `zip_to_coords()` / `nearest_colleges()` — real,
    working distance ranking. Sanity-checked: a University City Philadelphia
    zip correctly puts Community College of Philadelphia ~1.5 miles away.
  - `EquivalencyRecord` schema (course, institution, transfer course,
    credits, effective/expiry dates, scraped_at) — what the eventual scraper
    needs to produce, agreed even though the scraper itself isn't built.
  - `soonest_expiring()` — the expiry-prioritized refresh-scheduling logic
    Aarush asked for, built and tested against synthetic data so it's ready
    the moment real scraped records exist.
  - `rank_colleges_for_courses()` — the "consolidate and recommend the
    college with the most transfer credits offered, ties broken by
    distance" logic from the original spec.
- **`POST /api/transfer-credit`** — live endpoint (zip code + course list in,
  ranked colleges out). Since the equivalency cache is still empty, every
  response currently returns `courses_covered_count: 0` for all colleges
  and an explicit `note` saying so — but the distance ranking itself is real
  and usable today, and the response shape won't change once equivalency
  data lands.
- Tests: `TestTransferCredit` (distance math, zip lookup incl. the
  out-of-PA-scope case, ranking sort order, coverage-beats-distance
  priority, expiry sorting) + 3 API-shape tests — 49 backend tests total,
  was 46.

### First real record seeded (2026-07-18)

Aarush sent a real PDF export — Delaware County CCC, PSU course ENGL 15.
Confirmed the exact results-table shape:

```
Delaware County Community College: 100123622
  Transfer:  ENG - ENGLISH  100  "ENGLISH COMPOSITION I"          3 units
  PSU:       016510  ENGL - English  15  "Rhetoric and Comp"       3 units
             Effective Dates: 01/01/2000 - 09/03/2027
```

Mapped 1:1 onto `EquivalencyRecord` (added `psu_course_id` — PSU's internal
numeric catalog ID, e.g. `016510` — as an optional traceability field the
PDF happened to include) and seeded it into
`Backend/data/transfer_equivalencies.json`. Verified end-to-end through the
real `/api/transfer-credit` endpoint: for a Philadelphia zip, Delaware
County CCC now correctly ranks **first** (course coverage) even though
Community College of Philadelphia is physically closer — and its
`2027-09-03` expiry is a genuine near-term case (~14 months out) that
`soonest_expiring()` correctly surfaces, a real exercise of the
refresh-priority logic rather than just a synthetic-data test.

### Still blocked — scaling beyond one record

One (course, institution) pair doesn't establish PA-wide coverage. Open
question for Aarush: was this PDF from a **"Show all institutions"** search
for ENGL 15 (i.e. Delaware County CCC might be the *only* PA school with a
confirmed ENGL 15 equivalency), or a **single-institution** search scoped to
Delaware County CCC specifically (i.e. there could be more matches at other
schools not shown here)? That determines whether the ~140-institution list
Aarush pasted earlier means "these are known to transfer ENGL 15" or just
"these are the institutions the tool recognizes." Either way, scaling to
real PA-wide coverage needs either more PDF samples or a working "show all"
export — one PDF per institution won't scale to the ~16 PA community
colleges × however many gen-ed/major courses this needs to cover.

---

## 6. Flowchart semester-by-semester view (toggle) — ✅ shipped

### User story

> As a student looking at my Path to Graduation, I want a second view — a
> true semester-by-semester flowchart, color-coded green for courses I've
> completed, red for what I need to take next, and grey for everything else
> still ahead — with arrows showing the prerequisite chain between them, and
> a toggle to switch back to the current card-based view.

### Design

- New Mermaid-based visualization, deterministic (same philosophy as the
  existing unlock map and progress flowchart — no LLM in the rendering path).
- Layout: one subgraph per semester (left to right, matching the degree
  plan's semester order), courses as nodes within their semester's subgraph,
  arrows drawn from each course to whichever later course lists it as a
  prerequisite (reusing the prereq-group data already loaded per catalog).
- Color coding via Mermaid `classDef`, matching the color language already
  established by the unlock map (`Backend/planner_engine.py:build_unlock_map`):
  - **Green** — completed courses (student's `completed` set)
  - **Red** — courses recommended for the *next* semester specifically
    (`recommend_semester()`'s output — the "need to take now" set)
  - **Grey** — everything else in the plan, still ahead
- Toggle lives in `Frontend/src/components/recommendations/` (or promoted to
  the Flowchart component, next to the existing Mermaid unlock map) — a
  simple two-state switch between the current recommendation cards and this
  new semester grid, no new backend endpoint needed if the semester-flowchart
  Mermaid string is added as a new field on the existing `/api/plan` response
  (alongside `unlockMap`, following the same pattern).

### Backend work

- `planner_engine.build_semester_flowchart(plan, catalog, completed, next_sem_courses)`
  — new function alongside `build_unlock_map`/`build_mermaid`, reusing their
  helpers (`_iter_plan_items`, `_mmd_id`, prereq-group lookups).
- Wire into `app.py`'s `/api/plan` response as `coursePlan.semesterFlowchart`.

### Frontend work

- `FullPlan`/`CoursePlan` model: add `semesterFlowchart?: LlmFlowchart` (reuse
  the existing `{mermaid, explanation}` shape).
- Toggle UI (two tabs or a switch) in the flowchart/recommendations panel.
- Render via the same Mermaid pipeline already used for the unlock map — no
  new rendering code needed, just a second `<div>` target and a visibility
  toggle bound to a signal.

### What shipped

Built essentially as designed, with the design's "green for completed" caveat
resolved the same way `build_unlock_map` already resolves it: completed
courses don't carry a "which semester was this taken in" timestamp (chat/chip
input only records that a course is done, not when), so they render as one
"Completed" subgraph rather than being split across historical per-semester
subgraphs. The FUTURE path — the actual new capability — is grouped by real
simulated term (`build_full_plan()`'s terms, not the plan JSON's nominal
semester numbers, so it reflects actual credit-cap-aware scheduling): the
very next term is red, everything after is grey.

- `planner_engine.build_semester_flowchart(catalog, completed, full_plan_terms)`
  — takes `full_plan["terms"]` directly (richer and more accurate than the
  original plan's signature, since it reflects the real simulated schedule
  including summer terms and credit-cap deferrals). One Mermaid `subgraph`
  per term; edges are real prereq/concurrent links between any two shown
  course nodes, colored via `linkStyle` to match their source node's tier
  (so a green course's outgoing arrow is green, a red course's is red, etc.
  — the "arrows along with it" behavior from the original request).
- Wired into `/api/plan` as `coursePlan.semesterFlowchart`, alongside
  `unlockMap`.
- Frontend: `pathView` signal (`'cards' | 'flowchart'`) in
  `FlowchartComponent`, a two-button toggle in the "Path to Graduation"
  section header, and a third Mermaid host/effect/error-signal trio
  mirroring the existing `mermaidHost`/`unlockHost` pattern exactly. Verified
  end-to-end in-browser: correct term grouping, and computed SVG fill colors
  confirmed pixel-exact (`#dcfce7` green / `#fee2e2` red) against the
  `classDef` values.
- Regression tests: `TestSemesterFlowchart` (valid shape, color-class
  assignment, edge-count-matches-linkstyle-count, empty state) — 39 tests
  total, was 36.

---

## Execution log

Append one line per shipped checkpoint, newest first.

- 2026-07-17 — Shipped catalog-year back-referencing (§2): 8 new historical
  degree plan files (CMPSC/PREMED × 2022-2025), 2 real catalog bugs found and
  fixed, 2 frontend UX bugs found and fixed (dropdown desync, sticky
  catalog_year). 36 backend tests passing (was 30).
- 2026-07-17 — Shipped chat-based start-year override (§3) + wrote this plan.
- 2026-07-17 — Shipped the semester-by-semester flowchart toggle (§6):
  `build_semester_flowchart()`, Cards/Flowchart toggle in the "Path to
  Graduation" panel, verified in-browser with pixel-exact color assertions.
  39 backend tests passing (was 36).
- 2026-07-18 — Shipped the Transfer Credit foundation (§5): PA zip/college
  distance ranking on real Census-derived coordinates, the equivalency-cache
  schema + expiry-prioritized refresh logic, and a live `/api/transfer-credit`
  endpoint (distance-only until real equivalency data lands — LionPATH's
  Transfer Credit Tool resisted automated scraping across many approaches;
  still need a real results sample from Aarush to populate the cache).
  49 backend tests passing (was 39).
- 2026-07-18 — Seeded the first real equivalency record (§5) from Aarush's
  PDF export (Delaware County CCC ENG 100 -> PSU ENGL 15, confirmed the
  results-table schema), verified it correctly outranks a closer non-covering
  college end-to-end, and confirmed the expiry-refresh logic against its real
  2027-09-03 expiry date. 51 backend tests passing (was 49). Blocked on
  clarifying whether that PDF was a "show all institutions" result or
  single-institution, to know how to scale coverage.
- 2026-07-18 — Transfer Credit bookmarked pending Aarush's next PDF/screenshot.
  Audited Premedicine for feature parity with CMPSC per Aarush's request: the
  one real gap found was the semester-flowchart toggle (§6, built after
  Premed's initial setup) had no Premed-specific test — verified it works
  correctly (correct color classes, correct term grouping) and added the
  missing test. Also re-verified live in-browser: chat-based major detection,
  the semester-flowchart toggle's colors (pixel-exact, same as CMPSC), and
  the chat-based start-year override all confirmed working for PREMED,
  including switching to the pre-2024 PREMED-2023 plan. Everything else
  (catalog years, catalogs, RAG index, major aliases) was already at parity
  from Premed's original build. 52 backend tests passing (was 51).
