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
| 5 | Transfer Credit Tool integration | ⛔ Blocked — needs more info from Aarush |
| 6 | Flowchart semester-by-semester view (toggle) | 📝 Planned |

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

## 5. Transfer Credit Tool integration — ⛔ blocked, need more info

### User story (partial — to be completed with Aarray's input)

> As a student who could take a course at a community college or another
> university over the summer, I want the planner to tell me whether that
> course would transfer to Penn State and satisfy a specific requirement, so
> I can see whether it shortens my path to graduation.

### What's needed before this can be scoped

Aarush flagged this is "a major part in displaying to a student on if they
can complete their major in less than 4 years" but said there's more detail
coming. Open questions once that arrives:

- Is there an API for PSU's Transfer Credit Tool (transfercredit.psu.edu), or
  does this require scraping its course-equivalency search UI?
- Scope: any accredited institution, or a specific shortlist (e.g. local
  community colleges)?
- How should a transfer-equivalent course interact with the deterministic
  engine's prereq/eligibility model — does it get treated as satisfying a
  specific PSU course code (like an alternate "option"), or as a separate
  slot type?

---

## 6. Flowchart semester-by-semester view (toggle)

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

---

## Execution log

Append one line per shipped checkpoint, newest first.

- 2026-07-17 — Shipped catalog-year back-referencing (§2): 8 new historical
  degree plan files (CMPSC/PREMED × 2022-2025), 2 real catalog bugs found and
  fixed, 2 frontend UX bugs found and fixed (dropdown desync, sticky
  catalog_year). 36 backend tests passing (was 30).
- 2026-07-17 — Shipped chat-based start-year override (§3) + wrote this plan.
