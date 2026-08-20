# Course Planner Expansion Plan

Status tracker + technical design for scaling the planner beyond CMPSC/Premedicine,
adding historical catalog years, and a new flowchart view. Written 2026-07-17.

Update the **Status** column as work lands. Each ✅ row should correspond to a real
git commit — that's the checkpoint discipline this plan is built around.

## Status at a glance

| # | Feature | Status |
|---|---|---|
| 1 | All PSU majors — discovery + build pipeline | 🚧 In progress; 161 of ~194 majors built |
| 2 | Catalog-year back-referencing (2022–2026) | ✅ Done — all 18 majors, all 5 years (87 plan files) |
| 3 | Chat-based start-year override | ✅ Done |
| 4 | Gen Ed fulfillment guidance | ✅ Done — real course recommendations across all 10 domains, Firewall rule enforced |
| 5 | Transfer Credit Tool integration | 🚧 Distance ranking + schema + 1 real record shipped; scaling coverage needs more data from Aarush |
| 6 | Flowchart semester-by-semester view (toggle) | ✅ Done |
| 7 | Minors + double major (`merge_plans`) | 🚧 Mechanism shipped; 81 real minors built (STATMIN, CPTSC, INTLBUS, PSYCH, ECON, CAS, MATHMIN, CMPENMIN, CYBERCF, ISTMIN, AIENG, ENTI, LHR, LDEV, ISM, LEBUS, CHEMMIN, BIOLMIN, PHYSMIN, ASTROMIN, GEOSCMIN, HISTMIN, PHILMIN, SOCMIN, PLSCMIN, ARTHMIN, ENGLMIN, SPANMIN, FRMIN, GERMIN, JOURNMIN, THEAMIN, ANTHMIN, KINESMIN, MUSTECHMIN, NUTRMIN, JAPNSMIN, KORMIN, CHNSMIN, GEOGMIN, SRAMIN, SGSMIN, LINGMIN, AFAMMIN, MEDIAMIN, JSTMIN, LEGSTMIN, HPAMIN, CAMSMIN, GDMIN, SCISTMIN, HDFSMIN, WFSMIN, NUCEMIN, WLITMIN, POLPOLMIN, ERMMIN, ANSCMIN, EBFMIN, AGBMMIN, MATSCIMIN, PSAMIN, PHOTOMIN, LARCHMIN, MUSPERFMIN, HORTMIN, MICRBMIN, RPTMMIN, FLMSMIN, CSJMIN, BEMIN, RHSMIN, SPLEDMIN, FORMIN, WWRMIN, REBPMIN, ENGYMIN, ENVSYSMIN, MINEMIN, PNGMIN, EASYSMIN) |
| 8 | Campus/location filtering | ✅ Mechanism done; only University Park has real plan data — branch-campus data deferred |
| 9 | Chat panel redesign: multi-major picker, restyled minors, X close | ✅ Done |

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
   catalogs already built for Premed): Biology B.S. ✅, Biochemistry & Molecular
   Biology B.S. ✅, Chemistry B.S. ✅, Statistics B.S. ✅ — done (2026-08-11).
2. **Phase B — Engineering siblings** — ✅ done (2026-08-11): Computer
   Engineering, Electrical Engineering, Mechanical Engineering, Civil
   Engineering. Each needed its own fresh department catalog (EE, ME, CE,
   plus EMCH/MATSE/IE/GEOSC/EDSGN as supporting departments).
3. **Phase C — high-enrollment Liberal Arts / Smeal**: Economics ✅,
   Political Science ✅ (both 2026-08-11) — Accounting, Finance, and
   Marketing were already covered by the Smeal batch. Psychology is
   blocked, not built — see [BLOCKED_MAJORS.md](BLOCKED_MAJORS.md).
4. **Phase D — everything else**, backlog-driven, one college at a time.
   First batch (2026-08-11): Industrial Engineering ✅, Physics ✅,
   Microbiology ✅, Biotechnology ✅, Chemical Engineering ✅ — picked for
   catalog reuse (`IE` from the earlier ME build, `PHYS` from Premed/CMPSC,
   `MICRB` from the BMB build) plus two majors needing fresh catalogs
   (`CHE`, `BIOTC`/`PPEM`/`GEOSC`-adjacent).
   Second batch (2026-08-11): Aerospace Engineering ✅, Biomedical
   Engineering ✅, Nuclear Engineering ✅, Astronomy and Astrophysics ✅,
   Forensic Science ✅ — needed 6 more fresh department catalogs (`AERSP`,
   `BME`, `NUCE`, `ASTRO`, `FRNSC`, `CRIM`).
   Third batch (2026-08-11): Biological Engineering ✅, Neurobiology ✅,
   Planetary Science and Astronomy ✅, Engineering Science ✅, Data
   Sciences ✅ — Environmental Engineering was attempted but blocked (see
   [BLOCKED_MAJORS.md](BLOCKED_MAJORS.md)); Data Sciences substituted in to
   keep the batch at 5. Needed 5 more fresh department catalogs (`BE`,
   `EARTH`, `GEOG`, `METEO`, `ESC`, `DS` — six, not five, since Planetary
   Science needed three on its own).
   Fourth batch (2026-08-11): Surveying Engineering ✅, Electro-Mechanical
   Engineering Technology ✅, Integrative Science ✅, Electrical Engineering
   Technology ✅ — this **closes out both the Engineering and Eberly
   Science colleges** from the original discovery table. Opened a new
   college, **Earth and Mineral Sciences** (13 majors), with Meteorology
   and Atmospheric Science ✅ as its first major, reusing the `METEO`
   catalog already scraped for Planetary Science.
   Fifth batch (2026-08-11): Geosciences ✅, Geography ✅, Energy
   Engineering ✅, Materials Science and Engineering ✅, Earth Sciences ✅.
   Sixth batch (2026-08-11): Geobiology ✅, Mining Engineering ✅,
   Petroleum and Natural Gas Engineering ✅, Environmental Systems
   Engineering ✅, Energy Business and Finance ✅ — **completes the College
   of Earth and Mineral Sciences** except Earth Science and Policy and
   Energy and Sustainability Policy (both policy-focused majors, likely
   sharing heavy overlap with Earth Sciences/Energy Business — not yet
   attempted).

This doc's status table gets a row added per major (or per phase) as they land.

### What shipped so far

Six majors built (2026-07-26), driven directly by Aarush's request rather than
the proposed rollout order above — "the IT field, then business, math, english,
science, and medical" — each carried through the full build pipeline (scrape →
structure → simulate to 0 warnings in exactly 8 terms → regression test →
commit):

- **`NURS-2026.json`** — Nursing, B.S.N., General Nursing option, University
  Park. Surfaced and fixed a real scraper bug: `_BOUNDARY_RE` in
  `Courseplanner.py` didn't recognize "Recommended Corequisite:" or bare
  (no-colon) "enforced concurrent" label variants, so those clauses' scope
  bled into the enforced-prereq parse for `NURS 301`/`230`/`480`.
- **`ENGL-2026.json`** — English, B.A., Traditions of Innovation option,
  Liberal Arts. Era-based "Concentration Course" requirements and the World
  Language sequence have no fixed PSU course codes, so they're modeled as
  slots — same convention as CMPSC's open elective pools.
- **`BUSINESS-2026.json`** — Business, B.S. (Intercollege), Accounting option
  — chosen per Aarush's clarification ("do all the courses that fall under
  the 'business' major") since Smeal has no single generic business major.
  Notably has **no University Park offering** (Commonwealth/World Campus
  only) — the first major in the planner where that's true.
- **`CYBER-2026.json`** — Cybersecurity Analytics and Operations, B.S.,
  University Park — substituted for the general "Information Sciences and
  Technology, B.S." (which turned out to have no on-campus Suggested
  Academic Plan) as the "IT field" major. Surfaced a real
  `planner_engine.py` bug: when two plan items share an overlapping option
  pool (e.g. two "ENGL 15 or CAS 100A/B" writing boxes), the engine always
  recommended the same first-listed option for both, so the second item
  could never be marked done and the simulation looped for the full 24-term
  cap without finishing. Fixed generally (`_pick_option`/`_ranked_options`
  now de-prioritize already-completed/already-picked options) rather than by
  reordering the JSON, so it can't recur silently in a future major.
- **`MATH-2026.json`** — Mathematics, B.S., General Mathematics option,
  University Park, standard MATH 140 start. Needed zero new department
  scraping (MATH/STAT/CMPSC/ENGL/ESL/CAS all already cached). Surfaced a
  placement-gate prereq bug — `CMPSC 101`/`121` (two of five equivalent
  intro-programming options) enforce `MATH 21`/`MATH 110` prereqs that are
  really placement thresholds below Calc I, already cleared by any
  MATH-140-track student — same pattern patched for `CHEM 110/130`,
  `STAT 200/250`, and `MATH 21/110`'s own prereqs while building
  Nursing/Business/Cyber.
- **`BIOL-2026.json`** — Biology, B.S., General Biology option, University
  Park, standard MATH 140 start (represents the "science" request). Also
  needed zero new scraping. The bulletin's own suggested plan lists its
  18-credit "400-level biology, ≥3 from each of 6 groups" requirement
  generically as "BIOL 4XX" (each group has 20-30 alternative courses), so
  this plan does the same, modeling the 6 groups as slots.

All six pass `build_full_plan()` with 0 warnings and `goal.met = True` in
exactly 8 simulated terms, matching each major's real bulletin-suggested
timeline. Backend test count: 52 → 69 (one dedicated test class per major,
plus `TestPlanEngineRobustness` — a plan-agnostic regression test guarding
the duplicate-option infinite-loop fix at the engine level, independent of
any single major's data).

### Smeal College of Business — all 10 majors (2026-07-26)

Aarush asked for "everything that falls under the Business branch —
Accounting, finance, supply chain, ETC" — distinct from the generic
Intercollege `BUSINESS` major built earlier (which has no University Park
offering and exists only as a Commonwealth/World Campus curriculum). Smeal
itself has 10 specific majors, every one with a real University Park
Suggested Academic Plan; all 10 were built:

`ACCTG` (Accounting), `FIN` (Finance), `SCM` (Supply Chain and Information
Systems), `MKTG` (Marketing), `MGMT` (Management), `ACTSC` (Actuarial
Science), `BAIS` (Business Analytics and Information Systems), `CIE`
(Corporate Innovation and Entrepreneurship), `REST` (Real Estate), `RM`
(Risk Management — Enterprise Risk Management option; the major's other
option, Real Estate, has no University Park Suggested Academic Plan on the
bulletin, so the standalone `REST` major covers that curriculum instead).

**Shared structure discovered and reused across all 10**: every Smeal major
(except Actuarial Science) has an identical First/Second Year "Smeal core"
— PSU 006 seminar, MATH 110/140, the GWS writing course, ECON 102/104, a
3-course World Language ramp, MGMT 301, ACCTG 211, MKTG 301, FIN 301, SCM
301, MIS 250 — all flagged as Smeal's own "Entrance-to-Major" requirements.
Once verified working for Accounting, the same 4-semester JSON block was
reused verbatim for Finance/Supply Chain/Marketing/Management/Business
Analytics/Corporate Innovation/Real Estate/Risk Management, with only the
Third/Fourth Year (the actual major-specific courses) built fresh each
time. Actuarial Science is the one outlier — it starts directly with
MATH 140/141 (Calc I/II) instead of MATH 110, since its curriculum needs
real calculus from term one.

**Two new department catalogs scraped**: `BLAW` (Business Law) and `RM`
(Risk Management) — needed by nearly every Smeal major (`BLAW 341`/`BA 342`
is a recurring either-order pair) and by Actuarial Science/Real
Estate/Risk Management specifically.

**A fifth instance of the placement-gate prereq pattern**: `ACCTG 211` and
`SCM 200` both enforce a literal `MATH 21` prereq (PSU's placement
threshold, not a completable course) — patched to accept `MATH 110`/`140`
as alternatives, same fix applied four times already across CMPSC/CHEM/
STAT/MATH.

**A real chat-detection bug, not a data bug**: `_extract_major_from_prompt`
picks whichever alias matches earliest in the message — but when two
aliases match at the *same* start position (the generic `"BUSINESS"` and
the new, more specific `"BUSINESS ANALYTICS"`, both matching the word
"business" in "I am a business analytics major"), the old tie-break kept
whichever alias happened to be inserted into the `_MAJOR_ALIASES` dict
first, not the more specific one — so a Business Analytics student would
have silently been routed to the generic Intercollege Business plan. Fixed
in `app.py` so ties on start position go to the *longer* alias.

**The option-deduplication engine fix earns its keep on real data**: several
of these majors' own bulletin-suggested plans list the identical "X (or
elective)" slot in two or more terms (Accounting's `ACCTG 403W`/`BA 411`,
Risk Management's whole elective pool repeated across two slots) — every
one of these now resolves correctly to distinct courses without any
data-level reordering workaround, confirming the general fix from the
Cybersecurity/Mathematics builds holds up under real, messier bulletin data.

All 10 pass with 0 warnings and `goal.met = True` in exactly 8 terms.
Backend test count: 69 → 98 (one dedicated test class per major, each with
at least one real prereq-chain-ordering assertion beyond the basic
graduation check).

### Phase A — Eberly College of Science siblings (2026-08-11)

The three majors proposed in the rollout order above that share BIOL's
department catalogs: Biochemistry and Molecular Biology B.S., Chemistry
B.S., Statistics B.S. Checked every course each needed against the catalogs
already scraped for BIOL/PREMED/CMPSC/MATH before starting — all of them
(`bmb`, `chem`, `math`, `stat`, `micrb`, `phys`, `cmpsc`) already had every
required course cached, so this phase needed **zero new department
scraping**, the cheapest of any major batch so far.

- **`BMB-2026.json`** — Biochemistry option (the bulletin's other option,
  Molecular and Cell Biology, overlaps heavily with the existing BIOL major
  and wasn't built separately). The bulletin's own "Requirements for the
  Major" table and its Suggested Academic Plan disagree on how the lab
  sequence (BMB 442/443W/445W/448) is grouped, and the plan's own listed
  semester credit totals don't even sum correctly — built against the
  cleaner Requirements table instead of trying to reconcile the
  inconsistent suggested-plan text.
- **`CHEM-2026.json`** — Analytical/Environmental-Focused option (of four
  options sharing the same 15-credit 400-level pool + 4-credit advanced
  lab, modeled generically like BIOL's 400-level elective groups).
- **`STAT-2026.json`** — Statistics and Computing option — the general/
  data-science track, picked over the bulletin's Actuarial Statistics
  option since that curriculum is already covered by the existing ACTSC
  major. Surfaced a real placement-gate case: `STAT 184`'s `MATH 21`
  prerequisite is PSU's placement threshold, not a completable course (the
  same pattern hit five times before across CMPSC/CHEM/ACCTG/SCM/MATH) —
  but this time the fix needed to go further than just adding `MATH 110`/
  `140` as alternates: the bulletin's own plan takes `STAT 184` in the
  *same* term as `MATH 140`, which a strict prerequisite can never satisfy.
  Moved it to `concurrent_groups` in `stat_catalog.json` instead, the same
  mechanism already used to fix `CHEM 110`'s `MATH 140` concurrency during
  the original catalog-year work (§2).

All three pass `build_full_plan()` with 0 warnings and `goal.met = True` in
exactly 8 terms on the first simulation — no data bugs needed a second pass,
likely because their department catalogs were already battle-tested by
Premed/BIOL/CMPSC's own builds. Backend test count: 105 → 114 (one
dedicated test class per major, each with a real prereq-chain-ordering
assertion — including a regression test locking in the `STAT 184`/
`MATH 140` concurrency fix).

### Phase B — Engineering siblings (2026-08-11) — ✅ done

- **`CMPEN-2026.json`** — Computer Engineering B.S., University Park.
  First major since Phase A to need a **brand-new** department catalog —
  `EE` wasn't cached by any prior major, so this confirmed
  `load_merged_catalog()`'s auto-scrape-and-cache path actually works
  end-to-end (90 EE courses scraped live from the bulletin on first load).
  The 12 credits of open CMPEN/CMPSC 400-level electives and two
  "Department List" general-elective slots are modeled generically, same
  convention as CMPSC's own open pools. Finishes in 7 simulated terms, not
  8 — legitimate tight-packing near the 18cr/term cap (same pattern as
  ENGL's 7-term result), not a bug.
- **`EE-2026.json`** — Electrical Engineering B.S., University Park. Reused
  the `EE` catalog CMPEN had just scraped. The 18 credits of open EE/CMPEN
  elective + "Related"/"Statistics" pools are modeled generically.
- **`ME-2026.json`** — Mechanical Engineering B.S., University Park
  (Suggested Academic Plan for last names A-K; the bulletin has a
  credit-equivalent L-Z track, not modeled separately). Needed three new
  department catalogs (`EMCH`, `MATSE`, `IE`). The bulletin's listed
  capstone alternate, `ME 441W`, doesn't exist in the current department
  catalog — the plan lists `ME 440W` only.
- **`CE-2026.json`** — Civil Engineering B.S., University Park. Needed two
  new department catalogs (`CE`, `GEOSC`). The bulletin's "Requirements for
  the Major" table lists a `CE 337`-or-`CE 475` pick as one item, but the
  Suggested Academic Plan separately shows an unnamed "CE Capstone Design"
  course in the final semester — modeled as concrete `CE 337` for the
  first (a real, named 1-credit course) and a generic "CE 400-Level
  Capstone Design (W)" slot for the second, since the bulletin never names
  that actual course code.

All four passed `build_full_plan()` at 0 warnings / `goal.met = True` on
the first simulation — every department catalog these four needed (`EE`,
`EMCH`, `MATSE`, `IE`, `CE`, `GEOSC`, `EDSGN`) scraped live from the
bulletin successfully, confirming the auto-scrape path holds up under real
volume (8 new catalogs in one batch), not just the single-department case
CMPEN exercised first.

### Phase C — Economics and Political Science (2026-08-11)

- **`ECON-2026.json`** — Economics B.S., College of the Liberal Arts.
  `ECON 106`'s `MATH 21` prerequisite is PSU's placement threshold, not a
  completable course — the same pattern hit six times before, patched in
  `econ_catalog.json` to also accept `MATH 110`/`140`. Surfaced a real
  **engine bug**, not just a data one: this major's own bulletin plan lets
  a student satisfy their calculus requirement with `MATH 110` (a
  Liberal-Arts-track "Techniques of Calculus" course) instead of `MATH
  140` — but `CMPSC 101`'s prerequisite specifically requires `MATH 140`/
  `141` (not `MATH 110`), so a `MATH 110` student can never make `CMPSC
  101` eligible. The item was written as "`CMPSC 101` (or `203`)" expecting
  the engine to fall back to `CMPSC 203` (no prerequisite at all) — but
  `recommend_semester()`'s scheduler only ever evaluated an item's
  **first-ranked** option; if that one was prerequisite-blocked, it skipped
  the whole item every scan instead of trying the second option, leaving it
  permanently unscheduled. Fixed generically in `planner_engine.py`: the
  scheduler now walks every ranked option for an item and takes the first
  one that's actually eligible, not just the first one listed. This is a
  plan-agnostic engine fix, not an ECON-specific workaround — regression
  test in `TestPlanEngineRobustness`.
- **`PLSC-2026.json`** — Political Science B.S., College of the Liberal
  Arts. Unlike every major built so far, the bulletin's own Suggested
  Academic Plan uses generic placeholders for most major-specific courses
  ("400-level PLSC," "Related course in consultation with adviser")
  instead of concrete codes. Judged this **not** the same kind of blocker
  Psychology hit: PLSC still has real, named prescribed courses (`PLSC
  10`, `309`, `308`, a real 5-course introductory pool) and the vague parts
  are large, genuinely open pools *within* one department — the same shape
  as BIOL's 400-level elective groups, which are already modeled as
  labeled slots successfully. Psychology's blocker was different in kind:
  it didn't even specify which of 5 Options to build. Built PLSC with
  generic slots for the open pools, following that established convention.
  Also caught a real gap in this plan's own first draft during
  verification: it never included the `ENGL 15` writing prerequisite that
  `ENGL 202A` (used later in the plan) needs — every other major has this
  in Semester 1, this one was simply missed when transcribing the
  bulletin's non-standard table layout.

Both passed at 0 warnings / `goal.met = True` after the engine fix and the
`ENGL 15` gap were caught and corrected — neither was committed with a
warning outstanding. 118 → 134 backend tests (16 new: 3 tests × 5 majors,
plus 1 engine-level regression test for the option-fallback fix).

### Phase D, first batch (2026-08-11)

With the proposed rollout order's named phases exhausted (Psychology
excepted), picked the next 5 by the same catalog-reuse-first logic the
whole rollout order has followed:

- **`IE-2026.json`** — Industrial Engineering B.S., General option,
  University Park. Reused the `IE` catalog already scraped for Mechanical
  Engineering's `IE 312` dependency. The bulletin's own suggested plan
  lists `IE 470` in the final semester, but its `concurrent_groups`
  requires `IE 306`/`307`/`311`/`428` — none of which appear anywhere else
  in the bulletin's plan, and their relationship to the plan's separate
  "Human Factors Elective" slot is unclear — so `IE 470` was dropped for a
  generic elective slot rather than guessing at that link.
- **`PHYS-2026.json`** — Physics B.S., General option, University Park (of
  five options — General, Medical, Electronics, Computation, Nanotechnology
  /Materials — all sharing this common core). Reused `PHYS`/`MATH`
  catalogs from Premed/CMPSC/CMPEN. Finishes in 7 simulated terms, not 8 —
  the same legitimate tight-packing pattern as ENGL and CMPEN.
- **`MICRB-2026.json`** — Microbiology B.S., General Microbiology option
  (Cell Biology & Genetics emphasis), University Park. Reused `MICRB`/
  `BMB`/`CHEM` catalogs from the BMB build; needed one new department
  (`PPEM`, for `PPEM 456`).
- **`BIOTECH-2026.json`** — Biotechnology B.S., General option, University
  Park. Needed one new department catalog (`BIOTC`). Uses the standard
  `CHEM 110/112/210/212/213` organic-chemistry path rather than the
  bulletin's alternate `CHEM 202/203` sequence, matching every other
  Eberly Science major built so far.
- **`CHE-2026.json`** — Chemical Engineering B.S., University Park. Needed
  one new department catalog (`CHE`, distinct from `CHEM`).

All five passed `build_full_plan()` at 0 warnings / `goal.met = True` on
the first simulation — no data bugs this round, likely because every
department catalog they needed had already been scraped and battle-tested
by an earlier major, or scraped cleanly fresh (`PPEM`, `BIOTC`, `CHE`).
134 → 149 backend tests (3 tests × 5 majors).

### Phase D, second batch (2026-08-11)

- **`AERSP-2026.json`** — Aerospace Engineering B.S., University Park.
  Needed a new `AERSP` catalog. The bulletin's design-sequence choice
  (`401A`/`401B` or `402A`/`402B`) and capstone-adjacent choice (`413` or
  `450`) are each two separate plan items — listed the same preferred
  option first in both halves of the design-sequence pair so the engine's
  option-fallback logic can't mix a `401A` pick with a `402B` follow-up.
- **`BME-2026.json`** — Biomedical Engineering B.S., Biomechanics option,
  University Park. Needed a new `BME` catalog.
- **`NUCE-2026.json`** — Nuclear Engineering B.S., University Park. Needed
  a new `NUCE` catalog. `EMCH 316` needs `EMCH 315` as a strict prior-term
  prerequisite (not concurrent, despite the bulletin's suggested plan
  listing them in the same semester) — scheduled a full term later.
- **`ASTRO-2026.json`** — Astronomy and Astrophysics B.S., Computer Science
  option, University Park. Needed a new `ASTRO` catalog. Two bulletin-named
  courses, `ASTRO 320` and `CMPSC 202`, don't exist in the current
  department catalogs (only `ASTRO 320W` does) — used the real code and
  dropped the nonexistent alternate.
- **`FRNSC-2026.json`** — Forensic Science B.S., Forensic Molecular Biology
  option, University Park. Needed new `FRNSC` and `CRIM` catalogs. Surfaced
  a real **engine-level bug**, not a data-only one: `BIOL 234`/`235W`
  (lecture/lab) were scraped as *bidirectional* concurrent requirements —
  each course listing the other as something it needs same-term. That
  deadlocks the scheduler completely: evaluating either course first always
  finds the other not yet picked, so scan_once() can never resolve the
  pair, no matter how many times it re-scans the term. Every other
  concurrent pair in this codebase (`CHEM 110`/`111`, `STAT 184`/
  `MATH 140`) is one-directional, which is why this hadn't surfaced before.
  Fixed the data in `biol_catalog.json` to match that established
  direction: only the lab requires the lecture concurrently, not the
  reverse. Also found a real prereq gap: the bulletin's `BIOL 222`-or-`322`
  requirement needs `BIOL 110`/`141` or `BMB 251`/`MICRB 201` — none of
  which are otherwise part of this plan's own core sequence (FRNSC uses a
  different `BIOL 114`/`115`/`234`/`235W` intro track) — modeled as a
  generic "Genetics course" slot rather than inserting a prerequisite
  course the sourced bulletin plan never called for; nothing downstream
  actually depends on that specific requirement (`FRNSC 420`'s own
  prerequisite is independently satisfied via `CHEM 212`). Also caught a
  likely bulletin-scrape duplication: `FRNSC 415W` was listed in two
  different semesters of the suggested plan; modeled once.

All five passed at 0 warnings / `goal.met = True` after the `BIOL 234`/
`235W` fix — none committed with the deadlock warning outstanding.
149 → 164 backend tests (3 tests × 5 majors).

### Phase D, third batch (2026-08-11)

**Blocked before building: Environmental Engineering.** Unlike every major
built so far, this bulletin page has no Suggested Academic Plan at all —
confirmed by fetching it twice, once generically and once looking
specifically for that section. The individual courses are all named (this
isn't Psychology's "don't know which option" problem), but there's no
PSU-published semester ordering to build against, so constructing one would
mean guessing at a sequence PSU itself hasn't committed to in writing.
Logged in [BLOCKED_MAJORS.md](BLOCKED_MAJORS.md); **Data Sciences**
substituted in to keep the batch at 5.

- **`BE-2026.json`** — Biological Engineering B.S., Agricultural
  Engineering option, University Park (of three options). Reused
  `EMCH`/`ME`/`CE` catalogs from earlier Engineering builds.
- **`NEURO-2026.json`** — Neurobiology B.S., University Park. The
  bulletin's own suggested plan interleaves two separate requirements
  confusingly (`BIOL 222`-or-`BIOL 161`&`162` appearing in both Second
  Year terms) — resolved against the cleaner Requirements-for-the-Major
  list instead. Caught two real gaps in this plan's own first draft during
  verification, not the bulletin's fault: `MATH 140B` (used to match the
  bulletin's literal text) doesn't satisfy `CHEM 110`'s established
  "concurrent with `MATH 140`" fix, since that check only recognizes the
  bare `MATH 140` code, not variants — fixed by using `MATH 140` like every
  other major already does; and `CHEM 111` was missing entirely from the
  plan, which blocked `CHEM 113` (which needs it as a real prerequisite).
- **`PLANET-2026.json`** — Planetary Science and Astronomy B.S., University
  Park. Needed three new department catalogs on its own (`EARTH`, `GEOG`,
  `METEO`) on top of the `ASTRO`/`GEOSC` catalogs already scraped for
  Astronomy and Astrophysics.
- **`ESC-2026.json`** — Engineering Science B.S., University Park. Major
  code follows the department's real course prefix (`ESC`), not the
  bulletin URL slug's "engineering-science" (an early guess at `EGEE`,
  Energy Engineering's prefix, turned out to be a different department
  entirely with none of the required courses — caught before any file was
  written, not a shipped bug). The bulletin gives no concrete course codes
  for its "Foundational Elective" (15cr) or "Technical Elective" (12cr)
  pools — modeled as generic slots, the same convention as BIOL's
  400-level elective groups, since it's a large open departmental pool
  rather than Psychology-style total ambiguity.
- **`DS-2026.json`** — Data Sciences B.S., Statistical Modeling option,
  University Park. `DS 200`'s `MATH 21` prerequisite is PSU's placement
  threshold (the same recurring pattern, now hit eight times) — moved to
  `concurrent_groups` since the bulletin schedules it alongside `MATH 140`
  in term 1. Deliberately ordered the bulletin's own "`DS 200` or
  `STAT 200`" item as `STAT 200` first, opposite the bulletin's listed
  order — `STAT 462` later in the same plan specifically needs real
  `STAT 200`/`240`/`250`/`401` credit, which `DS 200` alone would never
  satisfy; a plain first-listed pick would have silently produced an
  unschedulable requirement three semesters later.

All five passed at 0 warnings / `goal.met = True` after the two Neurobiology
fixes — neither committed with a warning outstanding. 164 → 179 backend
tests (3 tests × 5 majors).

### Phase D, fourth batch (2026-08-11) — closes Engineering & Eberly Science

- **`SUR-2026.json`** — Surveying Engineering B.S. The bulletin's own
  Suggested Academic Plan is published for the Wilkes-Barre campus (no
  University Park offering), matching the same pattern already established
  for the Intercollege `BUSINESS` major — built anyway, since the courses
  and prereqs apply program-wide regardless of campus. `SUR 121`'s own
  `MATH 26`/`41` concurrent requirement is PSU's placement threshold —
  patched to also accept `MATH 140`/`141`, the ninth instance of this
  recurring pattern.
- **`EMET-2026.json`** — Electro-Mechanical Engineering Technology B.S.
  Suggested plan published for the Beaver campus (labeled by the bulletin
  itself as the "University Park equivalent"). Uses a genuinely slower math
  on-ramp than other Engineering majors (`MATH 26` in term 1, Calc I not
  until term 2) — matched to the bulletin's real sequence rather than
  forcing an early `MATH 140`. Surfaced a real **data bug**: `MATH 26`
  itself required `MATH 21` (an uncompletable placement threshold) as a
  strict prerequisite, which would have made `MATH 26` — the plan's own
  entry point — permanently unschedulable. Fixed by clearing that
  prerequisite in `math_catalog.json`; a student places into `MATH 26`,
  they don't complete a prior course to get there. Also patched `EET 105`
  (concurrent with the same placement threshold) and `MET 111` (needs
  `MATH 26`/`81`) to accept `MATH 140` as an alternate.
- **`INTSC-2026.json`** — Integrative Science B.S., General Science option.
  No dedicated department course prefix, matching `BUSINESS`/`ACTSC`/`CIE`.
  Chose the simpler `PHYS 250`/`251` and `BIOL 230W` paths over the
  bulletin's other listed alternatives, the same choice already made for
  Neurobiology and Biotechnology.
- **`EET-2026.json`** — Electrical Engineering Technology B.S. The
  bulletin merges two options (General EET, Power/Automation) into one
  table with variable credit ranges ("0-3", "3-11") instead of a clean
  per-option breakdown — built against the General EET option's real
  anchor courses, with the option-dependent electives modeled as generic
  slots (the same call made for `ESC`'s Foundational/Technical Elective
  pools). Surfaced two more real data bugs: `EET 114` requires `EET 105`
  **and separately** `MATH 26` — two independent required groups, not
  alternatives to each other — patched the `MATH 26` group to also accept
  `MATH 140`/`141`. And `EET 331`'s three separate "AND" prereq groups
  (`EE 314`/`315`/`EET 311`, `EE 310`, `EET 312`) were almost certainly a
  scraper-flattened OR-group of equivalent circuits courses, not a
  requirement to complete three unrelated circuits sequences — merged into
  one OR group, the same "flattened OR-group" quirk already documented for
  CMPSC/CMPEN 315.

Reused catalogs across all four (`MATH`, `PHYS`, `CHEM`, `ENGL`, `CAS`,
`EDSGN`, `CMPSC`) plus five new ones (`SUR`, `EET`, `CMPET`, `EMET`,
`MET`/`IET`/`EGT`/`STS`). This closes out every major from the original
discovery table's Engineering and Eberly Science college listings except
Environmental Engineering (blocked) and Psychology (blocked).

### Opening Earth and Mineral Sciences (2026-08-11)

- **`METEO-2026.json`** — Meteorology and Atmospheric Science B.S.,
  Atmospheric Science option (of six options — Climate Science,
  Environmental Meteorology, General, Weather Forecasting and
  Communications, and Weather Risk Management are the others), University
  Park. First major from this college. Reused the `METEO` catalog already
  scraped for Planetary Science and Astronomy — every `METEO` course
  fetched came back with no listed prerequisites at all, the simplest
  prereq graph of any major built so far.

All five passed at 0 warnings / `goal.met = True` after the three data
fixes above — none committed with a warning outstanding. 179 → 193 backend
tests (3 tests × 5 majors, minus one class split differently than usual —
`TestElectricalEngineeringTechnologyPlan` folded its data-bug notes into
its docstring rather than a fourth test method).

### Earth and Mineral Sciences, second batch (2026-08-11)

- **`GEOSCI-2026.json`** — Geosciences B.S., General option (of two — the
  other, Hydrogeology, swaps in a different elective structure). Major code
  `GEOSCI` avoids colliding with the `GEOSC` department prefix used across
  most of this college's majors. Every `GEOSC` course came back with no
  listed prerequisites except one concurrent pair. The bulletin's real plan
  schedules `GEOSC 472B` (Field Geology II) in a required summer term —
  this planner only models summer as an optional student choice, so it's
  scheduled in a regular term instead.
- **`GEOG-2026.json`** — Geography B.S. Every core `GEOG` course has zero
  listed prerequisites, so ordering follows the bulletin's own suggested
  sequence directly — the simplest major to sequence so far.
- **`ENGY-2026.json`** — Energy Engineering B.S. Major code `ENGY` avoids
  colliding with the `EGEE` department prefix. The bulletin's own
  "`EGEE 451` or `ENVSE 470`" item lists `EGEE 451` first, but `EGEE 451`
  needs `FSC 431` (a Fuel Science course not otherwise part of this plan)
  — relies on the engine's option-fallback fix (from the Economics build)
  to resolve to `ENVSE 470` instead, confirming that fix generalizes to a
  case it wasn't originally written for.
- **`MATSCI-2026.json`** — Materials Science and Engineering B.S. Major
  code `MATSCI` avoids colliding with the `MATSE` department prefix. The
  capstone (`MATSE 493W` or `494W`, a variable 0-3/1-3 credit split across
  two real terms per the bulletin) is simplified to one 3-credit term.
- **`EARTHSCI-2026.json`** — Earth Sciences B.S. Major code `EARTHSCI`
  avoids colliding with the `EARTH` department prefix. Requires 18 credits
  from ONE of five interdisciplinary minors (Climatology, Marine Science,
  Watersheds and Water Resources, Earth Systems, Global Business
  Strategies) — assumed Earth Systems as the most directly related, modeled
  generically as "Minor Course" slots since the bulletin page doesn't give
  the per-minor course list.

All five passed at 0 warnings / `goal.met = True` on the first simulation —
no data bugs this round. 193 → 206 backend tests.

### Earth and Mineral Sciences, third batch (2026-08-11) — closes the college

- **`GEOBIO-2026.json`** — Geobiology B.S. The "`BIOL 444` or `GEOSC 472A`"
  item lists `BIOL 444` first per the bulletin's own order, but `BIOL 444`
  needs `BIOL 220W` (not otherwise part of this plan) — relies on the
  engine's option-fallback fix to resolve to `GEOSC 472A` instead.
- **`MINE-2026.json`** — Mining Engineering B.S. Major code `MINE` avoids
  colliding with the `MNG` department prefix. Two real data bugs: the
  bulletin's own suggested plan lists "`EME 460` or `MNG 412`" as one
  alternative pick, but `MNG 412` is independently required by `MNG 451W`'s
  own capstone prerequisite — a genuine four-way AND-chain (`MNG 331` +
  `MNG 404` + `MNG 412` + `MNG 422`), not a flattened-OR artifact this
  time. Modeling them as alternatives left `MNG 412` never actually
  completed, permanently blocking the capstone — fixed by making both
  standalone required items, matching the Requirements table. Second bug
  was a knock-on effect of an earlier fix: after `EME 210`'s placement-gate
  prerequisite was patched (see `EME-2026.json`'s note) to also accept
  `MATH 140`/`141`, `EME 210` became eligible early enough that "`EME 210`
  or `STAT 401`" would resolve to `EME 210` — but `MNG 412` specifically
  needs `STAT 401`. Fixed by deliberately ordering `STAT 401` first,
  opposite the bulletin's own order (the same class of fix already used
  for Economics and Energy Engineering).
- **`PNG-2026.json`** — Petroleum and Natural Gas Engineering B.S. `PNG 490`
  (capstone) genuinely requires six separate courses completed first
  (`PNG 430`, `PNG 440W`, `PNG 450`, `EME 460`, `PNG 475`, `GEOSC 454`) —
  all six were already independently required elsewhere in the plan, so no
  extra items were needed. Passed at 0 warnings on the first simulation,
  though building it first surfaced the `EME 210` placement-gate bug that
  later required the Mining Engineering fix above.
- **`ENVSYS-2026.json`** — Environmental Systems Engineering B.S. Major
  code `ENVSYS` avoids colliding with the `ENVSE` department prefix. The
  bulletin's own suggested plan lists an "`EME 210` or `ENGL 202C`" item in
  two different terms — since `ENGL 202C` is separately a required
  prescribed course, this reads as a scrape duplication (the same class of
  issue as `FRNSC 415W` a few batches back) — modeled `ENGL 202C` once,
  with a generic Supporting Course slot in its second appearance.
- **`EBFIN-2026.json`** — Energy Business and Finance B.S. Major code
  `EBFIN` avoids colliding with the `EBF` department prefix. Surfaced a
  real **engine-level bug distinct from anything found so far**: the
  bulletin requires 6 credits from "`EGEE 401`/`EME 444`/`METEO 469`"
  across two separate terms, but with this plan's own course choices
  (`IB 303` chosen over `EGEE 120`, no `CHEM 112` anywhere) only
  `METEO 469` is ever actually eligible — `EME 444` and `EGEE 401` are
  permanently prereq-blocked given what else this plan includes. Modeling
  both occurrences as a real course pick caused a genuine infinite loop —
  24 simulated terms, never finishing — because the second item could
  never resolve to a course distinct from the first (unlike every prior
  duplicate-option case, where a second real alternative existed once the
  first was consumed). This is a different failure mode from the
  already-fixed "first-ranked option blocked" bug: here *every* option but
  one is blocked, so there's no valid second pick at all. Fixed by
  modeling the second occurrence as a generic slot instead of forcing a
  duplicate real pick — the correct fix is at the data level (only one
  course actually fits this plan), not the engine level.

All five passed at 0 warnings / `goal.met = True` after the fixes above —
none committed with a warning (or an infinite loop) outstanding. This
closes every major from the original discovery table's Earth and Mineral
Sciences college listing except Earth Science and Policy and Energy and
Sustainability Policy. 206 → 220 backend tests.

### Earth and Mineral Sciences policy majors + opening Agricultural Sciences (2026-08-11)

- **`ESP-2026.json`** — Earth Science and Policy B.S. (General option).
  `EBF 472`, one of four bulletin-listed alternatives for one item, doesn't
  exist in the current department catalog — dropped in favor of the other
  three. Real bug: the bulletin's own "`MATH 83`, `110`, `140`, or `140G`"
  ordering, followed literally, resolves to `MATH 110` — which does not
  satisfy `CHEM 110`'s concurrent `MATH 140`/`140G`/`141`/`22` requirement
  (same bug class as Neurobiology's `MATH 140B` case) — reordered to list
  `MATH 140`/`140G` first. This closes the Earth and Mineral Sciences
  college.
- **`ESUS-2026.json`** — Energy and Sustainability Policy B.S. World Campus
  only — the bulletin's Suggested Academic Plan is published for World
  Campus, not University Park (same "no UP offering" pattern already seen
  for Surveying Engineering). Its own year-by-year table lists uneven
  per-year credit totals (31/33/30/26) rather than a clean semester split —
  redistributed the same courses into a standard 8-term, ~15cr/term
  structure.
- **`ANSC-2026.json`** — Animal Science B.S. (Industry and General Animal
  Interest option — the other track, Animal Health/Research/Higher
  Education, needs heavier science prerequisites like `MICRB 201`/`202`
  and `PHYS 250`/`251`). Opens the College of Agricultural Sciences. Passed
  at 0 warnings on the first simulation.
- **`FDSC-2026.json`** — Food Science B.S. Two real bugs: (1) `CHEM 110`
  requires a concurrent `MATH 140`/`141`/`22`, but the plan's `MATH`
  item was originally scheduled a full semester after `CHEM 110` with no
  math course alongside it at all — fixed by moving `MATH 140` (or
  `140B`/`110`) into Semester 1, the same fix pattern as `ESP` above and
  Neurobiology's `MATH 140B` case; (2) `FDSC 405` requires `MATH 110` *and*
  `PHYS 250` as prerequisites, but this plan takes `MATH 140` instead of
  literal `MATH 110` — added `MATH 140`/`140B` as alternates to that
  prereq group in `fdsc_catalog.json`.
- **`PLSCI-2026.json`** — Plant Sciences B.S. (Agroecology option — the
  only one of five options with a concrete example semester plan on the
  bulletin page; the other four — Crop Production, Horticulture, Plant
  Genetics and Biotechnology, Plant Science — are unbuilt). Major code
  `PLSCI` avoids colliding with the department's own `PLANT` prefix. Real
  data gap: `AGRO 28` and `HORT 101` (real anchor courses — `AGRO 28`
  gates `AGECO 438`, not a generic elective) were entirely missing from
  the catalog since the scraper never covered those two small
  departments — added minimal course entries to new `agro_catalog.json`
  and `hort_catalog.json` files (title, credits, no prereqs, sourced
  directly from the PSU course description pages).

All five passed at 0 warnings / `goal.met = True`. 220 → 232 backend
tests.

### Agricultural Sciences, second batch (2026-08-11)

- **`AGBM-2026.json`** — Agribusiness Management B.S. `AGSC 100` (AESE
  First Year Seminar, 1cr, no prereqs) was missing from the catalog
  entirely — added a minimal entry in new `agsc_catalog.json`. `AGBM 101`
  is listed first over `ECON 102` since almost every downstream AGBM
  course's prereq OR-group is trivially satisfied once `AGBM 101` alone
  is completed.
- **`IID-2026.json`** — Immunology and Infectious Disease B.S. Major code
  `IID` since courses split across `VBSC`/`MICRB`/`BMB` with no single
  natural prefix. The entire `VBSC` department catalog was missing from
  the scraper's coverage — added minimal entries in new
  `vbsc_catalog.json`, sourced from PSU course description pages. Real
  data gap: `VBSC 448W` needs `BMB 400`, which the bulletin's own
  suggested plan never actually scheduled anywhere (only `BMB 401`/`402`
  were listed) — added as an explicit Semester 7 item. Of the bulletin's
  "select 2 of 3 (`VBSC 435`/`445`/`451`)" pool, chose `435` and `451` —
  `445` needs a `BIOL 220` prereq (ambiguous whether that means the
  writing-intensive `BIOL 220W`), sidestepped entirely by picking the
  other two.
- **`PHTX-2026.json`** — Pharmacology and Toxicology B.S. Major code
  `PHTX`. Extended `vbsc_catalog.json` with `VBSC 190`/`230`/`331`/`430`/
  `431`/`433`/`438`. `VBSC 331`'s enforced `BIOL 230W`/`230M` prerequisite
  initially looked like a real gap (a narrower re-fetch of just the
  Year-2 rows missed it), but the bulletin's own Suggested Academic Plan
  does schedule `BIOL 230W` in Year 1 Spring — no fix was actually needed,
  just a closer re-read. `VBSC 438` lists `CHEM 202`/`201` as its prereq,
  but this plan's chemistry sequence uses `CHEM 210` instead — added
  `CHEM 210` as an equivalent alternate in the catalog entry, since the
  bulletin's own `CHEM 210` description says `CHEM 202` and `CHEM 210`
  "duplicate subject matter" and can't both be taken for credit (same
  precedent as `BMB 211`'s existing `CHEM 202`/`210` equivalence).
- **`ERM-2026.json`** — Environmental Resource Management B.S.
  (Environmental Science option — of three: Environmental Science, Soil
  Science, Water Science; Soil Science's own second year has an
  unresolved 5-way `AGRO 28`/`HORT 101`/`TURF 235`/`BIOL 220W`/`FOR 203`
  pool spanning two still-uncovered departments, so Environmental Science
  was chosen instead). `ASM 327` (a real anchor course, required across
  multiple majors/minors) had no findable prerequisite text anywhere on
  the bulletin after several attempts — its dedicated course-description
  page 404s — added a minimal no-prereq entry to new `asm_catalog.json`.
  `CED 201` requires `ERM 300` as a same-term concurrent requirement —
  both scheduled in Semester 6, with `ERM 300` listed first to resolve in
  the same scan pass.
- **`WFS-2026.json`** — Wildlife and Fisheries Science B.S. (Wildlife
  option — of two: Wildlife, Fisheries). The entire `WFS` department
  catalog and `FOR 203`/`350` (real anchor courses in the still-mostly-
  uncovered Forest Ecosystems department) were missing entirely — added
  minimal entries in new `wfs_catalog.json`/`for_catalog.json`. Real
  **catalog-level bug** distinct from anything data-specific to this
  major: `STAT 240`'s only listed prerequisite was the uncompletable
  placement-gate `MATH 21` — the same recurring pattern already fixed for
  `STAT 184`/`DS 200`/`ECON 106`/`SUR 121` earlier this session, just not
  yet caught for `STAT 240` specifically since no earlier major happened
  to need it. Fixed by adding `MATH 110`/`140` alternates in
  `stat_catalog.json` — this benefits every future major that reaches for
  `STAT 240`, not just this one. `WFS 407`+`406` (Ornithology + lab) cover
  the bulletin's first "`WFS 407` or `408`" selection; `WFS 408`
  (Mammalogy, lecture only) covers its second selection, avoiding
  scheduling the same course code twice.

This opens 8 of Agricultural Sciences' 16 majors (Food Science, Animal
Science, Plant Sciences from the prior batch, plus these five); 8 remain
(Agricultural and Biorenewable Systems Management, Agricultural and
Extension Education, Agricultural Science, Community/Environment/
Development, Forest Ecosystems, Landscape Contracting, Turfgrass Science,
Veterinary and Biomedical Sciences). All five passed at 0 warnings /
`goal.met = True`. 232 → 245 backend tests.

### Agricultural Sciences, third batch (2026-08-11) — closes the college

- **`ABSM-2026.json`** — Agricultural and Biorenewable Systems Management
  B.S. The entire `ABSM` department catalog was missing — added minimal
  entries in new `absm_catalog.json`. Several `ABSM` courses
  (`350`/`391`/`392`/`426`/`429`/`490`) list "5th/7th-semester standing"
  prerequisites the planner schema doesn't model directly (only
  course-code prereqs exist) — these are scheduled in their
  bulletin-intended later terms by author placement, same limitation as
  every major's "First-Year Seminar"-style items; real course-code chains
  (`391`→`392`→`430W`→`431W`, `301`→`422`/`428`) are fully encoded where
  the bulletin actually gives one.
- **`VBS-2026.json`** — Veterinary and Biomedical Sciences B.S. Extended
  `vbsc_catalog.json` (already started for Immunology/Pharmacology) with
  `VBSC 421` and `VBSC 403`. Chose the `CHEM 210`/`212`/`213` organic
  chemistry track and `BIOL 230W` for the plan's "or" pools since both
  feed cleanly into `BMB 401`'s own prereq OR-group.
- **`TURF-2026.json`** — Turfgrass Science B.S., single track. The entire
  `TURF` department catalog was missing — added minimal entries in new
  `turf_catalog.json`. Real bug **avoided** during construction (caught
  before it became a warning): the bulletin's own entry math course is
  `MATH 21` — a real, completable course here (unlike its usual role
  elsewhere in the catalog as an uncompletable placement-gate
  prerequisite) — but `CHEM 110`'s concurrent requirement only recognizes
  `MATH 140`/`141`/`22`, not `MATH 21`. Picked `CHEM 130` (no such
  concurrent requirement) over `CHEM 110` for Semester 1 specifically to
  sidestep the mismatch.
- **`FORES-2026.json`** — Forest Ecosystems B.S. (Biodiversity and
  Conservation option — of four; the other three reference
  `LARCH`/`ARCH`/`RPTM`/`GEOG` courses not yet in any catalog). Major code
  `FORES` avoids colliding with the `FOR` department prefix. Extended the
  `FOR` department catalog (started for Wildlife and Fisheries Science)
  with `FOR 200`/`204`/`255`/`266`/`308`/`409`/`410`/`421`/`430`/`450W`,
  plus `hort_catalog.json` with `HORT 445`. Real bug caught during
  verification (same class as `TURF`'s, but this time it actually fired a
  warning): the Semester 1 math item listed `MATH 110` first, which
  doesn't satisfy `CHEM 110`'s concurrent `MATH 140`/`141`/`22`
  requirement — cascaded into a 3-course warning (`CHEM 110`/`111`/`202`
  all stuck) — reordered to list `MATH 140` first, same fix pattern as
  Earth Science and Policy and Food Science.
- **`CED-2026.json`** — Community, Environment, and Development B.S.
  (Community and Economic Development option — of four; International
  Development's own "`AFR 440`, `CED 450`, `ECON 333`, `IB 440`,
  `PLSC 412`, or `PLSC 440`" 6-way pool spans departments not yet in any
  catalog). Real data gap: `AEE 460` has an enforced prerequisite of
  `AEE 360`, which the bulletin's own suggested plan never otherwise
  schedules anywhere — added `AEE 360` as an explicit Semester 5 item
  (new `aee_catalog.json`), the same class of fix as Food Science's
  `BMB 400` and Immunology's `BMB 400` additions earlier this session.

This closes the College of Agricultural Sciences — all 16 majors from the
original discovery table now built. All five passed at 0 warnings /
`goal.met = True`. 245 → 258 backend tests.

### Opening Information Sciences and Technology (2026-08-11)

- **`AIMA-2026.json`** — Artificial Intelligence Methods and Applications
  B.S. The entire `A-I` and `AIMA` departments were missing — added
  minimal entries in new `a-i_catalog.json`/`aima_catalog.json`. Real data
  gap: `STAT 401` needs `MATH 111`/`141`, never scheduled anywhere in the
  bulletin's own plan — added `MATH 141` explicitly. Separately, a
  **genuine infinite-loop bug identical in shape to Energy Business and
  Finance's `METEO 469` case**: `AIMA 430` was originally scheduled
  directly after its own prerequisite `A-I 375`, with enough same-term
  credit headroom left in that JSON block that the engine's greedy scan
  pulled both into the *same* simulated term — since prereqs (unlike
  concurrent requirements) only check credit already banked from prior
  terms, `AIMA 430` failed its own prereq check and silently fell back to
  the `A-I 494` alternate, permanently starving the real capstone
  sequence and looping for 24 simulated terms. Fixed at the DATA level by
  padding Semester 6 to 18 credits (over the 17cr/term cap) so the scan
  closes that term before ever reaching `AIMA 430`, guaranteeing
  `A-I 375` lands in `completed` a full term earlier.
- **Information Sciences and Technology, B.S. — blocked.** Unlike every
  other IST major, this one has no Suggested Academic Plan anywhere —
  confirmed via both the bulletin page (which only states 125 credits,
  entrance courses, and that one of its two options is "currently
  unavailable") and its own suggested-academic-plan PDF, which contains
  no course table at all, just campus-closure metadata. Logged in
  [BLOCKED_MAJORS.md](BLOCKED_MAJORS.md); substituted Information
  Technology Ethics and Compliance to keep the batch at 5.
- **`IEC-2026.json`** — Information Technology Ethics and Compliance B.S.
  The entire `IEC` and `ETI` departments were missing. Three real data
  gaps: (1) `MATH 22` needs `MATH 21`, never scheduled — substituted
  `MATH 110`, matching the bulletin's own "`MATH 22` or higher" phrasing;
  (2) `ETI 301`/`ETI 302` both need `IST 210` *and* `IST 220`, but
  `IST 220` never appears in the bulletin's plan — added it explicitly;
  (3) `DS 435` needs `DS 220` needs `CMPSC 121`/`131`, none scheduled —
  added `CMPSC 131` and `DS 220` explicitly, same class of fix as Food
  Science's `BMB 400` and CED's `AEE 360`.
- **`SRA-2026.json`** — Security and Risk Analysis B.S. (Intelligence
  Analysis and Modeling option — of two; the other, Information and Cyber
  Security, needs `IST 451`/`454`/`456`, not yet in any catalog). No data
  gaps found — every prereq resolves cleanly against courses this plan
  already schedules.
- **`HCDD-2026.json`** — Human-Centered Design and Development B.S. The
  entire `HCDD` department was missing. Every HCDD-sequence course
  (`264`/`311`/`340`/`361`/`364W`/`411`/`412`/`440`) accepts `HCDD 311` as
  an equivalent to the nonexistent `IST 311` — consistently picking the
  `HCDD`-prefixed course throughout keeps the whole chain self-satisfying
  with zero gaps, unlike most of this batch's other majors.
- **`ETI-2026.json`** — Enterprise Technology Integration B.S. Extended
  `eti_catalog.json` with `ETI 300W`/`420`/`421`/`423`/`435`/`461`/`463`,
  picking the `ETI`-prefixed variant of every `ETI X`-or-`IST X` pool
  since the `IST` alternates (`301`/`302`/`420`/`421`/`423`) don't exist.
  Real data gap: the bulletin's "`HCDD 331`, `IST 331`, or `HCDD 264`"
  item — the first two don't exist, and `HCDD 264` needs `HCDD 113`/
  `113S`/`ETI 100` as a prereq, none otherwise scheduled — added
  `HCDD 113S` explicitly to unlock it.

This effectively closes the College of Information Sciences and
Technology's 8-major table: Cybersecurity Analytics and Operations (built
earlier this session) and Data Sciences (built earlier as an Eberly
Science cross-listing) were already done, and this batch adds AIMA, SRA,
HCDD, and ETI — leaving only Information Sciences and Technology, B.S.
itself, which is blocked pending user input (see
[BLOCKED_MAJORS.md](BLOCKED_MAJORS.md)). All five attempted passed at 0
warnings / `goal.met = True`. 258 → 269 backend tests.

### Opening and closing Bellisario College of Communications (2026-08-11)

The college has exactly 5 undergraduate majors across 4 departments
(Advertising/Public Relations; Film Production and Media Studies, which
splits into two separate majors; Journalism; Telecommunications) — small
enough to close out in one batch, like Agricultural Sciences and IST. The
entire `COMM` department catalog was missing; every course this batch
needed was added to a single new `comm_catalog.json`, built up
incrementally across all five majors.

- **`JOURN-2026.json`** — Journalism B.A. (Digital and Print Journalism
  option — of three; Broadcast and Photojournalism need specialized
  production courses not yet scraped). Major code `JOURN` avoids
  colliding with the department's own `COMM` prefix. The bulletin's
  repeated "`COMM 403/409`" pool (appearing twice) was resolved to two
  distinct courses — `403` on the first occurrence, `409` on the second —
  avoiding scheduling the same code twice, same convention as `WFS`
  407/408 and `ENT 313`/`PPEM 318` earlier this session.
- **`ADPR-2026.json`** — Advertising/Public Relations B.A. (Public
  Relations option — of two; Advertising's own `COMM 424` capstone
  chain wasn't cross-checked). No data gaps found — the full
  `COMM 370`→`372`/`420`/`471`→`473` prereq chain resolves cleanly.
- **`TELE-2026.json`** — Telecommunications and Media Industries B.A.,
  no formal tracks. Extended `comm_catalog.json` with
  `COMM 180`/`280`/`380`/`404`/`486`/`487W`.
- **`FLMPR-2026.json`** — Film Production B.A. Major code `FLMPR` since
  the Film Production and Media Studies department has no course-code
  prefix of its own (everything is `COMM`-numbered). Of the bulletin's
  "Advanced Production"/"Advanced Additional" pool (9 possible codes,
  select 4), picked `COMM 437`/`440`/`444`/`445` — all four share the
  identical "`COMM 340` + `COMM 342W` + one of `337`/`338`/`339`" prereq
  shape; `COMM 439`/`437A`/`443`/`446` need `COMM 339`, not otherwise in
  this plan, so were skipped rather than guessed at.
- **`MDST-2026.json`** — Media Studies B.A. (Media Effects option — of
  three; Film/TV Studies and Society/Culture both lean on larger,
  less-defined "`COMM` 400-level" pools spanning 7-15 possible codes).
  Major code `MDST` for the same department-prefix reason as `FLMPR`. Of
  the "`COMM 325`/`326`/`327`/`328`" Media Effects elective pool, verified
  and used `COMM 325` and `326` (`327`/`328` weren't independently
  confirmed, so weren't used, though very likely share the same prereq
  shape).

This closes the Donald P. Bellisario College of Communications — all 5
majors from the original discovery table now built. All five passed at 0
warnings / `goal.met = True`. 269 → 279 backend tests.

### Opening Health and Human Development (2026-08-11)

The college has 9 majors; this batch covers the 5 highest-demand ones
(Kinesiology, Nutritional Sciences, Human Development and Family Studies,
Health Policy and Administration, Biobehavioral Health), leaving
Communication Sciences and Disorders, Hospitality Management, Recreation
Park and Tourism Management, and Systems Neuroscience for a follow-up
batch. The `KINES`/`NUTR`/`HDFS`/`HPA`/`BBH` department catalogs were all
already substantially populated from earlier scraping — no new catalog
files needed except `hm_catalog.json`.

- **`KINES-2026.json`** — Kinesiology B.S. (Movement Science option — of
  three; Applied Exercise and Health is a PDE teacher-certification track
  needing `SPLED 400`/`CI 280`/student teaching, and Exercise Science
  needs relocation off University Park). Real bug avoided: the bulletin's
  own Semester 1 math pick is `MATH 26`, but `CHEM 110` (Semester 3) has
  a concurrent requirement recognizing only `MATH 22`/`140`/`141`, not
  `MATH 26` — substituted `MATH 140`, same recurring mismatch fixed for
  several majors this session. Also explicitly scheduled `ENGL 15` and
  `CAS 100A` rather than generic "GWS" slots, since `ENGL 202C`/`D` later
  needs `ENGL 15`/`30H` specifically.
- **`NUTR-2026.json`** — Nutritional Sciences B.S. (Nutrition and
  Dietetics option — of two; Health Sciences leans on undifferentiated
  "Any NUTR course" placeholders throughout). Added `hm_catalog.json` for
  `HM 230`/`330` (Hospitality Management department). Chose `CHEM 202`
  over `CHEM 210` since `202`'s prereq (`CHEM 110`) is directly
  satisfied, while `210` needs `CHEM 112`, not otherwise scheduled.
- **`HDFS-2026.json`** — Human Development and Family Studies B.S.
  (Human Development and Family Science option — of two; Developmental
  Science for Health Professions needs an unspecified 4-item "Science
  and Health Foundations" pool). Real bug avoided: the `HDFS 200`/
  `EDPSY 101`/`STAT 200` statistics item lists `STAT 200` first, since
  `HDFS 312W`'s own prereq only recognizes `EDPSY 101`/`STAT 200`, not
  `HDFS 200` — same mismatch pattern as `KINES`'s `MATH 26` case. The
  bulletin's flexible "HDFS Capstone" (internship/research pathways, no
  single official course sequence) was split into a real Fall precursor
  — `HDFS 490`, the actual required first step for every pathway — and a
  generic Spring slot for the pathway-specific follow-on course, which
  genuinely isn't pinned down by the bulletin itself.
- **`HPA-2026.json`** — Health Policy and Administration B.S., no formal
  tracks. Real bug avoided: picked `CMPSC 203` over `CMPSC 101` for the
  "Programming/Spreadsheets/MIS" item, since `CMPSC 101` has an
  uncompletable placement-gate prereq (`MATH 21`, never scheduled) — same
  recurring pattern fixed for `STAT 184`/`DS 200`/`ECON 106`/`SUR 121`/
  `STAT 240` earlier this session. Of the "9 credits from `HPA` 400-level
  electives" pool, picked `HPA 442`/`444`/`446` (all resolve cleanly),
  avoiding `HPA 445` (needs `ECON 302`/`315`/`323`, not otherwise
  scheduled).
- **`BBH-2026.json`** — Biobehavioral Health B.S., no formal tracks. No
  data gaps found — `BBH 311`'s 3-way `BBH 101`+`BIOL 110`+`PSYCH 100`
  requirement and `BBH 302`/`310`/`440`/`411W`'s `STAT 200`/`BBH 101`/
  `310` chains all resolve cleanly against courses this plan already
  schedules.

All five passed at 0 warnings / `goal.met = True`. 279 → 290 backend
tests.

### Closing Health and Human Development (2026-08-11)

The final 4 majors: Communication Sciences and Disorders, Hospitality
Management, Recreation Park and Tourism Management, and Systems
Neuroscience.

- **`CSD-2026.json`** — Communication Sciences and Disorders B.S., no
  formal tracks. The entire `CSD` department catalog was missing — added
  minimal entries in new `csd_catalog.json`. Real bug avoided: listed
  `STAT 200` first (not `PSYCH 200`) for the statistics item, since
  `PSYCH 200` has a genuine two-part AND prereq (`PSYCH 100` AND
  `MATH 21`), and `MATH 21` is never scheduled — same recurring
  placement-gate pattern fixed for several majors this session.
- **`HM-2026.json`** — Hospitality Management B.S. (the only option
  offered at University Park; Hospitality Entrepreneurship is Berks-only).
  Extended `hm_catalog.json` (started for Nutritional Sciences) with 16
  more `HM` courses. Real data quirk: `HM 366`'s bulletin-cited prereq
  ("`HM 201` and `HM 365`") references course numbers that don't exist
  anywhere in the catalog — treated as referring to their modern
  equivalents, `HM 101` and `HM 265W` (documented directly in the catalog
  entry as a judgment call, not a guess made silently).
- **`RPTM-2026.json`** — Recreation, Park, and Tourism Management B.S.
  (Commercial Recreation and Tourism Management option — of four; Outdoor
  Recreation's "pathway course" pool has no titles/codes given, and
  Professional Golf Management requires a golf handicap of 12 or lower
  for admission). The entire `RPTM` department catalog was missing. Real
  data gap: `RPTM 433W`'s bulletin-cited prereq "`RPTM 356`" doesn't
  exist anywhere — treated as referring to `RPTM 456` (which the
  suggested plan itself schedules directly beforehand) — and this major's
  own suggested plan never schedules any statistics course at all despite
  `RPTM 433W` requiring one, so a real `STAT 200` was substituted for a
  `GQ` Gen Ed slot.
- **`NROSCI-2026.json`** — Systems Neuroscience B.S., no formal tracks.
  Major code `NROSCI` avoids colliding with Eberly Science's Neurobiology
  (`NEURO`), already built earlier this session. Real recurring bug found
  and fixed at the catalog level, distinct from anything found so far:
  this major's own entrance math course is `MATH 140B` (Calculus and
  Biology I), but `CHEM 110`'s concurrent requirement, `PHYS 250`'s
  prereq, and `STAT 184`'s concurrent requirement *all three* failed to
  recognize it as equivalent to `MATH 140`/`110`/`22` — added `MATH 140B`
  as an accepted alternate to all three in `chem_catalog.json`/
  `phys_catalog.json`/`stat_catalog.json`, which benefits every future
  major reaching for any of the three, not just this one. A second,
  unrelated bug: `BBH 470`/`BIOL 470` both strictly require the literal
  code `BIOL 469` as a prereq, not the cross-listed `BBH 469` (same
  course, different department code) — reordered the relevant item to
  list `BIOL 469` first.

This closes the College of Health and Human Development — all 9 majors
from the original discovery table now built. All four passed at 0
warnings / `goal.met = True`. 290 → 299 backend tests.

### Opening the College of Education (2026-08-11)

The college has 9 majors; this batch covers the 5 highest-demand ones
(Elementary and Early Childhood Education, Special Education, Secondary
Education, Rehabilitation and Human Services, Education and Public
Policy), leaving Elementary and Kindergarten Education, Middle Level
Education, Workforce Education and Development, and World Languages
(K-12) Education for a follow-up batch. Ten new departments were
entirely missing from the catalog (`EDTHP`, `EDUC`, `MTHED`, `EDPSY`,
`CI`, `ECE`, `LLED`, `SSED`, `SCIED`, `SPLED`, plus `RHS`/`HIST` added
later in the batch) — built up incrementally across all five majors,
sourced from PSU course description pages.

- **`ELED-2026.json`** — Elementary and Early Childhood Education B.S.,
  single pathway. Real bug found and fixed: this plan's own
  `departments` list initially omitted `MATH`, which meant `MATH 200`
  wasn't in the merged catalog at all — the engine's tiered option
  ranking (catalog-presence is a preference tier, not a hard filter)
  silently fell through to `MTHED 240` instead, with no warning fired
  until the *downstream* course (`MTHED 420`, which needs `MATH 200`
  specifically) came up empty three semesters later. A **second**, more
  subtle bug of the same shape: `ECE 451` requires concurrent enrollment
  in *both* `EDPSY 11` and `HDFS 229`, but the bulletin's own item only
  offers them as alternatives — added `HDFS 229` as an explicit second
  item so the real AND requirement is satisfiable. Real data artifact:
  `CI 495D` cites `CI 495A; CI 495B` as prereqs, but `CI 495B` requires
  admission to the separate Middle Level Education major — treated as a
  bulletin template artifact, not modeled.
- **`SPLED-2026.json`** — Special Education B.S., single track. Extended
  `spled_catalog.json` with 17 more courses plus `EDPSY 10`. No real data
  gaps found — this major's entire 8-semester progression is
  self-consistent, every 300/400-level course's real prereq chain
  resolving cleanly against courses the bulletin itself schedules the
  prior term.
- **`SECED-2026.json`** — Secondary Education B.S. (Biology Teaching
  option — of five content areas: Biology, Chemistry, Earth and Space
  Science, English, Mathematics; Biology was the only one with a fully
  detailed plan in the fetched source). Real data artifact handled: `CI
  495C`/`495E` cite the content-methods courses for *all five* teaching
  options as prereqs/corequisites (`LLED 412W`/`MTHED 412W`/`SCIED
  412`/`SSED 412W`) — only `SCIED 412` (Science) applies to this option;
  the rest were treated as a shared bulletin template artifact, same
  judgment-call precedent as `ELED`'s `CI 495B` case.
- **`RHS-2026.json`** — Rehabilitation and Human Services B.S., single
  track. The entire `RHS` department catalog was missing — added minimal
  entries in new `rhs_catalog.json`. Real bug fixed: `RHS 302` needs a
  concurrent statistics course, but the bulletin's own suggested plan
  schedules `RHS 302` in Year 2 Spring while the statistics course
  doesn't appear until Year 3 Fall — moved `STAT 200` to the same term as
  `RHS 302`, same fix pattern as several majors earlier this session
  (`FDSC`/`ESP`/`FORES`'s `CHEM 110`/`MATH` cases).
- **`EDPP-2026.json`** — Education and Public Policy B.S., no formally
  named tracks (students pick from department-approved "Policy
  Problems", "Leadership", and "Diversity & Equity" lists instead).
  Extended `edthp_catalog.json` with `EDTHP 200`/`394`/`395`/`420`, plus
  new `hist_catalog.json` for `HIST 21`. No real data gaps found.

This opens 5 of the College of Education's 9 majors; 4 remain (Elementary
and Kindergarten Education, Middle Level Education, Workforce Education
and Development, World Languages (K-12) Education). All five attempted
passed at 0 warnings / `goal.met = True`. 299 → 311 backend tests.

---

### Closing College of Education + opening Arts and Architecture (2026-08-12)

Attempted the College of Education's remaining 4 majors. Two are blocked
on program holds, not data gaps — logged in `BLOCKED_MAJORS.md` rather
than built:

- **Elementary and Kindergarten Education, B.S.** — on hold since
  2010-09-10, "PROGRAM CURRENTLY ON HOLD; NOT ACCEPTING NEW STUDENTS," no
  Suggested Academic Plan published (consistent with 15+ years of
  non-admission).
- **World Languages (K-12) Education, B.S.** — on hold since 2024-04-25.
  Unlike the above, this one *does* have a full 5-language-option plan
  published; the blocker is purely non-admission, not missing data.

The other two built cleanly:

- **`MLED-2026.json`** — Middle Level Education B.S., English 4-8 Option
  (of three content areas: English, Math, Social Studies). Extended
  `ci_catalog.json` with `CI 295B`/`CI 495B` and `lled_catalog.json` with
  `LLED 450`. Real fix: generalized the shared `CI 495D` catalog entry's
  prereq from `CI 495A`-only (an ELED-specific assumption baked into
  shared data) to an OR of `CI 495A`/`CI 495B`, since Middle Level
  Education needs `CI 495B` — verified `ELED`'s own plan still passes
  after the change. Real gap: `LLED 450` needs `EDPSY 14`, which the
  English 4-8 option's own suggested plan never otherwise schedules —
  added it as an explicit Semester 1 item. Treated `LLED 402` (cited only
  as a corequisite, no independent course description) as equivalent to
  the already-cataloged `LLED 302`, same title.
- **`WFED-2026.json`** — Workforce Education and Development B.S.,
  Industrial Education specialization (of four: Industrial Education,
  Health Occupations Education, Occupational Home Economics Education,
  Industrial Training). The entire `WFED` department catalog was
  missing — added a new `wfed_catalog.json`. Real bug fixed: the
  bulletin's own suggested plan schedules `WFED 441` (Year 2 Fall) before
  `WFED 445` (Year 3 Spring), but `WFED 441` strictly requires `WFED 445`
  completed first — reordered to the correct prerequisite sequence rather
  than the bulletin's own internally-contradictory one.

With 2 of the remaining 4 majors blocked, sourced 3 replacement majors
from a newly-opened college (Arts and Architecture) to keep the batch at
5:

- **Architecture, B.S. (ARCBS)** — blocked, not built. Enrollment is
  restricted to internal transfers from the B.Arch program (not directly
  enrollable), and its own Suggested Academic Plan PDF contains no course
  table at all — same "PSU never published a real sequence" gap as
  Environmental Engineering / IST-BS. Logged in `BLOCKED_MAJORS.md`.
  Built `Architecture, B.Arch.` instead — the actual direct-entry
  professional program, which does have a full plan.
- **`ARCHBARCH-2026.json`** — Architecture, B.Arch. The first **5-year,
  10-semester** program built this session (every other major so far is
  8-semester/4-year); required a `grad_years=5` override, including a new
  exception in `TestHistoricalCatalogYears`'s generic all-majors test
  (previously hardcoded `grad_years=4` for everything on disk). New
  catalogs: `arch_catalog.json`, `arth_catalog.json`, `ae_catalog.json`
  (all previously missing). Real engine-mechanics gap found: the
  bulletin describes several course pairs/trios as *mutually* concurrent
  with each other (`ARCH 121`/`131`, `ARCH 122`/`132`, `ARCH 203`/`231`,
  `ARCH 204`/`232`, `ARCH 332`/`381`/`480`, `ARCH 499A`/`B`/`C`) — the
  engine's same-term scheduling can only resolve *one-directional*
  concurrency (course B already picked before course A is scanned in a
  later pass), so true mutual/circular concurrent requirements had to be
  broken into one-directional edges, or dropped entirely for the
  `ARCH 499A/B/C` Rome-semester trio (which share a common prereq gate
  instead). `AE 211`, cited as an `ARCH 331` concurrent requirement,
  could not be confirmed to exist anywhere in the current PSU catalog and
  was not modeled — documented directly in `ARCH 331`'s entry rather than
  guessed at.
- **`ARTH-2026.json`** — Art History B.A. Extended `arth_catalog.json`
  with `ARTH 1S`, `ARTH 111`, `ARTH 101N`, `ARTH 350W`. The bulletin's own
  9-credit "Additional Courses" requirement must include one Western and
  one non-Western art course — filled with `ARTH 111` (Western) and
  `ARTH 101N` (non-Western) instead of leaving both generic, same
  precedent as picking real courses over generic slots wherever the
  bulletin names a specific constraint.
- **`GD-2026.json`** — Graphic Design B.Des. New `gd_catalog.json` for
  the entire GD department (18 courses) — clean, fully-specified prereq
  chain data straight from the bulletin. The bulletin's own "GD 300,
  315, 320, or 400" pool item repeats 3 times across Semesters 6-8 (one
  required completion each term) — relies on the engine's tiered option
  ranking to naturally advance to a different option each time a prior
  one is completed, since by Semester 6 all four options are
  simultaneously prereq-eligible. `GD 495` (Internship, repeatable
  1-18cr) is scheduled 3 times — first as a literal course pick, the
  other two as generic repeat slots, same convention as `ARCH 491`'s
  repeatable-studio modeling earlier in this batch.

This **closes the College of Education** (7 of 9 majors built — the
original 5 plus `MLED`/`WFED` from this batch — the remaining 2,
Elementary and Kindergarten Education and World Languages (K-12)
Education, are permanently blocked on program holds, not left for a
future batch) and opens Arts and Architecture (3 of 21 majors:
`ARCHBARCH`, `ARTH`, `GD`; 1 blocked). All five attempted this batch
passed at 0 warnings / `goal.met = True`. 311 → 326 backend tests.

---

### Arts and Architecture, second batch (2026-08-12)

Attempted 5 more Arts and Architecture majors. Two hit a genuine
data-ambiguity wall and are blocked, not built:

- **Art, B.A.** and **Art, B.F.A.** — both require 15-24 credits from
  one of five Areas of Concentration (ceramics, drawing and painting,
  new media/digital arts, photography, sculpture), and the bulletin's
  own program requirements page states explicitly that it does not list
  course codes for any concentration, directing students to LionPATH or
  an adviser instead. Most of the rest of each curriculum *is*
  concretely specified (ART 11/110/111/122Y, ARTH 111/112, an enumerated
  "Additional/Beginning-Level Studio" course menu) — only the
  concentration-specific block is unresolvable, the same shape of gap as
  Psychology. Logged in `BLOCKED_MAJORS.md`.

The other three built cleanly:

- **`AED-2026.json`** — Art Education B.S. New catalogs: `aed_catalog.json`
  (entire AED department), `art_catalog.json` (`ART 11`/`110`/`111`/
  `122Y`), `aplng_catalog.json` (`APLNG 200`/`210`). Real gap fixed:
  `AED 489` requires `AED 490` as an enforced concurrent, but the
  bulletin's own Suggested Academic Plan never schedules `AED 490`
  anywhere — added it as an explicit companion item. Real
  engine-mechanics gap (same pattern as Architecture B.Arch): `AED
  495A`/`495B` and `AED 495C`/`495D` are each mutual corequisites of each
  other — broken into one-directional edges, same fix pattern as ARCH's
  mutual pairs.
- **`LARCH-2026.json`** — Landscape Architecture B.L.A. The **second
  5-year professional program** built this session — 9 semesters, 139
  total credits, ending Fall of Year 5 rather than Spring (unlike
  Architecture B.Arch's 10-semester/Spring-Y5 finish). New
  `larch_catalog.json` for the entire LARCH department (26 courses).
  Same mutual-corequisite engine gap hit again: `LARCH 115`/`155`,
  `116`/`156`, `215`/`255`, `216`/`256` are each listed as mutual
  corequisites — broken into one-directional edges. `LARCH 414` (5-15cr,
  repeatable) is scheduled 3 times, matching the `ARCH 491`/`GD 495`
  repeatable-course convention. A 3-credit gap between the bulletin's
  own Semester 2 header total (17cr) and its itemized course list
  (14cr) was closed with one additional generic Gen Ed slot — the
  resulting 9-semester total (139cr) exactly matches the bulletin's
  stated program total, confirming the reconciliation was fair rather
  than a guess.
- **`DMD-2026.json`** — Digital Multimedia Design B.Des. (online-only,
  World Campus). New catalogs: `dart_catalog.json`, `dmd_catalog.json`;
  extended `comm_catalog.json` and `art_catalog.json`. Real bug found:
  `COMM 230W`'s actual prereq is `ENGL 15` and `ENGL 202`, but the
  bulletin's own suggested plan schedules it in Semester 2 — before
  `ENGL 202` appears in Semester 5. Left it in its bulletin-labeled slot
  and confirmed the simulator naturally defers it to the correct later
  real term, the same reordering behavior already relied on for
  `GD 495`.

Arts and Architecture is now 6 of 21 majors built (`ARCHBARCH`, `ARTH`,
`GD`, `AED`, `LARCH`, `DMD`), 3 blocked (`ARCBS`, `Art B.A.`,
`Art B.F.A.`). All five attempted this batch resolved at 0 warnings /
`goal.met = True`. 326 → 335 backend tests.

---

### Arts and Architecture, third batch (2026-08-12)

Attempted the remaining 5: 4 built cleanly, 1 blocked.

- **`PPHOTO-2026.json`** — Professional Photography B.Des. New catalogs:
  `photo_catalog.json` (entire PHOTO department), `aa_catalog.json`
  (`AA 1`, the college's first-year seminar, reused by later builds
  too). Entrance to Major (`PHOTO 200`/`202` or a portfolio review) is
  satisfied directly since `PHOTO 202` is a Semester 1 item.
- **`DAMD-2026.json`** — Digital Arts and Media Design B.Des., Digital
  Art and Design Emphasis (of three tracks — the Animation track names
  no course codes for its sub-categories, the same shape of gap as Art
  B.A./B.F.A., so it was skipped in favor of the one fully-enumerated
  track). Greatly extended `dart_catalog.json` (2 → 24 courses). Real
  gap fixed: `ART 476` (a `DART 400` concurrent) requires 3cr of `ARTH`,
  but the bulletin's own plan never otherwise schedules an ARTH course —
  added `ARTH 111` as an explicit Semester 1 item.
- **`THEA-2026.json`** — Theatre B.A. New catalogs: `thea_catalog.json`,
  `dance_catalog.json`. Real bug fixed: `THEA 120` (Acting I) has a real
  prereq of `THEA 106` (among others), but the bulletin's own Semester 1
  schedules both together — added `THEA 106` to `THEA 120`'s
  `concurrent_groups` too, same fix pattern as `MATH 140B`/`CHEM 110`
  earlier this session. `DANCE 411`, one option in a repeated 7-course
  history pool, could not be confirmed to exist in the current catalog
  and was dropped from the modeled option list rather than guessed at.
- **`MUSIC-2026.json`** — Music B.A., General Music Studies Option (of
  two; Music Technology needs `INART`/`MATSE` catalog data not yet
  built). New `music_catalog.json`. Real engine-mechanics gap hit again
  (same pattern as Architecture B.Arch/AED/LARCH): `MUSIC 122` and
  `MUSIC 132` are mutual corequisites of each other — broken into
  one-directional edges.
- **Integrative Arts, B.A.** — blocked, not built. An even purer version
  of the Art B.A./B.F.A. gap: nearly the entire 42-credit major (~36
  credits across 12 "Art Area I"/"Art Area II" items) has zero course
  codes anywhere, because the bulletin states these selections depend on
  "the individual's academic plan submitted to the Department of
  Integrative Arts before admission" — there isn't even a fallback
  course menu the way Art B.A./B.F.A. at least have. Logged in
  `BLOCKED_MAJORS.md`.

Arts and Architecture is now 10 of 21 majors built (`ARCHBARCH`, `ARTH`,
`GD`, `AED`, `LARCH`, `DMD`, `PPHOTO`, `DAMD`, `THEA`, `MUSIC`), 4
blocked (`ARCBS`, `Art B.A.`, `Art B.F.A.`, `Integrative Arts`). All
five attempted this batch resolved at 0 warnings / `goal.met = True`.
335 → 346 backend tests.

---

### Arts and Architecture, fourth batch (2026-08-12) — closes the college

Attempted the remaining 7 performing-arts majors; picked 5, all built
cleanly (no blockers this batch — every remaining major turned out to
have real, enumerable course codes despite the audition/portfolio
entrance gates).

- **`MUSED-2026.json`** — Music Education B.M.E. Greatly extended
  `music_catalog.json` (14 → 32 courses), researched via a dedicated
  subagent given the scale of the department's course list. Real gap
  fixed: `MUSIC 240` underlies `MUSIC 295A`/`341`/`345`/`395A` as a
  prereq, but the bulletin's own Suggested Academic Plan never names it
  directly — it's the real course behind that plan's generic 'Education
  elective' item, added explicitly. Real gap fixed: `SPLED 400`
  (explicitly in the plan) needs `EDPSY 14`/`10`/`11` or `HDFS 229`/
  `239`, none of which the plan otherwise schedules — added `EDPSY 14`
  explicitly, same fix pattern as other majors reusing `SPLED 400`. Same
  mutual-corequisite engine gap hit again: `MUSIC 345`/`395B`.
- **`THEABFA-2026.json`** — Theatre B.F.A., Stage Management Option (of
  six: five Design/Technology emphases plus Stage Management — Stage
  Management chosen as the most self-contained). Real data artifacts
  found and fixed via direct department-PDF verification (not just the
  AI-summarized search page): the bulletin's approved-electives footnote
  has three typos — `THEA 405Y`/`407`/`408` should be `405W`/`407W`/
  `408W` — and cites a nonexistent `THEA 406`. `THEA 200`, listed in the
  plan's own Semester 2, could not be confirmed to exist anywhere in the
  department's course catalog (unlinked in the bulletin's own HTML,
  absent from the official course-description PDF) — replaced with a
  generic Gen Ed slot rather than guessed at. Real gap fixed: `THEA 270`
  needs `THEA 201W` and `THEA 252`, neither otherwise scheduled — added
  both explicitly.
- **`ACTING-2026.json`** — Acting B.F.A. Real data gap: `DANCE 361`'s own
  bulletin prereq cites `DANCE 262`, unconfirmable despite being
  referenced by two different courses' descriptions — substituted
  `DANCE 261` (confirmed real, immediately prior in the sequence,
  self-described as leading into `DANCE 262`) rather than guessed at.
- **`MUSTHEA-2026.json`** — Musical Theatre B.F.A. New
  `voice_catalog.json`. Real gap fixed: `DANCE 232` needs `DANCE 230`,
  never otherwise scheduled — added explicitly. Real gap fixed:
  `THEA 425A` needs concurrent `THEA 425C` — unlike Acting B.F.A. (which
  already schedules both), Musical Theatre's own plan never otherwise
  schedules `THEA 425C` — added explicitly. `DANCE 251` couldn't be
  independently confirmed despite being a real, active, cited code —
  title/credits inferred from the `DANCE 231`/`241`/`251` "Beginning X I"
  naming pattern rather than left unmodeled.
- **`MUSICBM-2026.json`** — Music B.M., Keyboard Instruments Option (of
  four: Composition, Keyboard, Strings/Winds/Brass/Percussion, Voice —
  Keyboard chosen for maximum reuse of already-cataloged `MUSIC` courses
  and a fully-named Applied Music sequence). New `keybd_catalog.json`
  for the 8-course `KEYBD` applied-piano sequence, confirmed to have no
  enforced inter-level prereqs (placement is by audition/jury, not
  encoded in the bulletin's prereq fields).

Arts and Architecture is now 15 of 21 majors built, 4 blocked; 2 remain
unattempted (Music Technology B.M., Musical Arts B.M.A.). All five
attempted this batch passed at 0 warnings / `goal.met = True`.
346 → 360 backend tests.

---

### Closing Arts and Architecture; opening Liberal Arts (2026-08-12)

Attempted the last 2 Arts and Architecture majors, then opened Liberal
Arts (58 majors, the largest college — only Economics, Political
Science, and blocked Psychology had been touched before this batch).

- **`MUSTECH-2026.json`** — Music Technology B.M. New
  `inart_catalog.json` (`INART 50`/`258A`); extended `music_catalog.json`
  and `thea_catalog.json`. `MUSIC 452`'s own bulletin prereq text cites
  `INART 50Z`, unconfirmable as a course distinct from `INART 50` —
  treated as `INART 50`. `MUSIC 177` (ROARS lab) is scheduled once per
  semester across all 8 semesters for a cumulative 8cr, exactly matching
  the bulletin's own stated total.
- **Musical Arts, B.M.A.** — blocked, not built. This degree pairs music
  performance with a student-chosen "Other Area of Study" outside music
  entirely (24cr, minimum 12 at 400-level) individually approved by the
  Dean of Undergraduate Studies — zero course codes possible since the
  secondary field could be any department at Penn State. Same shape of
  gap as Integrative Arts, B.A. Logged in `BLOCKED_MAJORS.md`. **This
  closes the College of Arts and Architecture's attempt list** — all 21
  majors now either built (16) or blocked (5), none left unattempted.
- **`HIST-2026.json`** — History B.A. Extended `hist_catalog.json` with
  `HIST 1`/`2`/`302W`. `LA 283`, named in the bulletin's own plan, could
  not be confirmed to exist anywhere in the LA department's course
  listing — replaced with a generic Second-Year Liberal Arts Seminar
  slot rather than guessed at. `HIST 100/200-level` and `HIST 400-level`
  are open department-level pools with no bulletin-enumerated list —
  modeled generically, a normal open-elective structure rather than a
  data-ambiguity wall.
- **`CRIM-2026.json`** — Criminology B.A. Real bug fixed in the shared
  `crim_catalog.json` (benefits any future major reusing it): `CRIM 249`
  and `CRIM 250W` had empty `prereq_groups` despite the bulletin's own
  "Critical Sequencing Note" (`CRIM 12`/`SOC 12` → `CRIM 249` →
  `CRIM 250W` MUST be followed) — added the real prereq/concurrent
  chains sourced directly from the department's own course pages;
  verified this doesn't affect any other existing plan.
- **`SOCBA-2026.json`** — Sociology B.A. Real gap fixed in the shared
  `soc_catalog.json`: `SOC 400W` had no `prereq_groups` despite the
  bulletin's own capstone sequence (`SOC 207` → `SOC 470` → `SOC 400W`)
  requiring `SOC 470` — added. `SOC 207`/`405`'s bulletin prereq text
  ("3 credits in SOC") was approximated as `SOC 1` specifically, since
  the schema needs a concrete course code. Verified this doesn't
  regress `EDPP-2026.json`, which also references `SOC 207`.

Liberal Arts is now 5 of 58 majors built (`ECON`, `PLSC`, `HIST`,
`CRIM`, `SOCBA`), 1 blocked (`Psychology`, carried over from earlier in
the session). All five majors attempted this batch resolved at 0
warnings / `goal.met = True`. 360 → 370 backend tests.

---

### Liberal Arts, second batch (2026-08-12)

Caught a real process gap before it became a wasted build: started
researching English, B.A. as one of this batch's five, only to
discover it had already been built earlier in the session (under major
code `ENGL`, part of the original 16-major historical-catalog-years
expansion, not the Liberal-Arts-specific batches) — the duplicate
`ENGLBA-2026.json` file was deleted before it was wired into tests or
aliases. Cross-checked the other four candidates against every
existing plan file's title before building to confirm no further
overlap, then substituted African American Studies as the fifth major.

- **`PHILBA-2026.json`** — Philosophy B.A., General Philosophy Option
  (of six — Humanities and Arts, Philosophy of Science and Mathematics,
  Social Sciences, Professional Studies, Justice/Law/Values are the
  others). Unlike Art B.A./B.F.A.'s blocked concentrations, all six
  options here name real, enumerated course pools (e.g. `{PHIL 401,
  402, 409, 413, 424, 435}`), so this was never a data-ambiguity wall.
  All PHIL courses used were already fully cataloged from an earlier
  build.
- **`ANTH-2026.json`** — Anthropology B.A. New `anth_catalog.json`.
  Judgment call: the bulletin's own Semester 1 item is "ANTH 45N or
  21," but Semester 2 separately and specifically requires "ANTH 21"
  — since a completed course can't satisfy two distinct requirements,
  modeled Semester 1 as literal `ANTH 45N` (one of the two bulletin
  options) to avoid the conflict.
- **`LING-2026.json`** — Linguistics B.A. New `ling_catalog.json`
  (`LING 100`/`402`/`404`/`449`).
- **`CASBA-2026.json`** — Communication Arts and Sciences B.A. All
  literal courses (`CAS 101N`/`301`/`303`/`304`/`311`) were already
  fully cataloged. Computed 8-semester total (123cr) matches the
  bulletin's own stated total exactly.
- **`AFAM-2026.json`** — African American Studies B.A. New
  `afam_catalog.json`; added `HIST 152` (cross-listed with `AFAM 152`)
  to `hist_catalog.json`. Real gap avoided: `AFAM 401` strictly
  requires both `AFAM 100N` AND `AFAM 101N`, but the bulletin's own
  Semester 2 item is a 7-option pool spanning AFAM/WMNST/SOC courses —
  a wrong pick would leave `AFAM 401` permanently unsatisfiable, so
  simplified to a literal `AFAM 101N` pick rather than modeling the
  full pool. Real gap fixed: `SOC 207` needs `SOC 1` (per this batch's
  earlier `SOC` catalog fix), never otherwise scheduled in this major's
  own plan — added explicitly.

A repeated "LA 283" citation across nearly every Liberal Arts major's
Suggested Academic Plan this batch and last (History, Criminology,
Sociology, Linguistics, Communication Arts and Sciences, African
American Studies) could not be confirmed to exist in the LA
department's own course listing (unlike LA 83, which is real) —
consistently modeled as a generic Second-Year Liberal Arts Seminar
slot across every affected plan rather than guessed at.

Liberal Arts is now 10 of 58 majors built, 1 blocked (`Psychology`).
All five majors attempted this batch resolved at 0 warnings /
`goal.met = True`. 370 → 381 backend tests.

---

### Liberal Arts, third batch (2026-08-12)

Attempted 5 more; 3 built, 2 hit the same "real courses, no published
ordering" or "no course codes at all" walls seen earlier this session.

- **Law and Society, B.A.** — blocked, not built. World Campus program;
  the bulletin has no Suggested Academic Plan section at all (confirmed
  via two fetches), only Prescribed/Additional/Supporting course
  categories with real codes — same shape of gap as Environmental
  Engineering and IST-B.S. earlier in this session. Logged in
  `BLOCKED_MAJORS.md`.
- **`INTPOL-2026.json`** — International Politics B.A., International
  Political Economy Option (of three: IPE, International Relations,
  National Security — IPE chosen to avoid the SRA/CRIM catalog
  dependencies the other two need). All PLSC/ECON courses used were
  already fully cataloged.
- **Global and International Studies, B.A.** — blocked, not built. 21
  of the major's credits come from five named "Pathways" (Human
  Rights, Culture and Identity, Global Conflict, Wealth and Inequality,
  Health and Environment), and the bulletin states outright that
  Pathway course lists are kept on the department's own website
  (glis.la.edu), not in the bulletin — the same "named options, zero
  course-code data" gap as Psychology and Art B.A./B.F.A. Logged in
  `BLOCKED_MAJORS.md`.
- **`OLEAD-2026.json`** — Organizational Leadership B.A. New
  `olead_catalog.json`, `lhr_catalog.json`. Confirmed the suggested
  `OLEAD 100 -> 201 -> 210 -> 464 -> 465` sequence is not an enforced
  prereq chain — each course's real prereq is either none or a
  semester-standing gate, not the prior OLEAD course.
- **`LHR-2026.json`** — Labor and Human Resources B.A., University Park
  & World Campus track. Extended `lhr_catalog.json` (built for
  Organizational Leadership in this same batch) with 5 more courses.
  Bulletin explicitly states "LHR 304, LHR 305, and LHR 312 may be
  taken in any order" — confirmed no artificial sequencing was needed.

Also worth noting: mid-batch, a routine cross-check against existing
plan titles caught that English, B.A. (originally on this batch's
list) had already been built in an earlier session batch under a
different major code (`ENGL`, from the historical-catalog-years
expansion) — the duplicate build was discarded before touching tests
or aliases, and African American Studies was substituted in that slot
(see the previous batch's write-up above).

Liberal Arts is now 13 of 58 majors built, 3 blocked (`Psychology`,
`Law and Society`, `Global and International Studies`). All five majors
attempted this batch resolved at 0 warnings / `goal.met = True` (where
built). 381 → 387 backend tests.

---

### Liberal Arts, fourth batch (2026-08-12) — languages + Social Data Analytics

Attempted 5 more; all five built cleanly, no blockers this batch.

- **`SPANBA-2026.json`** — Spanish B.A. New `span_catalog.json`.
  `SPAN 1 -> 2 -> 3` is a strict linear prereq chain; `SPAN 100`
  (standard) and `SPAN 100A`/`100B` (heritage-speaker/medical-Spanish
  tracks) run in parallel, each gated on `SPAN 3` or placement, per the
  bulletin's own note that heritage/native speakers take the A-suffixed
  courses instead. `SPAN 215` has no unsuffixed catalog entry — only
  `SPAN 215N`/`215Q` exist. `SPAN 100C`/`100H`, cited as prereq
  alternatives, could not be confirmed as standalone courses (absent
  from the department's full listing) — not modeled.
- **`FRENCHBA-2026.json`** — French and Francophone Studies B.A.,
  Language and Culture Option (of three — the other two describe
  requirements only in prose, no detailed semester grid). New
  `fr_catalog.json`. Notably, unlike Spanish's coded chain, French's
  `FR 1`/`2`/`3` have **no** coded prerequisite at all — confirmed via
  direct DOM inspection, not a fetch gap.
- **`GERBA-2026.json`** — German B.A. New `ger_catalog.json`. Unlike
  French, German's `GER 1 -> 2 -> 3` **is** a formally coded prereq
  chain (same pattern as Spanish). Two real data artifacts fixed via
  direct DOM inspection: the bulletin's own plan cites `GER 200`, which
  no longer exists (current course is `GER 200N`); `GER 208Y`, an
  alternative to `GER 201`, could not be confirmed to exist at all and
  was dropped.
- **`CMLIT-2026.json`** — Comparative Literature B.A. New
  `cmlit_catalog.json` (`CMLIT 10`/`100`/`400Y`).
- **`SODA-2026.json`** — Social Data Analytics B.S. New
  `soda_catalog.json` (`SODA 308`/`496`) — every other course used
  (`MATH`/`CMPSC`/`PLSC`/`IST`/`STAT`/`DS` courses) was already fully
  cataloged from earlier majors, an unusually clean build reusing five
  departments' worth of existing data.

This batch established a useful pattern for PSU's language majors:
check each language's elementary sequence for a coded prereq chain
before assuming one — Spanish and German enforce it, French doesn't,
and none of that is guessable without checking the live bulletin DOM
directly (WebFetch's summarized pass silently drops prerequisite text
for some collapsed course entries).

Liberal Arts is now 18 of 58 majors built, 3 blocked. All five majors
attempted this batch resolved at 0 warnings / `goal.met = True`.
387 → 397 backend tests.

---

### Liberal Arts, fifth batch (2026-08-12) — more languages + area studies

Attempted 5 more; all five built cleanly, no blockers this batch.

- **`ITBA-2026.json`** — Italian B.A. New `it_catalog.json`. Confirmed
  `IT 1 -> 2 -> 3` is a coded prereq chain (matching Spanish/German, not
  French).
- **`RUSBA-2026.json`** — Russian B.A. New `rus_catalog.json`. Confirmed
  `RUS 1 -> 2 -> 3` is coded. Real data artifact: the bulletin's own
  Suggested Academic Plan cites `RUS 400` as a literal course, but it
  doesn't exist anywhere in the department's listing (absent from the
  full catalog, surfacing only as a bare cross-reference inside other
  courses' concurrent text) — treated as a stale placeholder for "a
  400-level Russian course" and modeled generically, same as the
  bulletin's own separate "400-Level Russian" items.
- **`WMNSTBA-2026.json`** — Women's, Gender, and Sexuality Studies B.A.
  New `wmnst_catalog.json`. Real data artifact: `WMNST 83S` doesn't
  exist (only `WMNST 83N` does) — substituted. Several downstream
  prereq strings in the department's own PDF contain literal typos
  (`WMST 106`, `WMNST005`, `WMNST001`) — normalized to their evident
  intent rather than transcribed literally. `WMNST 492W`'s real prereq
  needs `WMNST 400N` specifically, but the plan's own Semester 5 item
  pools `400N`/`401` as interchangeable — modeled `492W`'s prereq to
  accept either, matching the plan's own treatment.
- **`CAMS-2026.json`** — Classics and Ancient Mediterranean Studies
  B.A., CAMS Option (of three — Ancient Languages needs specific
  ancient-language catalog data not yet built, and Ancient
  Mediterranean Archaeology requires fieldwork the schema can't model,
  so CAMS was the cleanest). New `cams_catalog.json`.
- **`JST-2026.json`** — Jewish Studies B.A. New `jst_catalog.json` and
  `hebr_catalog.json` (`HEBR 1/2/3`, a coded chain matching the other
  language departments this session). Computed 8-semester total (123cr)
  matches the bulletin's own stated total exactly.

The PSU-language-department pattern established two batches ago held
again: Spanish, German, Italian, Russian, and Hebrew all enforce coded
elementary-sequence prerequisites; French remains the outlier with none
at all. Every language build this session has required checking this
individually via direct DOM inspection rather than assuming either way.

Liberal Arts is now 23 of 58 majors built, 3 blocked. All five majors
attempted this batch resolved at 0 warnings / `goal.met = True`.
397 → 407 backend tests.

---

### Liberal Arts, sixth batch (2026-08-13) — sibling B.A./B.S. degree pairs

Attempted 5 more; all five built cleanly, no blockers this batch. Four of
five are sibling B.A./B.S. pairs of majors already built earlier in the
college — reusing the existing department catalog and diffing the two
bulletin pages against each other for the real structural differences
turned out to be a much faster build than starting from scratch.

- **`CHNSBA-2026.json`** — Chinese, B.A. New `chns_catalog.json` (18
  courses). Confirmed `CHNS 1 -> 2 -> 3 -> 110 -> 401 -> 402 -> 403W ->
  404` is a fully linear coded prereq chain, matching every other PSU
  language department checked this session except French. Real data
  artifact: the bulletin's own Suggested Academic Plan cites a
  `452/453/454/455` pool, but `CHNS 455` doesn't exist anywhere in the
  department's course listing — modeled as a 3-way pool.
- **`ECONBA-2026.json`** — Economics, B.A., sibling of the already-built
  Economics B.S. (`ECON-2026.json`). Real difference: the B.A. drops the
  B.S.'s `MATH 110/140`/`CMPSC` requirements for a 3-course World
  Language sequence plus 9cr of B.A. Fields and a World Cultures course.
  `ECON 106` still needs a `MATH` prereq even though the B.A. itself
  doesn't require calculus — `MATH 21` (real, no-prereq, GQ-satisfying)
  scheduled explicitly to resolve it without pulling in `MATH 110/140`.
- **`PLSCBA-2026.json`** — Political Science, B.A., sibling of the
  already-built Political Science B.S. (`PLSC-2026.json`). Same World
  Language swap as Economics. Real data artifact confirmed via direct
  DOM inspection of both the Suggested Academic Plan and the PLSC
  course-description listing: the bulletin's own SAP cites `PLSC 3,
  PLSC 20, or PLSC 22`, but `PLSC 20` and `PLSC 22` don't exist anywhere
  in the department's course listing (only substring matches like
  `PLSC 200N`/`PLSC 220` exist, not the bare codes) — modeled as literal
  `PLSC 3` only, the same stale-citation pattern as `RUS 400`/`GER 208Y`.
- **`PHILBS-2026.json`** — Philosophy, B.S., sibling of the already-built
  Philosophy B.A. (`PHILBA-2026.json`, six named options) — the B.S. has
  no options/concentrations, one straightforward track. **Caught and
  fixed a real engine-interaction bug during verification**: the
  bulletin's own "Formal Reasoning" pool (`CMPSC`/`ECON`/`IST`/`MATH`/
  `RM`/`SC`/`SRA`/`STAT`, used twice, 6cr) is almost entirely gated
  behind a `MATH 110/140` prereq or concurrent that this plan otherwise
  never schedules; the one exception, `CMPSC 111` (a real but only
  1-credit "Logic for CS" course with no prereqs), was reused for both
  Formal Reasoning slots and caused the engine to reschedule the same
  1cr course forever — `plan_progress`'s one-completed-course-per-item
  rule means a single already-completed course can never satisfy a
  second plan item, so with no second distinct eligible option the
  simulation just kept re-picking `CMPSC 111` every remaining term until
  hitting the 24-term cap. Root cause wasn't a data mistake, it was a
  plan-design mistake: reusing one identical multi-option OR-pool across
  multiple item slots is only safe when at least as many *genuinely
  eligible* (prereq/concurrent-satisfiable given what's actually
  scheduled elsewhere in the plan) distinct options exist as there are
  reuses. Fixed by scheduling `MATH 110` explicitly (Semester 1, also
  satisfies GQ) so `CMPSC 131` and `STAT 184` both become real, distinct,
  completable options — added a regression test
  (`test_formal_reasoning_pool_has_two_distinct_completable_options`)
  asserting both appear in the built plan.
- **`SOCBS-2026.json`** — Sociology, B.S., sibling of the already-built
  Sociology B.A. (`SOCBA-2026.json`). Real difference: `MATH`/`STAT`/one
  programming course, plus a 15cr "Supporting Courses" Pathway (5 named
  options, each with real enumerated course codes — Data Analysis,
  Geographic Information Systems, Social Demography, Political Analysis,
  Health and Society — so not a data-ambiguity wall, same "multi-track
  with real codes" pattern as Philosophy B.A.'s six options). Picked
  Data Analysis and, applying the lesson just learned from
  `PHILBS-2026.json`, used 5 *distinct* single-course pathway slots
  (`CMPSC 203 -> MATH 220 -> DS 220 -> DS 402 -> STAT 460`) sequenced so
  every real prereq is satisfied by an earlier semester, rather than one
  reused OR-pool.

Liberal Arts is now 28 of 58 majors built, 3 blocked. All five majors
attempted this batch resolved at 0 warnings / `goal.met = True`.
407 → 418 backend tests.

---

### Liberal Arts, seventh batch (2026-08-13) — B.S. siblings of the four language majors + Criminology

Attempted 5 more, all sibling B.S. builds of majors already on file; all
five built cleanly, no blockers this batch.

- **`CRIMBS-2026.json`** — Criminology, B.S., Computing and Statistics
  Option (of four named options, all with real course codes — Business/
  Public Administration, Computing and Statistics, Legal Studies, Social
  Science Research — picked for cleanest catalog reuse). Real hidden-
  prereq chain handled explicitly: the option's own prescribed `SOC 470`
  needs `SOC 207`, not otherwise scheduled anywhere in CRIM — added
  `SOC 207` explicitly, and narrowed the common `SOC 1/3/5` requirement
  to literal `SOC 1` so the `SOC 1 -> SOC 207 -> SOC 470` chain resolves
  deterministically regardless of which option the engine picks first.
- **`FRENCHBS-2026.json`** — French and Francophone Studies, B.S.,
  Applied French Option (of three — French-Engineering needs a mandatory
  study-abroad semester, French-Business needs Smeal-specific courses).
  Added `FR 401/409/417/418/419` to `fr_catalog.json` with real prereqs
  confirmed via direct DOM inspection.
- **`GERBS-2026.json`** — German, B.S., Applied German Option (of three,
  same reasoning as French). Added `GER 399/431/432/499` to
  `ger_catalog.json`. Real data artifact: `GER 432`'s own bulletin prereq
  text cites `GER 401`, which doesn't exist as a standalone course (only
  `GER 401Y` does) — same stale-citation pattern as `RUS 400`/`PLSC 20/22`.
- **`ITBS-2026.json`** — Italian, B.S. Unlike French/German (three named
  options each), Italian's B.S. has **no named options at all** — one
  straightforward track, the same shape as Philosophy B.S. Added `IT 412`
  (prescribed) and `IT 99`, a real variable-credit (1-12, max 12),
  no-prereq study-abroad course — modeled as a single literal 6cr pick
  rather than a generic slot, since it's a real enumerated course
  satisfying the bulletin's own "minimum 6 credits in a Penn State
  education abroad program in Italy" requirement.
- **`SPANBS-2026.json`** — Spanish, B.S., Applied Spanish Option (of two
  — Business needs Smeal-specific courses). Added 9 courses to
  `span_catalog.json` with real prereqs confirmed via direct DOM
  inspection; several (`SPAN 314/411/417`) cite the bulletin's own stale
  `SPAN 215` (only `SPAN 215N` exists), the same pattern hit repeatedly
  across this college.

**Real engine-interaction bug caught last batch, applied as a design rule
this batch:** reusing one identical multi-option course pool across
multiple plan items is only safe when at least as many genuinely
prereq/concurrent-eligible distinct options exist as there are reuses
(see `TestPhilosophyBSPlan`'s regression test). Every "Related area" /
"Applied Option" / "Supporting" pool this batch that needed 2+ picks used
either a large enough pool of mutually-interchangeable options (the
French/German/Italian/Spanish culture-literature pools, 4-5 wide) or
distinct literal single-option items (Criminology's and the language
majors' 400-level picks) — never a narrow 2-3-option pool reused more
times than it has genuinely eligible members.

Liberal Arts is now 33 of 58 majors built, 3 blocked. All five majors
attempted this batch resolved at 0 warnings / `goal.met = True`.
418 → 428 backend tests.

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

### Expansion to all 18 majors (2026-08-11)

CMPSC and PREMED had 5 catalog years each; the other 16 majors (everything
shipped in §1's Smeal batch, plus Nursing/English/Business/Cybersecurity/
Math/Biology) only had the current 2026-27 plan. Aarush asked for the same
back-referencing depth across all of them, pointing at
`bulletins.psu.edu/archive/` as the source. 16 parallel research agents each
diffed one major's 2022-23 through 2025-26 archived editions against its
current plan.

One URL-format trap: the archive path is `archive/{YYYY}-{YYYY}/...` — full
4-digit years on both sides (`2025-2026`), not the 2-digit `2025-26` shorthand
the live bulletin sometimes displays. Guessing the 2-digit form 404's even for
majors that do have older archives; confirmed the real pattern by extracting
every link on the archive index page with a `document.querySelectorAll('a')`
JS pull. (The first batch of 16 research agents also hit a session token
limit mid-run — by the time that was checked, the reset window had already
passed, so they were simply relaunched rather than scheduled for later.)

**Triage principle**: this plan's JSON schema only models
`type`/`options`/`credits`/`gen_ed`/`etm` — it doesn't represent GPA entrance
cutoffs, footnote prose, elective-pool renames ("Two-Piece Sequence" →
"Business Breadth Course"), campus-availability lists, or Gen-Ed
domain-structure prose. Every year-over-year difference the research agents
reported that was limited to those categories is invisible to the schema, so
that year just reuses the current 2026 plan verbatim (`catalog_year` changed,
nothing else). Applying that filter meant most of the work was catalog-year
copies, not new plan structures — only a real course-code, credit-count, or
requirement change earned a distinct JSON:

- **ENGL** 2022-24: no `LA 83`/`LA 283` (123 total credits, not 126).
- **NURS** 2022-23: one extra 400-level NURS "Supporting Course" slot.
- **BUSINESS** 2022-23: fixed `MIS 204` (no 250 alternate), a narrower MATH
  entrance menu, `ACCTG 495` fixed at 6 credits (not a 3-6 range).
- **CYBER** 2022-24: `SRA 221`/`IST 451`/`454`/`456` — the parallel
  `CYBER`-prefix cross-listings didn't exist yet.
- **FIN** 2022-24: a 7-course elective pool (6 cr / 2 picks), not the current
  10-course/9-cr/3-pick pool.
- **BIOL** 2022-23: `MATH 141` added as a common prescribed course; plain
  `CHEM 213` (no `213W`/`213M` alternate yet).
- **MATH** 2022-23: the intro-programming choice was `CMPSC 101/121/201`
  only (not today's 5-course menu).
- **ACTSC** 2022 only: a materially different Risk Management sequence —
  `RM 411`/`412` straight-prescribed instead of an elective pair, no
  `RM 421`, `STAT 414` standalone. Checked each substitute course's
  prerequisites against the flowchart position before finalizing (`RM 420`
  needs `RM 412`, not yet complete at that point — so the item lists
  `RM 401` first, the alternative that's always satisfiable there).
- **BAIS**: only 2025-26 exists — before that the major was named
  "Management Information Systems, B.S.", a different plan not built here.

`MGMT`, `ACCTG`, `CIE`, `SCM`, `MKTG`, `REST`, and `RM` needed zero new
variants — every reported difference across all 4 archived years fell into
the cosmetic bucket above.

All 87 resulting (major, year) plan files simulate at 0 warnings /
`goal.met = True`. One legitimate (not a bug) surprise: `ENGL-2022/2023/2024`
finish in 7 simulated terms instead of 8 — verified by inspecting each term's
credit total (17.5, 17.5, 16.0, 18.0, 18.0, 18.0, 18.0 = 123, the reduced
total after dropping `LA 83`/`LA 283`); the lighter curriculum genuinely lets
the greedy scheduler finish half a term early.

One real gap caught during verification, not before: `FIN-2025.json` was
missing entirely from the first build pass (a plain omission from the
copy-script's year list). Caught only because `TestHistoricalCatalogYears`
was rewritten to discover every `(major, year)` pair from the files actually
on disk (`glob.glob(".../degree_plans/*.json")`) rather than iterating a
hardcoded CMPSC/PREMED year list — so a missing file now fails the test
instead of silently never being checked. That rewrite is what took the
historical-years test from 10 subTest cases to 87.

104 backend tests → 105 (the count held near-flat because this was mostly a
data expansion inside one already-parameterized test, not new test classes).

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

## 4. General Education course fulfillment — ✅ shipped

### User story

> As a student with open GEN ED slots in my plan, I want the planner to tell
> me which specific courses satisfy each requirement (GWS, GQ, GN, GS, GHW,
> GA, GH...) instead of just showing a generic "GEN ED (3 cr)" placeholder.

### Research findings

Aarush pointed the agent at `genedplan.psu.edu` — a PSU tool requiring a
Penn State login (Aarush logged in himself; the agent can't authenticate
on a user's behalf). Once in, it laid out PSU's exact Gen Ed structure:

- **Foundations** (15 cr): Quantification (GQ, 6 cr), Writing/Speaking
  (GWS, 9 cr) — C-or-better required, Inter-Domain courses can't be used here.
- **Knowledge Domains / Integrative Studies** (30 cr): Inter-Domain (6 cr),
  Arts (GA, 3 cr), Health & Wellness (GHW, 3 cr), Humanities (GH, 3 cr),
  Natural Sciences (GN, 3 cr), Social & Behavioral (GS, 3 cr), Exploration
  (9 cr, must include ≥3 GN, remainder any GA/GH/GN/GS/Inter-Domain).
- **Cultural Diversity** (6 cr): International Cultures (IL, 3 cr), US
  Cultures (US, 3 cr).
- **The "Firewall" rule**: a course sharing your major's own department
  prefix can't count as Gen Ed, except Inter-Domain/Integrative Studies,
  which is explicitly exempt.

Each category's "Course Search" button links straight to a public bulletin
page (`bulletins.psu.edu/undergraduate/general-education/course-lists/*`)
listing every approved course — no login required, same `<table
class="sc_courselist">` structure as the department catalogs already
scraped for majors. Confirmed scope with Aarush directly: full
course-level recommendations across all 10 categories (not just the credit
structure), with the Firewall rule enforced.

### What shipped

- **`Backend/scripts/scrape_gen_ed.py`** — scraped all 10 domains into
  **`Backend/data/gen_ed_courses.json`** (~4,460 courses, 532KB).
- **`planner_engine.py`**: `load_gen_ed_courses()` + `_pick_gen_ed_course()`
  pick a real, eligible course for any `GEN ED` slot tagged with a `gen_ed`
  domain (a single code, or a short list for combined slots like CYBER's
  "GA/GH"). The Firewall exclusion uses `plan["major"] in
  plan["departments"]` rather than `departments[0]`, since several majors
  (Actuarial Science, Business Analytics, Business, Corporate Innovation,
  Premed, Real Estate) have no dedicated course prefix at all — matching
  PSU's actual policy, where the rule simply doesn't apply to those majors.
- **`Backend/scripts/tag_gen_ed_slots.py`** — migrated 62 existing
  `"GEN ED (...)"` slots across 14 plans to carry the structured `gen_ed`
  field. Bare, domain-less `"GEN ED"` slots were left untagged on purpose —
  guessing the wrong domain risked violating a plan's real per-category
  credit distribution.

Two real engine bugs surfaced wiring this in:

- A Gen Ed slot resolved to a real course was never marked done in
  `plan_progress` (only the picked course's *code* landed in
  `sim_completed`, not the plan item itself in `consumed_slots`), so the
  engine re-picked a new course for the same slot every term, forever.
  Fixed by always marking the originating plan item consumed regardless of
  whether the pick carries a code.
- The picked course's own credit count was overriding the slot's carefully
  calibrated credit value (e.g. Cybersecurity's GHW slots are 1.5 credits
  to match the real bulletin plan) — inflated a term's total and pushed
  Cybersecurity to a 9th term. Fixed by having the slot's declared credits
  win over the course's.

A third bug in `app.py`'s card builder: it discarded a Gen Ed pick's real
title whenever the course wasn't in an already-scraped department catalog
(true for most Gen Ed courses — e.g. "AA 1" is Arts & Architecture, never
scraped for any major), silently falling back to the bare course code.
Fixed by threading the pick's own name through as a fallback.

All 18 majors re-verified at 0 warnings / exactly 8 terms after the
change. `Backend/tests.py`: added `TestGenEdRecommendations` (6 tests:
data loading, real-course resolution, the credit-priority fix, the
Firewall rule, and its Inter-Domain exemption) — 98 → 104 backend tests.

### Bookmarked for later (not part of this pass)

Two related asks from Aarush, explicitly deferred:

- **RateMyProfessor-based ranking** of Gen Ed recommendations (default to
  "easy" courses, ask the student's preference, re-rank from their answer).
  Real blocker: neither the bulletin nor `data/gen_ed_courses.json` names
  an instructor — course-to-professor assignment lives in the term-specific
  Schedule of Courses and rotates every semester, so there's no static
  mapping to anchor an RMP lookup to a specific future section. Needs a
  current-term instructor-assignment source before this can be scoped.
- **Chat-based transfer-credit capture** ("I took X at [community
  college]") — folds into the existing §5 Transfer Credit Tool effort
  below once that's unblocked.

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

## 7. Minors + double major (`merge_plans`) — 🚧 mechanism shipped, minor catalog growing

### User story

> As a student who isn't just doing one plain major, I want to add a minor
> or a second major and see it folded into the same plan — including
> courses that count for both at once — not a second, disconnected plan.

### Design

Kept to one hard constraint: **zero changes to `build_full_plan`/
`recommend_semester`** — they only ever touch
`plan['semesters'][*]['items'][*]` via `_iter_plan_items`, relying on
item-`id` uniqueness. So the whole feature is about *assembling* a merged
`plan` dict in the same shape, upstream of those functions.

- **Minor data model** — `Backend/minors/<MINOR>-<YEAR>.json`, flat (no
  semesters, since a minor isn't a flowchart): a `requirements` list of the
  same `course`/`slot` item shapes degree plans already use. A requirement
  can optionally declare `substitutes_for_major_options`: real,
  hand-verified codes that count for an existing major requirement too
  (e.g. the Math minor's STAT 418/419 satisfying CMPSC's own STAT 318/319
  requirement) — not inferred generically from overlapping option lists,
  since which substitutions are real is genuine curated bulletin data.
- **`merge_plans(primary, *, second_major=None, minors=None)`** — returns
  `primary` completely unchanged when both args are falsy (the single-major
  fast path every existing plan still hits). Otherwise deep-copies,
  renumbers merged-in item ids past `primary`'s max, and for each
  second-major/minor requirement either **widens** an existing overlapping
  major item's `options` in place (tagged `also_satisfies: [...]`) or
  appends a new item (also tagged, so even non-overlapping minor
  requirements roll into their own progress bucket). Non-widened minor
  items land in one trailing synthetic semester; second-major items merge
  term-by-term into matching semester indices. `departments` becomes the
  union of all three sources, so `load_merged_catalog` and the Gen-Ed
  department firewall work with no extra plumbing.
- **`plan_progress`'s `also_satisfies` extension** — any tagged item
  contributes to an extra `"minor:X"`/`"major:X"` category bucket on top
  of its normal one, purely additive (new keys only appear when
  `also_satisfies` is present, never true on a plain single-major plan).
  This is what lets the Progress page show a per-minor/per-second-major
  completion percentage.

### Two real bugs found (via the test suite, not user-reported)

1. A literal duplicate requirement (e.g. `MATH 140` required identically by
   two merged majors, no OR-alternative) caused infinite rescheduling,
   since `plan_progress`'s one-completed-course-per-item rule meant the
   second occurrence could never resolve — fixed by widening second-major
   items the same way minor items already were, not just minors.
2. Newly-appended (non-widened) minor/second-major items weren't tagged
   `also_satisfies`, so a minor's own non-overlapping requirements never
   counted toward its own progress bucket — fixed by tagging the
   `new_item` branch too.

### Real minors built so far

Verification recipe for each: `merge_plans` against CMPSC → `build_full_plan`
checked for absence of the "did not finish within 24 simulated terms"
warning (the real-bug signal above) → `plan_progress`'s `minor:<CODE>`
bucket checked against the bulletin's own stated credit total.

- **STATMIN** (Statistics Minor) — the pilot case, including the real
  STAT 318/319 → 418/419 substitution against CMPSC.
- **CPTSC** (Computational Sciences, College of Engineering) — substituted
  for a plain "Computer Science" minor, which doesn't exist at University
  Park (only Behrend/Capital campus pages do). 18cr, CMPSC 204→205→301→348
  plus 2 adviser-approved 400-level slots.
- **INTLBUS** (International Business, Smeal) — substituted for a plain
  "Business" minor; Smeal only offers 5 specific named minors. 27cr
  bulletin total, computed at 31cr after adding STAT 200 to unlock a real
  hidden SCM 301 prereq gap (documented, not silently absorbed).
- **PSYCH** (Psychology, Liberal Arts) — 18cr bulletin, computed 19cr
  (one-credit elective-slot rounding, the same pattern hit repeatedly
  across majors this session). Reuses the psych_catalog.json already
  scraped for the blocked Psychology *major* — the major is blocked because
  its concentrations don't publish concrete course codes, but the minor's
  own prescribed/elective courses are clean and enumerated.
- **ECON** (Economics, Liberal Arts) — 18cr, exact bulletin match, fully
  clean, reuses econ_catalog.json entirely.
- **CAS** (Communication Arts and Sciences, Liberal Arts) — 18cr, exact
  bulletin match. Prescribed CAS 101N + 2 of {203, 210, 215, 220} + one
  200-level and two 400-level supporting courses (excluding the
  bulletin-listed 493/494/495/496/499 internship/independent-study codes).

### CS/Math minors, sourced from the real bulletins.psu.edu/programs/ directory

A second batch, researched directly from PSU's master program list rather
than guessed at: filtered to Math- and Computer-related minors, then
cross-checked each candidate's own bulletin page to confirm it's a
University Park offering (the listing page's own "Campus" field is
boilerplate for every minor — the real signal is the parenthetical college
suffix in the program name, e.g. "(Engineering)"/"(Science)" for University
Park vs. "(Behrend)"/"(Capital)" for branch campuses, cross-checked against
each page's breadcrumb).

- **MATHMIN** (Mathematics, Eberly College of Science) — 26-28cr bulletin
  range, computed at 26cr. MATH 140/141 prescribed, MATH 230+231
  additional, 4 clean 400-level electives.
- **CMPENMIN** (Computer Engineering, College of Engineering) — distinct
  from the Behrend-only "Computer Engineering, Minor (Behrend)" variant.
  19cr bulletin, computed at 27cr after a real hidden-prereq chain (see
  below).
- **CYBERCF** (Cybersecurity Computational Foundations, College of
  Engineering) — distinct from the standalone Cybersecurity major (CYBER).
  18cr bulletin, computed at 42cr after two real hidden-prereq chains (see
  below) — CMPSC 473's actual prereq (CMPSC 311 **and** CMPEN 331 together)
  was confirmed directly on the live course-description page after the
  flattened catalog groups first looked like an OR.
- **ISTMIN** (Information Sciences and Technology, College of IST) — the
  plain, general-purpose IST minor, distinct from the many major-paired
  variants the bulletin also lists (IST for Accounting, for Aerospace
  Engineering, etc.). 18cr, exact bulletin match, fully clean.

**Researched but not built: Artificial Intelligence Engineering Minor**
(College of Engineering, University Park). Its real prereq chain goes 4
levels deep — A-I 410 needs A-I 341W, which needs A-I 100 concurrently with
A-I 370 and CMPSC 448, and A-I 370 itself needs STAT 401 concurrently — an
appropriate scope for a dedicated pass, not a rushed addition to this batch.

### Two real bugs, both caught by testing against MULTIPLE majors

Unlike the first minors batch, each of these was tested against **multiple**
majors, not just CMPSC — and that caught two distinct real bugs live:

1. **A genuine PSU anti-requisite pair.** MATHMIN originally required both
   MATH 230 and MATH 232 as Additional Courses. `math_catalog.json`'s own
   `excludes` data (populated during Feature 4) already had MATH 232
   excluding MATH 230 — a real "may not schedule both for credit" bulletin
   rule — so `build_full_plan` correctly refused to schedule it
   (`excludes_satisfied()` returned `False`). Fixed by dropping MATH 232.
2. **A structural interaction with flattened OR-pools — since fixed, see
   the next section.** Several majors (MATH, CYBER) have their own generic
   "pick one of several equivalent intro courses" item — e.g. MATH's own
   plan has a single item with `options: ["CMPSC 101", "CMPSC 121",
   "CMPSC 131", "CMPSC 200", "CMPSC 201"]`. When a minor's own hidden-prereq
   addition happens to name a course already inside that pool (e.g.
   CMPENMIN/CYBERCF adding `CMPSC 131` to unlock a downstream chain),
   `merge_plans` correctly widens the *existing* major item instead of
   duplicating it — but the scheduler used to be free to satisfy that
   single widened item with **any** option in the pool, not necessarily the
   specific one the minor's downstream chain needed, leaving the minor's
   own chain to silently never get the exact course it needed. Originally
   shipped as a documented, flagged limitation; fixed for real the same day
   once a design for it was agreed on.

### The OR-pool fix: prefer options that unlock a real downstream need

Fixed in `planner_engine.py` without touching a single minor's data —
this is a scheduler-ranking fix, not a per-minor patch.

**The idea, in the terms it was actually specified in:** when a course item
offers several interchangeable options (CMPSC's real "any of
101/121/131/200/201 satisfies the intro-programming requirement" pool),
the planner should recommend whichever option lets the student avoid
taking an *extra* course later — i.e. optimize for the fewest total credits
that still satisfies everything, not an arbitrary first-listed default.

- New `_codes_needed_as_prereqs(plan, catalog, done_ids)` walks every
  still-outstanding item's own eligible options and builds a priority map:
  **0** (hard) for a code that is the *sole* member of some other item's
  prereq/concurrent group — a real, non-optional requirement, like
  `PHYS 211`'s enforced concurrent `MATH 140` with no alternative; **1**
  (soft) for a code that's merely one of several OR'd alternatives
  elsewhere — picking it isn't uniquely necessary, since some other
  alternative could satisfy that same downstream requirement instead; no
  entry (implicitly lowest priority) for a code nothing downstream needs.
- `_ranked_options` (the function that already decided, in preference-tier
  order, which option to actually schedule for a multi-option item) gained
  a `preferred` parameter — within each tier, a stable sort puts higher-
  priority codes first, leaving ties in their original declared order.
- `recommend_semester` computes the priority map once per call and passes
  it through to the real scheduling decision.

**Why hard has to outrank soft, not just "anything mentioned":** the first
version of this fix treated every OR-alternative as equally "needed" and
immediately regressed on CYBER major + CYBERCF — CYBER's own math-placement
item offers `MATH 110` OR `MATH 140` (either alone satisfies the major),
but `MATH 110` is *also* one of several alternatives some other course
accepts, so a flat "is it needed anywhere" set flagged both codes as
needed and the stable sort kept `MATH 110` first (its original list
position) — even though `PHYS 211`'s concurrent requirement can *only* be
satisfied by `MATH 140`, no alternative exists. Distinguishing hard
(singleton group) from soft (multi-option group) requirements and letting
hard always win fixed it for real.

**Verified:** all 8 of the previously-limited major/minor pairings now pass
clean, including the two that were explicitly flagged as unresolved
(CYBER major + CYBERCF, MATH major + CYBERCF) — no minor's data needed to
change, only the engine. New `TestOptionRankingPrefersLoadBearingPrereqs`
(5 isolated unit tests on synthetic fixtures, pinning the hard-vs-soft
distinction precisely) plus 3 new real-major regression tests
(`test_cybercf_against_cyber_major`, `_against_data_sciences_major`,
`_against_unrelated_math_major`). All 496 backend tests pass (was 488) with
zero regressions across the full ~150-major, 11-minor catalog.

**Still open (flagged, not attempted):** authoring PSU's real minors
catalog at scale (~200+ actual minors) is per-minor research, not
mechanical, same cadence as the major rollout in §1. The Artificial
Intelligence Engineering minor's 4-level prereq chain needs its own pass
(§9 built AIENG from bulletin-only courses instead — see below). Also
unresolved: whether PSU's real double-major policy lets Gen Ed be
re-earned twice or requires dedup like the minor path does — v1's "keep
both majors' Gen Ed" is a flagged assumption, not a bulletin-verified rule.

---

## 8. Campus/location filtering — ✅ shipped

### User story

> As a student, I want the planner to be explicit about which PSU campus
> it's planning for, and to be able to pick a different campus and have the
> major/minor lists actually reflect what's real for that location — not
> just a cosmetic label.

### Design

Every major and minor built this entire session was researched
specifically against University Park bulletin pages (confirmed repeatedly
via college-suffix cross-checks, e.g. "(Business)", "(Engineering)",
"(Science)"). Rather than retrofitting a `campus` field onto all ~150
existing files, plans with no `campus` key default to `"University Park"` —
so the entire existing catalog is correctly tagged for free, and a future
branch-campus plan just needs to add the field explicitly to be picked up.

- `planner_engine.PSU_CAMPUSES` — the real 21 PSU undergraduate campus
  names (University Park first), sourced directly from the "Campus:" field
  values actually used across bulletins.psu.edu/programs/, not guessed.
- `list_degree_plans(campus=None)` / `list_minor_plans(campus=None)` — both
  now tag each entry with its `campus` (defaulting to University Park) and,
  when a campus is passed, filter to exact matches. A plan filtered by
  anything other than University Park today correctly returns empty — no
  special-casing needed, since every file's default already is University
  Park.
- New `GET /api/campuses` returns the campus list plus the default.
  `GET /api/degree-plans` and `GET /api/minor-plans` both accept an
  optional `?campus=` query param.

### Frontend

- New "Campus" dropdown at the top of the chat panel, above "Major" —
  `PlannerStateService` fetches the campus list on init, defaults to
  University Park, and refetches the major/minor lists (scoped to the
  selected campus) whenever it changes.
- Real empty state, not a silent failure: picking any campus besides
  University Park empties the major/minor dropdowns, shows an amber notice
  ("We don't have degree plan data for X yet"), and disables the major
  search box, prompt textarea, and Send button — verified live in-browser
  for both directions (switching away from and back to University Park).
- The existing "if no plans loaded, fall back to a placeholder CMPSC
  option" logic (originally meant for a failed backend fetch) had to be
  narrowed to only apply on University Park specifically — otherwise a
  legitimately-empty non-UP campus would have silently shown a fake CMPSC
  option instead of the real empty state.

### Tests

New campus-filtering assertions in `TestApiShape`: `/api/campuses` shape,
default-campus tagging on both list endpoints, filtering by a real other
campus (Erie) returns empty, filtering by University Park explicitly
matches the unfiltered list byte-for-byte. 480 backend tests passing (was
474).

**Deferred, explicitly not attempted yet (per Aarush, 2026-08-18):** actually
researching and building any non-University-Park campus's real plan data.
The mechanism above (campus field, `/api/campuses`, filtered list
endpoints, the frontend dropdown + empty state) is fully built and working
— what's missing is the DATA: a real major/minor JSON tagged with, say,
`"campus": "Erie"`. This is flagged here specifically so it's easy to pick
back up later; the natural next step would be researching one branch
campus's own bulletin pages (Erie/Behrend is the largest branch campus and
already has some catalog overlap with University Park's CMPSC/CMPEN plans)
as a first real worked example, same incremental cadence as every other
batch in this doc.

---

## 9. Chat panel redesign: N-major picker, restyled minors, X close — ✅ shipped

### Later update: docked to the right edge instead of floating bottom-left

The panel originally floated bottom-left with a dimming backdrop overlay.
Redesigned to dock full-height against the right edge as a real flex
sibling of `<main>` (no backdrop) — `<main>` now physically reflows around
it instead of being covered, so the flowchart/progress content underneath
stays visible while the panel is open. Exposed two real bugs, both fixed:
the global "?" help button was `fixed` to the viewport's top-right corner
and started colliding with the panel's own close (×) once the panel docked
against that same edge (fixed by shifting the help button left, via
`[style.right]`, only while the panel is open); and the home page's stat
cards / "Jump to" grid used viewport-based Tailwind breakpoints
(`sm:grid-cols-3`) that don't know `<main>` got physically narrower — a
docked panel can starve `<main>` of width on a wide viewport in a way an
overlay panel never did. Fixed by switching those grids to
`grid-template-columns: repeat(auto-fit, minmax(...))`, which tracks the
container's actual width instead of the viewport's.

### User story

> As a student picking a double, triple, or (rare, but real) quadruple
> major, I want a dropdown that scales to however many majors I'm
> declaring, without ever letting me pick the same major in two slots —
> and the minors picker should look and feel like the majors one. The chat
> panel itself should be a little wider, and have an obvious X to close it.

### Backend: `merge_plans` generalized from 2 majors to N

Previously `merge_plans(primary, *, second_major=None, minors=None)` only
folded ONE extra major. Added `additional_majors: Optional[List[dict]] =
None` — `second_major` stays for backward compatibility (every existing
caller and test still works unchanged), and both routes fold through the
exact same per-major loop internally, in order. A defense-in-depth dedup
guard (keyed on normalized major code) skips any major that's already been
merged — including the primary itself — so merging the same major twice
(a student picking CMPSC as both their primary and an extra slot) is a
harmless no-op server-side, not just something the frontend happens to
prevent.

- `POST /api/plan` gains `additional_majors: string[]` alongside the
  existing `second_major` field.
- New `TestMultiMajorMerging`: additional_majors alone (no second_major)
  still merges correctly, both fold in together, a duplicate major code is
  silently deduped with an item-count assertion proving it's a true no-op,
  a real CMPSC+MATH+STAT triple major flows cleanly through
  `build_full_plan` (chosen specifically for heavy MATH/STAT requirement
  overlap — the case most likely to expose a scheduling bug if the
  widening logic didn't generalize from 2 majors to N), plus API-level
  accept/reject shape tests.

### Frontend

- **Number of majors** — a 1-4 selector; the primary "Major" picker stays
  as-is, and picking >1 renders that many "Major 2" / "Major 3" / ...
  plain `<select>` dropdowns (grouped by college via `<optgroup>`, reusing
  the same `groupedPlanOptions` the primary picker already computes).
  Each slot's own option list excludes the primary major AND every OTHER
  slot's current pick (`extraMajorOptionsFor(index)`), so the same major
  can never be selected twice across the pickers — not rejected after the
  fact, simply never offered. Swapping the primary "Major" to a value
  already sitting in an extra slot clears that slot automatically.
- **Minors restyled to match Major** — replaced the native `<select
  multiple>` with the same searchable, college-grouped dropdown panel the
  Major picker uses, except clicking an option toggles a checkbox and
  keeps the panel open (multi-pick) instead of closing on select. Closed
  state shows "None selected", the one selected minor's label, or "N
  minors selected".
- **Layout** — Major and Minors now sit side by side in the same row,
  right below the Campus dropdown (previously Minors was buried below Year
  planning / Summer toggle in a mismatched native-select row). Number of
  majors + its extra slots sit directly below.
- **Wider panel** — `w-[26rem]` → `w-[34rem]`, needed room for the
  Major/Minors side-by-side row and the college-grouped `<optgroup>` extra
  major dropdowns.
- **X close button** — a light-grey `×` in the panel's own top-right
  corner (new `closed` output on `ChatbotComponent`, wired to the same
  `toggleChat()` the floating "Close chat" pill already used). The
  floating pill stays too, so there are now two ways to close — an X on
  the panel itself, and the pill that reopens it.

Verified live in-browser end-to-end: set "Number of majors" to 3, confirmed
CMPSC (primary) was excluded from both extra slots' option lists, picked
MATH in slot 2 and confirmed it then disappeared from slot 3's options,
toggled a minor (AIENG) via the restyled dropdown and watched the plan
re-fetch live (warning text updated from "7 extra terms" to "8 extra
terms"), and confirmed the X button closes the panel exactly like the
floating pill does.

### Tests

488 backend tests passing (was 480). Frontend: `npx tsc --noEmit` clean,
plus the live-browser walkthrough above (no dedicated frontend test suite
exists in this project — verification has always been manual in-browser,
same as every other frontend change this session).

---

## Execution log

- 2026-08-20 — §7 batch: 5 more real minors (81 total), all from the
  College of Earth and Mineral Sciences' own real minor listing, each
  picked to pair name-for-name with an already-built major of the same
  program (ENGY, ENVSYS, MINE, PNG, EARTHSCI all already exist as majors).
  Fetched the college's full minor listing directly (19 real programs)
  rather than guessing, which also caught a real research error from the
  prior batch: "Marine Science" (ruled out then as "does not appear on the
  Earth and Mineral Sciences minor listing") is actually real, just filed
  under the *Eberly College of Science*'s listing instead ("Marine
  Sciences, Minor") -- not built this batch (no directly-paired major
  exists for it) but flagged for a future batch. Also fetched Engineering,
  Eberly Science, Health and Human Development, and Education's full
  minor listings before picking, to confirm this EMS batch was the
  strongest available option; none of those four had as many direct
  name-for-name major pairings still open. **Energy Engineering**
  (ENGYMIN) -- 18cr exact bulletin match, computed 34cr against CMPSC
  after a real hidden-prereq chain: the bulletin's First Elective Pool
  (select 9cr from EGEE 302/304/411W/420/430/EME 301) turns out to force
  EME 301 into any valid 9cr combination, since every pool option besides
  EGEE 302 and EGEE 411W (which only needs EGEE 302) itself needs EME 301
  -- filled with EGEE 302 + EME 301 + EGEE 411W. Second Pool filled with
  EGEE 437 + EGEE 441 + EGEE 470, all three satisfied by the same EME 301
  already forced above, avoiding EGEE 451's extra FSC 431/CHEM 210 chain.
  EGEE 302/EME 301's own CHEM 112 -> CHEM 110 -> MATH 22 -> MATH 21 chain
  (the same MATH-21-placement-gate pattern hit repeatedly across this
  project) was added explicitly for the CMPSC pairing; the ENGY major's
  own flowchart already supplies the whole chain, so the addition
  collapses to a near no-op against ENGY itself (only EGEE 470 is
  genuinely new against ENGY, and its sole prereq is already met).
  **Environmental Systems Engineering** (ENVSYSMIN) -- 18cr exact
  bulletin match, computed 37cr against CMPSC. Its prescribed ENVSE 427
  is a genuine 5-branch AND chain (CHEM 110 AND CHEM 112 AND MATH 141 AND
  MNPR 301 AND [CE 360 or EME 303]) -- picked EME 303 over CE 360 since
  EME 303's own chain (MATH 250/251 + PHYS 211) is shallower than CE
  360's (EMCH 212, itself needing EMCH 210/211 + MATH 141). The elective
  slot was filled with ENVSE 400 specifically because it reuses the same
  CHEM 110 the prescribed courses already force, adding zero net-new
  codes beyond what ENVSE 427 already required. The ENVSYS major's own
  flowchart already includes every single code this minor needs (CHEM
  110/111/112, EME 301/303/460, MATH 21/22/140/141/251, PHYS 211/212, and
  all the ENVSE/MNPR courses directly), so the ENVSYS pairing is a true
  no-op. **Mining Engineering** (MINEMIN) -- 20cr exact bulletin match,
  all 7 courses prescribed with no elective choice, computed 35cr against
  CMPSC. Real finding worth flagging: every one of this minor's 7
  prescribed courses (MNG 230/331/404/410/412/422/441) is *already*
  independently required by the MINE major's own flowchart -- meaning a
  MINE-major student pairing this minor would satisfy essentially none of
  the bulletin's own stated "at least six credits unique from the
  student's major(s)" administrative rule in real life. The planner
  doesn't model that uniqueness constraint (same already-flagged
  limitation as FORMIN/WWRMIN/REBPMIN in the prior batch), so the build
  itself passes clean either way -- documented here for a human to weigh
  whether real MINE students could actually declare this minor as
  written, or whether PSU advising would redirect them. **Subsurface
  Energy Engineering** (PNGMIN) -- 18cr exact bulletin match, the
  cleanest minor of this batch: zero hidden-prereq chain needed against
  either verification target. Its elective pool has several genuinely
  prereq-free real options (EME 460, GEOSC 454) plus PNG 440W (whose only
  two prereqs, PNG 305 and EME 200, are already prescribed by this same
  minor) -- deliberately avoided EBF 484 (a 4-branch AND chain through
  EBF 200/301/ECON 302) and MNG 410 (an unrelated 3-course chain) from
  the same pool. **Earth Systems** (EASYSMIN) -- 18cr: Prescribed (EARTH
  2, prereq-free) + Additional (select 6cr, filled with EARTH 103N + GEOG
  430, both prereq-free, both exactly 3cr) are bulletin-exact; Supporting
  Courses (9cr) has no bulletin-published course list ("the Earth Systems
  Committee's approved list of courses") so it's modeled as three generic
  3cr slot items, matching the established precedent for unpublished
  pools (BIOL's 400-level groups, ESC's Foundational/Technical
  Electives). Pairs with the already-built Earth Sciences major
  (EARTHSCI), which had already modeled its own 18cr "one of five
  interdisciplinary minors" requirement as six generic "Minor Course
  (Earth Systems)" slot items at build time, explicitly naming this exact
  minor as its assumed choice -- the second minor this session (after
  WWRMIN) to fulfill a slot EARTHSCI's own build notes had already
  flagged by name. Since `merge_plans` only widens overlapping
  `course`-type items (not `slot`-type ones), the real EARTH/GEOG courses
  merge alongside, not in place of, the major's own placeholders, with 0
  warnings either way. All 5 verified both against CMPSC (grad_years=8)
  and their own real matching major (ENGY, ENVSYS, MINE, PNG, EARTHSCI)
  -- 0 warnings and `goal.met = True` in all 10 pairings on the first
  simulation, no data fixes needed after this batch's research phase
  checked every candidate course's `concurrent_groups` field (not just
  `prereq_groups`) and cross-checked `excludes` data up front, applying
  the accumulated lessons from WWRMIN's own concurrent-group miss and
  MATHMIN's own MATH 230/232 exclusion hit in earlier batches -- notably,
  this check is *why* the Meteorology Minor (also considered for this
  batch) was dropped: its own MATH 231 + MATH 232 prescribed pair
  directly excludes MATH 230, which the already-built METEO major's own
  flowchart requires, an unresolvable catalog-level anti-requisite
  against the one major it would naturally pair with -- substituted Earth
  Systems in its place rather than force a mismatched verification major.
  10 new tests added to a new `TestEighthRealMinorBatch` class (same
  `_merge_and_build` helper pattern as the prior batch's
  `TestSeventhRealMinorBatch`). 672 backend tests passing (was 661).
- 2026-08-20 — §7 batch: 5 more real minors (76 total), a cross-college
  batch deliberately picked to pair with already-built majors that had no
  minor yet (RHS, SPLED, FORES, EARTHSCI, ABSM). Surveyed the real
  college-level minor listings directly (Smeal Business, College of
  Education, College of Health and Human Development, College of
  Agricultural Sciences, College of Earth and Mineral Sciences, College of
  the Liberal Arts) before picking, which ruled out several of the
  suggested candidates as not real PSU programs before any build work
  started: Actuarial Science and Real Estate minors do not exist at Smeal
  (both are B.S. majors only -- Smeal's own real minor listing has exactly
  five programs: Information Systems Management [ISM, already built],
  International Business [INTLBUS, already built], Legal Environment of
  Business [LEBUS, already built], and two Supply Chain variants, one of
  which is the already-built SCISTMIN); Middle Level Education is a real
  major (MLED) but has no corresponding minor; Cognitive Science does not
  appear on the Liberal Arts minor listing at all; Community, Environment,
  and Development and a Wood Products/Bio-based Products minor do not
  appear on the Agricultural Sciences minor listing (though a related,
  differently-named "Renewable Bioproducts, Minor" does); Marine Science
  does not appear on the Earth and Mineral Sciences minor listing; "Sport
  Management" and "Watershed Stewardship" aren't real titles, but "Sport
  Studies, Minor" and "Watersheds and Water Resources, Minor" are the real
  equivalents. Rehabilitation and Human Services (RHSMIN, College of
  Education) -- 18cr exact bulletin match, fully clean, name-for-name
  pairing with the already-built RHS major; minor code RHSMIN avoids
  colliding with the major's own code. Prescribed RHS 100 + RHS 300 + RHS
  403 (9cr) + RHS 401 for the 'select one additional 400-level RHS course'
  slot (3cr); Supporting Courses (6cr) filled entirely within RHS (RHS 402
  + RHS 404, both real listed options on the bulletin's own verbatim
  cross-department list, which explicitly names several more RHS codes
  alongside its BBH/CSD/HDFS/HPA/KINES/NUTR/PSYCH/SOC options) -- every
  course in rhs_catalog.json is prereq-free. Special Education (SPLEDMIN,
  College of Education) -- 24cr exact bulletin match, fully clean,
  name-for-name pairing with the already-built SPLED major; minor code
  SPLEDMIN avoids colliding with the major's own code. Prescribed EDPSY 14
  + SPLED 400 + SPLED 419 + SPLED 461 (12cr) + HDFS 229 + SPLED 403A (6cr,
  two 'select one' slots) + CSD 146 + CSD 218 (6cr, a 'select 6cr' pool) --
  every course prereq-free, deliberately avoiding the pool's only option
  with a real prereq (CSD 300, which needs CSD 146, itself in the same
  pool). Forest Ecosystems (FORMIN, College of Agricultural Sciences) --
  18-20cr bulletin range, computed 18cr at the floor, fully clean,
  name-for-name pairing with the already-built FORES major; minor code
  FORMIN matches the department's own FOR course prefix rather than
  colliding with the major's EARTHSCI-style renamed code. Prescribed FOR
  203 + FOR 308 (6cr, FOR 308's own real concurrent requirement is
  satisfied by FOR 203 in the same term); Additional Courses (12cr min,
  6cr at 400-level) filled with FOR 255 + FOR 303 (6cr, non-400) + FOR 401
  + FOR 403 (6cr, 400-level) -- every course prereq-free, deliberately
  avoiding the pool's other FOR courses that chain through FOR
  203/266/308/421/440 prerequisites (FOR 421, FOR 439, FOR 466W, FOR 475).
  Watersheds and Water Resources (WWRMIN, College of Earth and Mineral
  Sciences) -- 18cr exact bulletin match, fully clean, pairs with the
  already-built Earth Sciences major (EARTHSCI), which cites this exact
  minor by name as one of five interdisciplinary-minor options in its own
  build notes -- the first minor this session verified against a major
  that had already flagged it by name in an earlier, unrelated build. The
  bulletin publishes no Prescribed Courses at all -- the entire 18cr comes
  from one committee-approved elective pool spanning ASM/BE/CE/CHEM/ENVSE/
  ERM/FOR/GEOG/GEOSC/PLANT/SOILS/WFS, filled with ASM 327 + PLANT 217 +
  GEOSC 340 (9cr, non-400) + GEOSC 413W + GEOSC 419 + GEOSC 452 (9cr,
  400-level) -- all six genuinely prereq- AND concurrent-free. First draft
  used WFS 410 instead of GEOSC 340 and initially looked clean (its
  `prereq_groups` is empty), but a live build against both verification
  majors surfaced a real `could not schedule WFS 410` warning -- its
  `concurrent_groups` field (not checked in the first pass) requires BIOL
  110, WFS 209N, or WILDL 101 in the same term, none of which either
  verification plan supplies. Swapped in GEOSC 340 instead and re-verified
  clean; also caught the same class of hidden concurrent requirement on BE
  307 (needs CE 360/ME 320 concurrently) before it became a second
  warning, by checking every candidate course's `concurrent_groups` field
  up front rather than only `prereq_groups` -- a real methodology gap in
  this batch's own first-pass research, not a scraper bug. Renewable
  Bioproducts (REBPMIN, College of Agricultural Sciences) -- 18cr bulletin
  exact match on the nominal course list, computed 27cr against CMPSC
  after a real hidden-prereq chain; pairs name-for-name with the
  already-built Agricultural and Biorenewable Systems Management major
  (ABSM), which had no minor yet. Prescribed ABSM 300 + ABSM 350 (needs
  MATH 110/140, already required by both CMPSC and ABSM) + ABSM 411 (needs
  ABSM 350 [already prescribed] AND CHEM 110) = 9cr; Additional Courses
  filled with ABSM 423 (needs only ABSM 300, already prescribed) + MATSE
  441 + MATSE 445 (both prereq-free) = 9cr. Real hidden-prereq chain: ABSM
  411's own CHEM 110 requirement isn't satisfied by CMPSC (the standard
  baseline), and CHEM 110 itself enforces MATH 22, which itself enforces
  MATH 21 -- the same MATH-21-chain pattern documented repeatedly across
  this project's earlier batches (MICRBMIN, EBFMIN) -- added CHEM 110 +
  MATH 22 + MATH 21 explicitly (9cr) for the CMPSC pairing; the ABSM
  major's own flowchart already includes this entire chain (plus MATH 3/4
  feeding MATH 21 on the major's own build) on its own semester plan, so
  the addition collapses to a no-op against ABSM itself, matching the
  credits-differ-per-pairing pattern already established for BEMIN/
  MICRBMIN. All 5 verified both against CMPSC (this catalog's standard
  baseline, grad_years=8) and their own real matching major (RHS, SPLED,
  FORES, EARTHSCI, ABSM) -- 0 warnings and `goal.met = True` in all 10
  pairings, every CMPSC-paired minor's credit total confirmed exactly via
  `plan_progress` (18/24/18/18/27cr); the FORES-paired FORMIN pairing
  reports 17cr rather than 18cr for the minor's own bucket since FORES's
  own major flowchart already independently requires FOR 203/255/308/403 --
  matching the established precedent of asserting exact minor credits only
  for the CMPSC pairing, not the natural-major pairing, since overlapping
  courses can shift which bucket absorbs the credit. 10 new tests added to
  a new `TestSeventhRealMinorBatch` class (same `_merge_and_build` helper
  pattern as the prior batch's `TestSixthRealMinorBatch`). 661 backend
  tests passing (was 651).
- 2026-08-20 — §7 batch: 5 more real minors (71 total), a cross-college
  batch deliberately picked so every minor pairs with an already-built
  major of the same or closely-related real-world program (MICRB, RPTM,
  FLMPR, CASBA/CASBS, BE all already exist as majors). Surveyed the
  college-level minor listings directly (Eberly Science, HHD, Bellisario
  Communications, Agricultural Sciences, Engineering, IST) rather than
  guessing candidate names -- this ruled out several of the suggested
  candidates as not real PSU programs before any build work started:
  Robotics Engineering, Cybersecurity Analytics and Operations, Broadcast
  Journalism, Advertising, and Public Relations minors do not exist at
  University Park (Engineering's real minor list has no Robotics
  Engineering entry; IST's real minor list has only ISTMIN and SRAMIN,
  both already built; Bellisario's real minor list has six programs --
  Communication and Social Justice, Digital Media Trends and Analytics,
  Film Studies, IST for Telecommunications, Journalism [already built], and
  Media Studies [already built] -- with no Advertising/PR/Broadcast
  Journalism minor among them). Innovation and Entrepreneurship is already
  built under its real bulletin title (ENTI = "Entrepreneurship and
  Innovation, Minor"). Microbiology (MICRBMIN, Eberly College of Science)
  -- 24cr bulletin exact match on the nominal course list, computed 30cr:
  CHEM 110 (prescribed) enforces a real MATH 22 -> MATH 21 chain that
  neither CMPSC nor the MICRB major's own flowchart already covers (both
  build straight to MATH 140 calculus) -- added explicitly (6cr), the same
  MATH-21-chain pattern seen in EBFMIN and the BE major's own build notes.
  Every other prescribed/additional/supporting course (MICRB
  201/202/251/410, MICRB 421W, MICRB 412, MICRB 411) resolves entirely
  within the minor's own prescribed set, fully clean. Recreation, Park, and
  Tourism Management (RPTMMIN, College of Health and Human Development) --
  18cr bulletin exact match, name-for-name pairing with the RPTM major;
  minor code RPTMMIN avoids colliding with the major's own code. RPTM 101
  + RPTM 120 prescribed (6cr) plus RPTM 201 + RPTM 210 (6cr, non-400-level)
  + RPTM 410 + RPTM 433W (6cr, 400-level) for the 12cr Supporting Courses
  requirement, all four prereq-free, deliberately avoiding the pool's many
  other RPTM courses that chain through RPTM 120/210/236/250/254/325. Film
  Studies (FLMSMIN, Bellisario College of Communications / College of the
  Liberal Arts) -- 18cr bulletin exact match, pairs with the already-built
  Film Production major (FLMPR) as the closest real match -- the bulletin
  itself frames the minor as complementary to Film Production, emphasizing
  "critical, aesthetic, and historical studies of film, not the art of
  filmmaking." Distinct from the College's separate Media Studies minor
  (MEDIAMIN already built). Prescribed COMM 150N + COMM 250 (6cr); the
  bulletin's own 12cr Supporting Courses pool points only to a non-bulletin
  department webpage (bellisario.psu.edu) for its specific course list, not
  the bulletin itself, so real film-focused COMM courses were used instead
  -- COMM 151N + COMM 242 (6cr, non-400) + COMM 451 + COMM 452 (6cr,
  400-level, both needing only the already-prescribed COMM 250). This is
  the same modeling treatment MUSPERFMIN's Applied Music/Ensemble lines got
  when no fixed catalog list exists. Communication and Social Justice
  (CSJMIN, Bellisario College of Communications) -- 18cr bulletin exact
  match, fully clean, pairs with the Communication Arts and Sciences majors
  (CASBA/CASBS). COMM 232 + COMM 432 prescribed (6cr, COMM 432 is the
  minor's own capstone needing COMM 232 AND one of COMM 270/282); COMM 270
  (3cr) doubles as the Supporting Courses pick and clears COMM 432's second
  prereq group; SOC 5 + AFAM 100N + PLSC 451 (9cr, one at 400-level) picked
  directly from the bulletin's own published cross-department elective list
  (which spans AFAM, SOC, PLSC, WMNST, PHIL, GEOG, HIST, ENGL, CRIM among
  others) specifically because all three carry zero prerequisites of their
  own, unlike most of that list's other 400-level options (e.g. AFAM/HIST
  431, SOC 419/422/424) which chain through department-specific 200/300
  intro courses not otherwise in the minor. Biological Engineering (BEMIN,
  College of Engineering) -- 18-20cr bulletin range, computed 28cr, pairs
  name-for-name with the BE major. The bulletin publishes no mandatory
  Prescribed Courses at all -- every requirement comes from four selection
  pools. HORT 101 (3cr, prereq-free) for the Related Science Electives
  pool; BE 301 + BE 302 (7cr, BE 302 satisfied by BE 301 in its own
  OR-prereq-group) for the 300-Level BE pool, chosen over the pool's other
  options (BE 303/305/306/308) specifically because both resolve through a
  single MATH 251 addition -- needs only MATH 141, already required by both
  verification majors -- rather than the EMCH structural-mechanics or CHEM
  chemistry chains the other pool members require; BE 465 (needs only the
  already-selected BE 302) + BE 404 (needs the already-selected BE 301 AND
  one of EMCH 210/213) for the 400-Level BE pool, EMCH 210 (needs only
  MATH 140, already required by both verification majors) added as the
  second and last hidden-prereq course; the bulletin's own 3cr Supporting
  Courses line names no fixed course at all ("in consultation with the
  minor adviser") and was modeled as a generic slot, the same convention
  used for MUSPERFMIN's Applied Music/Ensemble lines. All 5 verified both
  against CMPSC (this catalog's standard baseline, grad_years=8) and their
  own real matching major (MICRB, RPTM, FLMPR, CASBA, BE) -- 0 warnings and
  `goal.met = True` in all 10 pairings, every CMPSC-paired minor's credit
  total confirmed exactly via `plan_progress` (30/18/18/18/28cr).
  **Two candidates researched and dropped before building:** Global
  Health, Minor (College of Health and Human Development) is real (27-28cr)
  but its Prescribed Courses mandatorily include BBH 390A/390B, a 9cr
  supervised fieldwork placement gated behind a written application to the
  program Director (GPA statement, faculty-adviser signature, proposed
  fieldwork plan) -- a non-course admission gate in the same family as
  PPHOTO's portfolio review and MUSPERFMIN's audition, except here the
  fieldwork courses themselves (not just entry to the minor) are
  non-standard credit-bearing placements rather than ordinary scheduled
  courses, so it was dropped rather than modeled. Biochemistry and
  Molecular Biology, Minor (Eberly College of Science, would pair
  name-for-name with the already-built BMB major) was drafted and then
  dropped: its own Prescribed Courses chain six real levels deep from
  MATH 21 (CHEM 110 -> CHEM 112 -> CHEM 210 -> CHEM 212 -> BMB 401 ->
  BMB 402, the last needing BMB 401 which itself needs both CHEM 210 and
  CHEM 212), a genuinely deep cascade for a minor's own required course
  list rather than an elective pool, so Microbiology (a real, shallower,
  name-for-name Eberly Science sibling) was built in its place instead. 10
  new tests added to a new `TestSixthRealMinorBatch` class (same
  `_merge_and_build` helper pattern as the prior batch's
  `TestFifthRealMinorBatch`). 645 backend tests passing (was 635).
- 2026-08-20 — §7 batch: 5 more real minors (66 total), picked so every
  minor pairs with an already-built major of the same or closely-related
  real-world program (PLANET, PPHOTO, LARCH, MUSIC/MUSICBM, PLSCI all
  already exist as majors). Planetary Science and Astronomy (PSAMIN, Eberly
  College of Science) — 19cr bulletin exact match, name-for-name pairing
  with the PLANET major; distinct from the already-built Astronomy and
  Astrophysics Minor (ASTROMIN), a different real program in the same
  department. Prescribed ASTRO 401 + ASTRO 402W (7cr); Additional Courses'
  "select one" 3cr slot filled with ASTRO 1 — deliberately picked over the
  other four options since it also clears ASTRO 401's own prereq group AND
  is the shared prereq for the "select three" 9cr slot's three picks
  (ASTRO 120/130/140), so one course clears every downstream gate at once.
  ASTRO 401's own hidden MATH 140 prereq needed no separate minor
  requirement since both CMPSC (the standard baseline) and PLANET already
  require it on their own flowcharts. Photography (PHOTOMIN, College of
  Arts and Architecture) — 19cr bulletin, computed 20cr. Prescribed
  PHOTO 303 + PHOTO 404; the live course-description pages were checked
  directly for both real Enforced Prerequisites since the flattened catalog
  groups first looked like two-course AND requirements — both are actually
  real ORs (PHOTO 303 needs "PHOTO 200 or PHOTO 202", PHOTO 404 needs
  "PHOTO 300 or PHOTO 303"), so PHOTO 202 alone (also counted toward the
  "select 9cr of PHOTO courses" pool, doing double duty) clears both gates
  without needing PHOTO 100/200/300 at all. The bulletin's "select 3cr of
  400-level PHOTO" slot has no prereq-clean 3cr option without also adding
  PHOTO 200, so PHOTO 405 (4cr, needs only PHOTO 202) was used instead — a
  1cr rounding overage, the same PSYCH-style credit-rounding pattern seen
  repeatedly this session. Landscape Architecture (LARCHMIN, College of
  Arts and Architecture) — 18cr bulletin exact match, name-for-name pairing
  with the LARCH major, fully clean: AA 121 + LARCH 60 + LARCH 125
  prescribed (7cr) plus LARCH 424 + LARCH 450 (6cr at the 400-level) +
  LARCH 65 + LARCH 155 (5cr) for the 11cr Additional Courses requirement,
  all seven courses prereq-free, deliberately avoiding the bulletin list's
  other options that chain through multi-level LARCH 115/116/145/155/156/
  215/216/255 design-studio sequences. Music Performance (MUSPERFMIN,
  College of Arts and Architecture) — 21cr bulletin exact match, pairs with
  the Music B.A./B.M. majors, distinct from the already-built Music
  Technology minor (MUSTECHMIN). The bulletin's own admission requirement
  ("Admission to the minor depends upon a successful performance audition")
  is a non-course entrance gate, not a credit-bearing substitute for the
  course list itself — noted in the minor's `notes` field but not modeled
  as a requirement item, the same treatment PPHOTO's own portfolio-review
  entrance gate got when that major was built. "Select 8cr applied music"
  and "select 4cr ensembles" name no fixed course codes at all (Penn
  State's applied-lesson/ensemble system is numbered per instrument and
  level, not a fixed catalog list) — modeled as two generic slots, the same
  convention this project's own MUSIC major flowchart already uses for its
  identical Applied Music/Ensemble line items. Filled the remaining 9cr
  with three prereq-free MUSIC courses (MUSIC 4 as the elective, MUSIC 423
  + MUSIC 469 at the 400-level). Horticulture (HORTMIN, College of
  Agricultural Sciences) — 18cr bulletin exact match, the cleanest minor
  this batch; substituted for a plain "Plant Science, Minor", which does
  not exist at University Park (the college's own minor-program listing has
  only subject-specific minors — Agronomy, Horticulture, Entomology, etc.),
  picking Horticulture as the closest real, direct pairing with the
  already-built Plant Sciences major (PLSCI). HORT 101 + HORT 202 +
  PLANT 201 prescribed (9cr, PLANT 201 a real cross-listing with AGECO 201,
  both scraped identically as prereq-free 3cr courses) plus HORT 131 (3cr)
  + HORT 407 + HORT 431 (6cr, HORT 431's only real prereq, HORT 101, is
  already prescribed above) — all six additional courses prereq-free,
  skipping the bulletin pool's other options for carrying real extra
  prerequisites (HORT 402W needs a concurrent SOILS 101 plus BIOL 441/
  HORT 315; HORT 455 needs AGBM 101 or ECON 102; HORT 459 needs an
  intro-biology-sequence course). All 5 verified both against CMPSC (this
  catalog's standard baseline, grad_years=8) and their own real matching
  major (PLANET, PPHOTO, LARCH, MUSIC, PLSCI) — 0 warnings and
  `goal.met = True` in all 10 pairings, every CMPSC-paired minor's credit
  total confirmed exactly via `plan_progress` (19/20/18/21/18cr).
  **Three candidates researched and dropped before building:** Food
  Systems, Minor (College of Agricultural Sciences) is real and confirmed
  offered at University Park, but its Prescribed Courses mandatorily
  include FDSYS 490 and FDSYS 495 — the FDSYS prefix has no scraped catalog
  file anywhere in `catalogs/*.json`, and since that file set is out of
  scope for this batch, the minor could not be modeled without inventing
  course data, so it was dropped. Meteorology, Minor (Earth and Mineral
  Sciences) was revisited per instruction to try verifying against a
  non-CMPSC major instead of the one that tripped it in an earlier batch,
  but the real blocker turned out to be structural, not major-specific: the
  minor's own MATH 232 requirement carries a real PSU anti-requisite
  against MATH 230 (`math_catalog.json`'s own `excludes` data), and CMPSC —
  the fixed standard baseline every minor in this project is verified
  against, not swappable — already requires MATH 230 on its own flowchart,
  so the conflict reproduces against CMPSC regardless of which second major
  is chosen; still not built. Turfgrass Science, Minor was rechecked
  against the College of Agricultural Sciences' current minor listing and
  reconfirmed absent — only a Turfgrass Management *graduate* minor and a
  Turfgrass Science and Management *certificate* exist at University Park,
  no undergraduate minor, matching the prior batch's finding. 10 new tests
  added to a new `TestFifthRealMinorBatch` class (same `_merge_and_build`
  helper pattern as the prior batch's `TestFourthRealMinorBatch`). 635
  backend tests passing (was 625).
- 2026-08-20 — §7 batch: 5 more real minors (61 total), a College of
  Agricultural Sciences / College of Earth and Mineral Sciences batch
  deliberately picked so every minor pairs with an already-built major of
  the exact same real-world program (ERM, ANSC, EBFIN, AGBM, MATSCI all
  already exist as majors, the first batch this session where every single
  pairing is a name-for-name match rather than a substitution). Environmental
  Resource Management (ERMMIN, Agricultural Sciences) — 18cr bulletin
  nominal, computed 29cr: prescribed ABSM 327 (3cr) + SOILS 101 (3cr); real
  hidden-prereq chain, ABSM 327 enforces a concurrent PHYS 211-or-250
  requirement, and PHYS 211 itself enforces a concurrent MATH 140 — both
  added explicitly (8cr, also unlocking ERM 327's identical concurrent gate
  for free); the bulletin's "any ERM offerings to reach 18cr, min 6cr at
  400-level" pool filled with ERM 210/402/411/448 (12cr, 9cr of it
  400-level) — ERM 402/411 need one of AGBM 101/ECON 102/ECON 104, filled
  with ECON 102 (3cr, prereq-free, unlocks both at once). Animal Science
  (ANSCMIN, Agricultural Sciences) — 20-21cr bulletin range, computed 20cr
  exactly at the floor, fully clean: every course resolves via ANSC 201 or
  ANSC 301, both prescribed, avoiding ANSC 311 (real prereq ANSC 100, not
  otherwise in the minor) and ANSC 300 (needs a concurrent intro-biology
  course not otherwise in the minor). Energy Business and Finance (EBFMIN,
  Earth and Mineral Sciences) — 27-29cr bulletin range, computed 32cr after
  the real MATH 21 hidden-prereq gap under STAT 200 (the same pattern hit
  repeatedly this session); EBF 472, one of the bulletin's own listed
  Statistics Selection options, doesn't exist in ebf_catalog.json. Surfaced
  a real instance of the documented "flattened OR-group" scraper quirk in
  eme_catalog.json: EME 444's real "ECON 104 or EGEE 102 or EGEE 120"
  prerequisite was scraped as three separate AND-required groups instead of
  one OR group, which would have made it permanently unschedulable against
  the CMPSC baseline (which has none of the three) even though the EBFIN
  major itself already supplies EGEE 120 early — caught live via a genuine
  "could not schedule EME 444" warning against CMPSC, not silently missed;
  since catalogs/*.json is out of scope for this batch, worked around at
  the minor-data level by substituting EBF 483 (needs EBF 200 + MATH 140 +
  EBF 301 + one of ECON 106/SCM 200/STAT 200/STAT 401 — every one of those
  already required elsewhere in this minor) instead. Agribusiness
  Management (AGBMMIN, Agricultural Sciences) — 21cr bulletin exact match,
  fully clean, zero hidden-prereq additions: picking ECON 102 (rather than
  AGBM 101) for the Foundation Course requirement also happens to unlock
  AGBM 106 and the entire 400-level elective pool used here (AGBM 407/455/
  470A/470B) in one stroke. Materials Science and Engineering (MATSCIMIN,
  Earth and Mineral Sciences) — 18cr bulletin exact match, the cleanest
  minor built this batch (every MATSE 400-level course used carries no
  prerequisite at all); hit the same flattened-OR-group quirk a second time
  independently (MATSE 449's real "MATSE 201 or MATSE 202" prerequisite was
  scraped as two separate AND-required groups) — caught during research
  this time rather than via a build-time warning, and worked around by
  picking MATSE 412 (genuinely prereq-free) instead. All 5 verified both
  against CMPSC (this catalog's standard baseline, grad_years=8) and their
  own real matching major (ERM, ANSC, EBFIN, AGBM, MATSCI) — 0 warnings and
  `goal.met = True` in all 10 pairings, every computed minor-credit total
  matching what `plan_progress` independently reports. **Three candidates
  researched and dropped before building, none committed with a warning
  outstanding:** Energy Engineering, Environmental Systems Engineering, and
  Mining Engineering minors (all College of Earth and Mineral Sciences) each
  sit behind a genuinely deep, multi-branch prerequisite cascade — every
  option in each minor's own required/elective pools ultimately gates on
  EME 301, EGEE 302, ENVSE 427, or MNG 331/422, which in turn need some
  combination of MATH 140 → MATH 141 → MATH 250/251, MATH 140 → PHYS 211 →
  212/EME 303, CHEM 110 → CHEM 112, and EMCH 210/212, i.e. 8+ extra hidden
  courses against the CMPSC baseline just to reach a single elective — the
  same anti-pattern class already documented for AIENG's 4-level A-I chain
  in an earlier batch, not an appropriate scope for a single batch entry.
  A plain "Food Science, Minor" and "Global Business Strategies, Minor"
  were also searched for and confirmed NOT to exist as real current
  University Park undergraduate minors — the College of Agricultural
  Sciences' own minor-program listing shows "Food Systems, Minor" instead
  (a different, real program), and the College of Earth and Mineral
  Sciences' own listing has no Global Business Strategies minor at all
  (despite EARTHSCI-2026.json's own notes citing it as one of five
  interdisciplinary minor options for that major's Earth Systems
  requirement — likely retired or renamed since that major was built) — so
  neither was pursued. 10 new tests added to a new `TestFourthRealMinorBatch`
  class (same `_merge_and_build` helper pattern as the prior batch's
  `TestThirdRealMinorBatch`).
- 2026-08-20 — §7 batch: 5 more real minors (56 total), a cross-college
  batch deliberately picked to pair with majors that had no minor yet
  (HDFS, WFS, ME as a Nuclear-Engineering-minor host, CMLIT, PLSCBA).
  Human Development and Family Studies (HDFSMIN, College of Health and
  Human Development) — 18cr exact bulletin match: prescribed HDFS 129
  (3cr) + 9cr from any HDFS courses (HDFS 200/210/239) + 6cr of 400-level
  HDFS courses (HDFS 416/431) — every course prereq-free in
  hdfs_catalog.json; picked specifically because most other 400-level HDFS
  courses need HDFS 312W in addition to HDFS 129, which this minor doesn't
  otherwise include. Wildlife and Fisheries Science (WFSMIN, College of
  Agricultural Sciences) — 22cr bulletin nominal, computed 26cr: prescribed
  BIOL 110 + WFS 209N + WFS 430 (10cr); real hidden-prereq gap, WFS 430
  needs BIOL 220W (not otherwise part of the minor) — added explicitly,
  since BIOL 220W's own only prereq is BIOL 110, already prescribed here;
  12cr elective pool filled with WFS 447W/462 (prereq-free) + WFS 422/460
  (need only BIOL 110, already prescribed), avoiding WFS 463W since it
  needs WFS 300/301/310, none otherwise in this minor. Nuclear Engineering
  (NUCEMIN, College of Engineering) — 18-20cr bulletin range, computed
  19cr. The real bulletin explicitly restricts this minor to students
  "admitted to a major other than nuclear engineering," so it was verified
  against Mechanical Engineering, not the NUCE major itself — the first
  minor this session verified against a major other than its own natural
  pairing for a real, bulletin-stated eligibility reason rather than a
  researched-but-dropped substitution. Real hidden-prereq gap: NUCE 301
  needs MATH 251 (not otherwise part of the minor) — added explicitly;
  MATH 251's own only prereq is MATH 141, already required by every
  engineering major (ME/CE/AERSP/EE) and by CMPSC itself, so the chain
  closes with one course. The bulletin's vague "Reactor Design and
  Thermodynamics" (0-8cr) and "400-Level Courses" (6-12cr, bulletin says
  only "consult the Department of Nuclear Engineering") pools were filled
  with real, prereq-clean picks (NUCE 309, needs only MATH 251; NUCE 401,
  needs MATH 250/251; NUCE 408, needs only NUCE 301). World Literature
  (WLITMIN, College of the Liberal Arts) — the real bulletin title is
  "World Literature, Minor," not "Comparative Literature, Minor" (that
  name doesn't exist as an undergraduate minor); pairs with the
  already-built Comparative Literature major (CMLIT). 18cr exact match:
  prescribed CMLIT 400Y + CMLIT 10 (6cr) + a 12cr elective pool unified by
  a war-and-society theme (CMLIT 108/405/406/440) — 84 of cmlit_catalog
  .json's 85 courses carry no prerequisite at all, so this was the
  cleanest minor built this batch. Politics and Public Policy (POLPOLMIN,
  College of the Liberal Arts) — 18-19cr bulletin nominal, computed 22cr:
  prescribed PLSC 1 + PLSC 202 (the bulletin's "PLSC 202 or PUBPL 304"
  pick — PLSC 202 chosen since no pubpl_catalog.json exists in this
  project's scraped data) + STAT 200 for the methods requirement (the
  bulletin's "PLSC 309/309H or STAT 200" pick); real hidden-prereq gap,
  STAT 200 needs MATH 21 (not otherwise part of the minor) — added
  explicitly, the same hidden-prereq pattern already used for STAT 200/
  SCM 200/ACCTG 211 across several earlier batches; 9cr Supporting Courses
  pool filled entirely within PLSC (PLSC 404/460/490, all prereq-free,
  all 400-level, exceeding the bulletin's 6cr 400-level minimum) to keep
  the chain simple. Distinct from the already-built plain PLSCMIN.
  **No candidates dropped this batch** — every one of the 5 researched
  candidates (cross-checked against Human Development and Family Studies,
  Wildlife and Fisheries Science, Nuclear Engineering, World Literature/
  Comparative Literature, and Politics and Public Policy) turned out to be
  real, buildable, and free of the anti-requisite/missing-catalog/
  non-course/deep-cascade/college-restriction traps documented in earlier
  batches; a plain "Turfgrass" minor and a plain "Forest Ecosystem
  Management" minor were searched for but not found as real University
  Park undergraduate minors (only a Turfgrass Management *graduate* minor
  and a Turfgrass Science and Management *certificate* exist), so neither
  was pursued. All 5 built minors verified via `merge_plans` against both
  CMPSC (this catalog's standard baseline, grad_years=8) and their
  natural-or-bulletin-mandated pairing major (HDFS, WFS, ME, CMLIT,
  PLSCBA) — 0 warnings and `goal.met = True` in all 10 pairings, every
  computed minor-credit total matching what `plan_progress` independently
  reports. 10 new tests added to a new `TestThirdRealMinorBatch` class
  (`_merge_and_build` helper, same pattern as the prior batch's
  `TestSecondRealMinorBatch`). 613 backend tests passing (was 603).
- 2026-08-20 — §7 batch: 5 more real minors (51 total), a cross-college
  batch deliberately picked to pair with majors that had no minor yet
  (CRIM/CRIMBS, HPA, CAMS, GD, BAIS), following up on the prior same-day
  batch. Legal Studies (LEGSTMIN, College of the Liberal Arts) — 18cr exact
  bulletin match: prescribed PLSC 1 (3cr) + 6cr from the bulletin's
  'additional courses' pool (PLSC 210N + PLSC 471) + 9cr 'Supporting
  Courses and Related Areas' (min. 6cr at 400-level, max 6cr per
  discipline) filled with CRIM 113 + CRIM 401W (CRIM capped at exactly
  6cr) + PHIL 405 (Seminar in Philosophy of Law, a second discipline);
  every course prereq-free in its real catalog. Health Policy and
  Administration (HPAMIN, College of Health and Human Development) — 18cr
  bulletin nominal, computed 21cr: renamed off the bulletin's own plan code
  to avoid colliding with the already-built HPA major's code. Prescribed
  HPA 57 + HPA 101 (6cr); supporting HPA 210 + HPA 211 (6cr, both resolve
  via HPA 101 alone); 400-level HPA 433 + HPA 442 (6cr, both need only HPA
  332); one hidden-prereq addition, HPA 332 itself (3cr), needed to unlock
  the 400-level pair — its own prereq (ACCTG 211 or HPA 211) AND HPA 101 is
  fully satisfied by this minor's own HPA 211 + HPA 101, no further
  cascade. Classics and Ancient Mediterranean Studies (CAMSMIN, Liberal
  Arts) — 18cr exact match, renamed off its own plan code to avoid
  colliding with the already-built CAMS major's code; bulletin gives no
  enumerated list, just 'select 12cr from CAMS courses' + 'select 6cr of
  400-level CAMS courses', filled with real prereq-free picks (CAMS 1/10/
  100/101 + CAMS 400W/405) since every one of cams_catalog.json's 91
  courses is prereq-free. Graphic Design (GDMIN, College of Arts and
  Architecture) — 21cr bulletin nominal, computed 27cr: renamed off its own
  plan code to avoid colliding with the already-built GD major's code; all
  7 bulletin-prescribed studio courses (GD 100/101/102/200/201/405/406)
  plus one hidden addition, GD 107 (4cr, needs only GD 101), since the real
  scraped catalog shows GD 200/201/405 all actually require GD 107 as a
  prerequisite rather than the bulletin's own listed sequence courses; two
  courses (GD 101, GD 201) are 4cr in the real catalog vs. 3cr as the
  bulletin page states, and the catalog's real value was trusted (same
  precedent as MATHMIN's MATH 140/231 in an earlier batch). Supply Chain
  and Information Sciences and Technology (SCISTMIN, Smeal College of
  Business) — 18cr bulletin nominal, computed 32cr: prescribed IST 110/
  210/220 + SCM 301 (12cr) + electives SCM 404 + SCM 406 (6cr); SCM 301's
  own real prereq is a 3-way AND (ACCTG 211 AND ECON 102 AND (SCM 200 or
  STAT 200)), each confirmed via scm_catalog.json's prereq_groups, plus
  ACCTG 211/SCM 200's own shared MATH 21 prereq — all four added as hidden
  prereq courses so the minor is self-sufficient against any
  major, not just a Smeal one; verified this whole chain collapses to a
  no-op (fully absorbed via also_satisfies widening) when merged against
  BAIS, since BAIS's own major requirements already include every one of
  those five hidden courses plus SCM 301 itself. **One real candidate
  researched but dropped in favor of SCISTMIN**: Supply Chain and
  Information Systems, Minor (SCIS_UMNR, Smeal's other real supply-chain
  minor) — its own two electives, SCM 445 and SCM 460, are both explicitly
  'Not available to baccalaureate business students in Smeal' per their
  real catalog course descriptions, which would make a same-college
  verification pairing (e.g. against BAIS, itself a Smeal baccalaureate
  major) unrealistic in practice, so SCIST_UMNR (whose own electives carry
  no such restriction) was built instead. **Two more real candidates
  researched but dropped**: Religious Studies, Minor (real, Liberal Arts,
  18cr, confirmed exact bulletin title) has no scraped catalog file for
  its RLST course prefix (no `rlst_catalog.json` exists), so it was
  dropped rather than faked, consistent with this project's established
  precedent for missing-catalog department prefixes. Global Health, Minor
  (real, Health and Human Development, 27-28cr) is built around a
  mandatory supervised field-experience course pair (BBH 390A/B) sitting
  at the end of a real 5-6-level prerequisite cascade (MATH 21 → STAT 200
  → BBH 310 → BBH 440 → BBH 390A → BBH 390B) comparable in depth to the
  AI Engineering minor dropped in an earlier batch for the same reason —
  dropped as too deep to model cleanly rather than force through a large
  hidden-prereq chain. Also confirmed Finance/Marketing/Actuarial Science
  have no real minor at Smeal (B.S. majors only, per Smeal's own bulletin
  minors listing), so none were pursued. All 5 built minors verified via
  `merge_plans` against both CMPSC (this catalog's standard baseline,
  grad_years=8) and their own naturally-paired major already in
  `degree_plans/` (CRIM, HPA, CAMS, GD, BAIS) — 0 warnings and
  `goal.met = True` in all 10 pairings, every computed minor-credit total
  matching what `plan_progress` independently reports. 10 new tests added
  to a new `TestSecondRealMinorBatch` class (`_merge_and_build` helper,
  same pattern as the prior batch's `TestCsAndMathMinorBatch`). 603
  backend tests passing (was 593).
- 2026-08-20 — §7 batch: 5 more real minors (46 total), a mixed batch
  picked for direct overlap with existing majors rather than a single
  college. Sexuality and Gender Studies (SGSMIN, Liberal Arts) — the
  bulletin's real title is "Sexuality and Gender Studies, Minor" (not
  "Women's, Gender, and Sexuality Studies, Minor," which is the B.S. major's
  name only); 18cr exact match: prescribed ENGL 245 + WMNST 250 (6cr) plus
  two named elective categories (Humanities/Arts vs. Sciences, min 3cr
  each, 6cr overall at 400-level) filled with WMNST 106N/110/400N/476W, all
  prereq-free; two bulletin-listed codes (WMNST 301, AFAM/WMNST 364) don't
  exist in any scraped catalog and were simply skipped in favor of real
  alternatives. Linguistics (LINGMIN, Liberal Arts) — distinct from the
  separate "Applied Linguistics, Minor" (not built this batch); 18cr exact
  match, fully clean, entirely reuses ling_catalog.json (LING 402 + LING
  404 prescribed, LING 100 foundation, LING 405/410/448 electives). African
  American Studies (AFAMMIN, Liberal Arts) — 18cr exact match, reuses
  afam_catalog.json already built for the AFAM major; three bulletin
  400-level electives (AFAM 412/463/469) carry real prereqs (intro
  theatre/dance or first-year writing) and were skipped for the clean
  AFAM 401/409 pair instead. Media Studies (MEDIAMIN, Donald P. Bellisario
  College of Communications) — distinct from the already-built
  Communication Arts and Sciences minor (different college, different
  department prefix); 18cr exact match, reuses comm_catalog.json already
  built for JOURN/FLMPR/MDST/TELE/ADPR; several bulletin electives carry
  real prereqs and were skipped for prereq-free COMM 401/403 instead.
  Jewish Studies (JSTMIN, Liberal Arts) — 18cr exact match, reuses
  jst_catalog.json already built for the JST major, fully clean
  (JST 10 prescribed + JST 121/118/140 mid-level + JST 416/426 400-level).
  **Two real candidates researched but dropped**: Bioethics and Medical
  Humanities, Minor (real, intercollege, 18cr) requires a BMH-prefix
  capstone (BMH 490) and other BMH-prefix courses that have no scraped
  catalog file (bioet_catalog.json only has 8 BIOET-prefix courses, no
  BMH ones), and this batch's ground rules forbid touching or adding
  catalogs/*.json files, so it was dropped rather than modeled around the
  gap. Global and International Studies, Minor (real, Liberal Arts) is
  structurally built around 12cr of required education-abroad credit and
  world-language proficiency exams rather than an enumerated course list,
  which doesn't fit this project's course-code-based requirement model at
  all — dropped as a poor fit rather than faked with placeholder codes.
  Also confirmed real minors that don't exist at University Park and were
  correctly not built: a plain "Criminology, Minor" (the only UP-adjacent
  hit is a Behrend-only "Crime, Law, and Psychology" minor), "Risk
  Management, Minor" (Smeal only offers the B.S. major, no minor), and
  "Social Data Analytics, Minor" (only a B.S. major exists). All 5 built
  minors verified against CMPSC (grad_years=8, the standing baseline) AND
  their own naturally-paired major already in degree_plans/ (WMNSTBA,
  LING, AFAM, JOURN, JST) — 0 blocking warnings, 18.0cr computed against
  CMPSC in every case, exactly matching each real bulletin total. 10 new
  tests added to `TestPlanMerging` (`_merge_and_build` helper). 593 backend
  tests passing (was 583).
- 2026-08-20 — §7 batch: 5 more real minors (41 total), picked for direct
  overlap with existing majors (JAPNSBA, KORBA, CHNSBA, GEOG, SRA already
  built). Japanese Language (JAPNSMIN, Liberal Arts) — 18-20cr stated
  range, computed 19cr: prescribed JAPNS 2/3 (8cr) + 'select 4cr from
  JAPNS 110/299' (JAPNS 110) + 'select 6-8cr' from a large upper-level pool
  (JAPNS 401 + JAPNS 430, 7cr) — every course prereq-free in
  japns_catalog.json; the bulletin's own ASIA 499 cross-listing isn't in
  any scraped catalog, so only real JAPNS-prefix codes were used. Korean
  Language (KORMIN, Liberal Arts) — 18cr exact match: KOR 2/3 (8cr) + KOR
  110 (4cr) + KOR 424/425 (6cr, culture/cinema courses), same ASIA 499
  omission as Japanese. Chinese Language (CHNSMIN, Liberal Arts) — 18-20cr
  range, computed 18cr (floor): CHNS 2/3 (8cr) + CHNS 110 (4cr) + CHNS
  452/453 (6cr). Geography (GEOGMIN, Earth and Mineral Sciences) — 18cr
  exact match; the bulletin gives four open category names ('3cr physical
  geography', '3cr human geography', '6cr additional', '6cr 400-level')
  with zero enumerated course codes — filled by matching each
  geog_catalog.json course's own real title to its category (GEOG 10
  'Physical Geography: An Introduction', GEOG 20 'Human Geography: An
  Introduction', GEOG 210/220 for additional, GEOG 411/421 for 400-level),
  all six prereq-free. Security and Risk Analysis (SRAMIN, College of IST)
  — 21cr exact match, min. 6cr at 400-level: prescribed CYBER 221/SRA
  111/SRA 211 (9cr); CYBER 221's real prereq is SRA 111 AND one of CMPSC
  101/121/IST 140, so IST 140 was picked for the first 'select one' 3cr
  slot specifically to clear that second AND-group without depending on
  CMPSC; IST 220 picked for the second 'select one' slot since it also
  unlocks the 400-level CYBER electives (CYBER 451 + CYBER 456, 6cr) used
  to fill the elective pool. **A candidate researched but dropped**:
  Meteorology, Minor (Earth and Mineral Sciences) — real and at University
  Park (METEO_UMNR, 39cr: CHEM 110, MATH 231+232+251, METEO 300/421/431,
  PHYS 211/212, plus a 9cr elective pool), but MATH 232 carries a real PSU
  anti-requisite against MATH 230 (`math_catalog.json`'s own `excludes`
  data) — and MATH 230 is already required by both CMPSC (this catalog's
  standard baseline) and the METEO major itself, so this minor could never
  pass a clean CMPSC or METEO-major verification without either touching
  catalog data (out of scope for this batch) or silently dropping a real
  prescribed course. Swapped in Security and Risk Analysis instead of
  spending the batch's research budget resolving that conflict. All 5 built
  minors verified both against CMPSC (this catalog's standard baseline) and
  against their own real matching major (JAPNSBA, KORBA, CHNSBA, GEOG,
  SRA) — 0 warnings and `goal.met = True` in every one of the 10 pairings,
  every stated minor credit total confirmed exactly via `plan_progress`.
  10 new tests added (`TestPlanMerging`: 5 CMPSC-pairing tests plus 5
  own-major-pairing tests). 583 backend tests passing (was 573).
- 2026-08-19 — §7 batch: 5 more real minors (31 total), Languages &
  Communications category (College of the Liberal Arts / Bellisario
  College of Communications). English (ENGLMIN) — bulletin gives three
  open ranges with no enumerated list ('6cr from ENGL 200-299', '6cr from
  ENGL 400-499', '6 additional credits') — filled with real, prereq-clean
  courses (ENGL 200/201, 400/401, 205/206); the 400-level pair's only real
  prereq (the ENGL 15/CAS 137H writing family) is already satisfied by
  CMPSC's own Gen Ed writing item. Spanish (SPANMIN) — the bulletin's own
  'X or Y' core-course pairs (SPAN 200/301, SPAN 215/253W, SPAN
  100/100A/100B/100C) partly reference codes not in span_catalog.json
  (301, 215, 100C) — used the catalog-present alternate of each pair
  (SPAN 200, SPAN 253W, SPAN 100); 18cr exact match. French and
  Francophone Studies (FRMIN) — 18cr exact match, prescribed FR 201/202
  plus one 'Additional' combination (FR 316 + FR 331) plus two 400-level
  courses, all prereq-free in fr_catalog.json. German (GERMIN) — 19cr
  exact match, prescribed GER 201/301/302W plus a 300/400-level course
  (GER 310) plus two 400-level courses, all prereq-free in
  ger_catalog.json. Journalism (JOURNMIN, Bellisario Communications) —
  major code kept distinct from the existing JOURN major; 19cr exact
  match, a real clean 3-deep prereq chain (COMM 160 -> COMM 260W ->
  COMM 461/462) with COMM 260W's ENGL 15-family half satisfied the same
  way as ENGLMIN's. All 5 verified both against CMPSC (this catalog's
  standard baseline) and against their own thematically-matching major
  (ENGL, SPANBA, FRENCHBA, GERBA, JOURN) — 0 warnings and `goal.met =
  True` in every one of the 10 pairings, no data bugs found this batch.
  7 new tests added (`TestPlanMerging`: 5 CMPSC-pairing tests plus 2
  own-major-pairing tests for ENGLMIN/JOURNMIN). 563 backend tests
  passing (was 556).
- 2026-08-20 — §7 batch: 5 more real minors (36 total), a
  College-of-Health-and-Human-Development-leaning batch picked for direct
  major overlap (THEA, ANTH, KINES, MUSTECH, NUTR majors already exist).
  Theatre (THEAMIN, Arts and Architecture) — 18cr exact match; core
  requirement 'select ONE of THEA 100/101N/105' picked THEA 100
  specifically because it's also the option every downstream 400-level
  THEA course's own OR-group prereq lists, so the 15cr of supporting
  courses (THEA 102/103/130 plus 400-level THEA 401/419) all resolve
  prereq-free. Anthropology (ANTHMIN, Liberal Arts) — 18cr exact match,
  ANTH 2N/21/45N prescribed plus ANTH 11 and two ANTH 400-489 courses
  (401, 403), all no-prereq in anth_catalog.json. Kinesiology (KINESMIN,
  Health and Human Development) — 18cr exact match; deliberately picked
  the no-prereq corner of the elective pool (KINES 100/101/160N/303/402/
  414) over KINES 350/360/384, which chain into real BIOL/PHYS/PSYCH
  courses not otherwise part of the minor. Music Technology (MUSTECHMIN,
  Arts and Architecture) — 18cr exact match; major code kept distinct
  from the existing MUSTECH B.M. major even though both share the same
  real-world name (two separate bulletin programs). Nutritional Sciences
  (NUTRMIN, Health and Human Development) — the one real data bug this
  batch: NUTR 445 is a prescribed course whose bulletin course-description
  page lists its actual enforced prereq as 'BIOL 161 and 162 and 163 and
  (164 or BMB 211) and NUTR 251', not just NUTR 251 as the minor's own
  requirements table implies — added the real BIOL 161/162/163/164
  sequence explicitly (same concurrent pairing the NUTR major itself
  already schedules in year one), landing the minor at a computed 26cr,
  8cr over the bulletin's stated 18cr, same documented-not-absorbed
  convention as CHEMMIN's MATH 140 and ASTROMIN's PHYS 212 additions. All
  5 verified both against CMPSC (this catalog's standard baseline) and
  against their own real matching major (THEA, ANTH, KINES, MUSTECH,
  NUTR) — 0 warnings and `goal.met = True` in every one of the 10
  pairings. 10 new tests added (`TestPlanMerging`: 5 CMPSC-pairing tests
  plus 5 own-major-pairing tests). 573 backend tests passing (was 563).
- 2026-08-19 — §7 batch: 5 more real minors (26 total), Arts & Humanities
  category (College of the Liberal Arts / Arts and Architecture). History
  (HISTMIN) and Philosophy (PHILMIN) — both entirely 'select N credits of
  department courses, no enumerated list, consult an adviser'; new
  hist_catalog.json (216 courses). Sociology (SOCMIN) — similar shape,
  SOC 1 prescribed plus 15cr open pool. Political Science (PLSCMIN) — the
  one genuinely structured pool this batch: 'at least one course in each
  of American, Comparative, International Relations, and Theory' — picked
  one clean intro course per subfield by matching each course's own real
  title to its subfield (PLSC 1/3/14/17W). Art History (ARTHMIN) — reused
  and expanded arth_catalog.json (was 6 courses left over from the Art
  History B.A. build, now 93, including real 400-level courses); filled
  the bulletin's own 'must include one Western and one non-Western course'
  requirement with ARTH 111 (Western) + ARTH 120 (non-Western). All 5
  clean, all landing exactly on their bulletin's stated credit total. 5
  new tests added (`TestPlanMerging`). 552 backend tests passing (was 547).
- 2026-08-19 — §7 batch: 5 more real minors (21 total), Sciences category
  (Eberly College of Science / Earth and Mineral Sciences), continuing
  autonomously toward all ~200 real PSU minors per explicit instruction
  (not stopping to ask category preference each batch). Chemistry
  (CHEMMIN) — real hidden-prereq gap: CHEM 227 needs MATH 140, absent
  from the minor's own prescribed courses; added it, landing at 30cr
  against the bulletin's 26-28cr range. Biology (BIOLMIN) — clean, 18cr
  exact match at the range floor. Physics (PHYSMIN) — clean, 29cr exact
  match at the range floor, real prescribed chain (140->141,
  211->212/213->214->237). Astronomy and Astrophysics (ASTROMIN) — two
  real findings: the bulletin's own table lists ASTRO 291/292 as 3cr
  each but the real catalog entries are 4cr each, and ASTRO 291 itself
  needs PHYS 212, never mentioned in the bulletin's prescribed-course
  list — added PHYS 212 explicitly; landed at 28cr. Geosciences
  (GEOSCMIN) — clean, 18cr exact match. 5 new tests added
  (`TestPlanMerging`, reusing the `_merge_minor_and_build` helper from
  the previous batch). 547 backend tests passing (was 542).
- 2026-08-18 — §7 batch: 5 more real minors (16 total), Business &
  Management category. Entrepreneurship and Innovation (ENTI) —
  substituted for a plain 'Entrepreneurship, Minor' (no UP page); real
  page has 10 named 'clusters' for its Additional 9-11cr with no course
  list published for any of them, modeled generically. Labor and Human
  Resources (LHR) — fully clean, 18cr exact match. Leadership
  Development (LDEV, Agricultural Sciences) — a real, unusually deep
  hidden-prereq chain: AEE 495 needs AEE 412 AND AEE 413 (an AND, not
  either/or, despite informal bulletin footnote wording), and AEE 412
  itself needs AEE 100 AND AEE 295 AND AEE 311 — closed the whole chain
  rather than stopping partway; computed total lands at 32cr against
  the bulletin's stated 18cr, needs 6 years (not 5) paired with CMPSC to
  actually finish, which is real credit load, not a bug — confirmed via
  the same 'no scheduling-bug warnings, just needs more calendar time'
  check used throughout this session. Information Systems Management
  (ISM, Smeal) — substituted for 'Business Analytics'/'Management
  Information Systems' (the latter is Behrend-only, not UP); 19cr exact
  match. Legal Environment of Business (LEBUS, Smeal) — 18cr exact
  match, every prereq in the chain resolves within the minor itself.
  Two real branch-campus traps caught and avoided during research:
  'Business Administration, Minor' only exists at Capital/Abington, and
  'Management Information Systems, Minor' only exists at Behrend —
  neither is a real University Park offering, so neither was built,
  consistent with this session's UP-only scope. 5 new tests added
  (`TestPlanMerging`). 542 backend tests passing (was 537).
- 2026-08-18 — §1 batch: 6 more majors (161 of ~194), all pre-verified
  by a research pass before building (no re-research needed this round).
  Japanese, B.A. (JAPNSBA) and Korean, B.A. (KORBA) — new japns_catalog.json
  (47 courses) and kor_catalog.json (42 courses), both entirely
  prereq-free; a couple of bulletin-named codes (JAPNS 450/433, KOR
  121/450) don't exist and were dropped from their pools. African
  Studies, B.A. (AFRSTBA) — new afr_catalog.json (45 courses); bulletin's
  own 'AFR 110' isn't real, the actual code is 'AFR 110N'. Sustainability,
  Society, and Environmental Geography, B.A. (SSEVG) — fully reuses
  geog_catalog.json/emsc_catalog.json, zero new scraping. Anthropological
  Science, B.S. (ANTHSBS) — distinct from the built Anthropology, B.A.;
  built the Integrated Option (of 4 named options, all with real course
  codes); re-scraped anth_catalog.json (partial → 90 courses) for its
  Methods-course pool. Landscape Contracting, B.S. (LSCPE) — built the
  Design/Build Option (of 2); new hort_catalog.json (2 → 45 courses) and
  a refreshed art_catalog.json; real hidden-prereq gap caught — ACCTG 211
  needs MATH 21, but the major's own required math course is MATH 26
  (Trigonometry), a genuinely different course — added MATH 21 explicitly
  rather than leaving ACCTG 211 permanently unschedulable. All 6 verified
  clean (0 warnings, goal met, credit totals matching their bulletins).
  New aliases for all 6 in `_MAJOR_ALIASES`. 537 backend tests passing
  (was 524).
- 2026-08-18 — §1 batch: 5 more majors (155 of ~194), all "companion"
  majors reusing catalogs already built for a sibling degree (fast,
  low-risk), plus one genuinely new one. Geography, B.A. (major code
  GEOBA) — companion to the built Geography, B.S., fully reuses
  geog_catalog.json; real bulletin-vs-catalog gap (GEOG 364's real STAT
  200 prereq isn't in the catalog's own prereq_groups) resolved by
  deliberately picking STAT 200 for the GQ Foundation slot rather than a
  generic one. Mathematics, B.A. (MATHBA) — companion to the built
  Mathematics, B.S. Organizational Leadership, B.S. (OLEADBS) — companion
  to the built OLEAD (B.A.); re-scraped olead_catalog.json (5→15 courses)
  for OLEAD 220/410/411. Women's, Gender, and Sexuality Studies, B.S.
  (WMNSTBS) — companion to the built WMNSTBA; zero re-scraping needed, all
  9 real courses already cataloged. Applied Linguistics, B.A. (APLNGBA) —
  distinct from the already-built Linguistics, B.A.; re-scraped
  aplng_catalog.json (2→25 courses). All 5 verified clean on the first
  build attempt (0 warnings, goal met, credit totals matching their
  bulletins exactly) — the established research→build→verify pipeline
  held up without incident this batch. New aliases for all 5 in
  `_MAJOR_ALIASES`. 524 backend tests passing (was 514).
- 2026-08-18 — §7: resolved the double-major Gen-Ed dedup question
  `merge_plans` had flagged as an unverified assumption since it shipped.
  Found the authoritative source — PSU's AAPPM policy M-3 (Concurrent and
  Sequential Majors Programs): "Students must fulfill all of the General
  Education requirements for at least one major listed on their record as
  well as all General Education courses listed as Major or Option
  requirements for their other degree(s)." Confirms the generic 45cr Gen
  Ed pool is satisfied ONCE across a concurrent-majors plan, never
  doubled — but any course a major's own flowchart actually requires
  stays required regardless of Gen Ed overlap. `merge_plans` now applies
  the exact same generic-slot dedup to a second/additional major's own
  `type: "slot"` Gen Ed items that minors already got — real `type:
  "course"` items are never touched by this, only bare domain slots with
  no specific course attached. Verified on CMPSC+MATH: 1 GHW slot
  survives the merge, not 2. New regression test
  (`test_second_majors_own_gen_ed_slot_is_deduped_not_doubled`); 514
  backend tests passing (was 513).
- 2026-08-18 — §1 batch: 5 more majors (150 of ~194), the first batch
  sourced by cross-referencing bulletins.psu.edu/programs/ against the
  145 already-built majors AND `BLOCKED_MAJORS.md`'s 12 tracked gaps by
  exact title (not just department code), pre-filtering for a real
  published Suggested Academic Plan to avoid repeating that blocker
  class. Architectural Engineering, B.A.E. (Engineering) — one of PSU's
  few real 5-year/10-semester programs; re-scraped a stale 3-course
  `ae_catalog.json` (now 62) and `arch_catalog.json` (now 49, up from
  23). Artificial Intelligence Engineering, B.S. (Engineering) — new
  `aie_catalog.json`; caught a real gap via `concurrent_satisfied`
  returning False despite `prereqs_satisfied` passing (A-I 370 needs one
  of CMPSC 465/DS 305/CMPSC 462 *concurrently*, invisible from
  `prereq_groups` alone). Data Sciences, B.S. (Engineering) — a third
  distinct PSU "Data Sciences B.S." program (Science, Engineering, and
  IST each have their own plan code and suggested plan) with major code
  DTSCE to avoid colliding with the already-built Science-college DS.
  Data Sciences, B.S. (IST) — major code DATSC, real hidden-prereq gap
  (DS 200 needs MATH 21) and a bulletin-vs-real-catalog code mismatch
  ("DS 440W" isn't real; the actual course is "DS 440"). Communication
  Arts and Sciences, B.S. — sibling of the already-built CASBA (B.A.),
  reused its conventions directly. Real regression caught and fixed
  before landing: re-scraping `arch_catalog.json` reintroduced a mutual
  ARCH 121↔ARCH 131 concurrent cycle a prior session had deliberately
  made one-directional (a dedicated regression test,
  `test_mutual_concurrency_pairs_are_one_directional`, caught it) —
  patched the 8 affected courses' prereq/concurrent groups back to the
  one-directional form while keeping the 26 newly-scraped courses. Added
  `_GRAD_YEARS_OVERRIDE["AE"] = 5` to `TestHistoricalCatalogYears`. New
  aliases for all 5 majors in `_MAJOR_ALIASES`. 513 backend tests
  passing (was 503); all 5 verified with 0 warnings / goal met.
- 2026-08-18 — Two more real-usage fixes on top of §9. (1) Real PSU billing
  awareness: `build_full_plan` now tags every term with `below_full_time`
  (under 12cr — part-time, per-credit billing) and `above_flat_rate` (over
  19cr — additional per-credit charges), surfaced as badges next to each
  term's credit count on the Flowchart page. Deliberately NOT added to
  `warnings` — a first draft did, and broke 208 tests, because a light
  final semester (e.g. a student-teaching-only term) is routine for many
  real majors, not a problem, and `warnings == []` is this whole suite's
  signal for "nothing wrong." Also deliberately does not clamp the
  scheduler to 19cr — 3 real majors in this catalog already need >19cr in
  at least one real term per their own bulletin-calibrated
  `max_credits_per_semester`. Purely informational, additive per-term
  flags instead; 0 regressions. (2) Root-caused a report of "picking one
  minor pulls in another minor's courses": both the raw merge_plans path
  and the real `/api/plan` endpoint were re-verified end to end and never
  leaked a second minor's departments — the actual cause was the §9
  minors picker itself, a multi-select that stays open after every click
  with only a collapsed "N minors selected" label, easy to over-select
  without noticing. Fixed with always-visible removable chips
  (`MATHMIN ×`) under the field. New `TestCreditBillingAnnotation` class
  (6 tests) and a permanent regression test locking in single-minor
  scoping. 503 backend tests passing (was 496); both fixes verified live
  in-browser end to end (ELED plan showing "20cr Extra fee" / "6cr
  Part-time" badges; CMPSC+MATHMIN showing one clean chip and 0 IST
  leakage).
- 2026-08-18 — Fixed the §7 OR-pool limitation for real (was flagged as a
  known, deferred issue earlier the same day): `recommend_semester` now
  computes a hard/soft priority map (`_codes_needed_as_prereqs`) over every
  still-outstanding item's real prereq/concurrent needs and threads it
  through `_ranked_options` as a stable tie-breaker, so a multi-option pool
  item (e.g. a major's generic "any intro programming course" slot)
  resolves to whichever option a minor elsewhere actually needs instead of
  an arbitrary first-listed default. Caught and fixed a real bug in the
  fix's own first draft along the way: treating every OR-alternative as
  equally "needed" isn't enough — a hard, no-alternative requirement (e.g.
  PHYS 211's enforced concurrent MATH 140) has to outrank a merely soft
  one-of-several-alternatives elsewhere, or the tie-break silently
  reintroduces the same bug it was meant to fix. No minor data changed;
  all 8 previously-limited major/minor pairings now pass clean. 496
  backend tests passing (was 488), zero regressions across the full
  ~150-major catalog.
- 2026-08-18 — Shipped §9, chat panel redesign: `merge_plans` generalized
  from 2 majors to N (`additional_majors` list alongside the original
  `second_major`, both folding through the same loop, with a dedup guard
  so a duplicate major code is a harmless no-op); a "Number of majors"
  picker (1-4) with per-slot option filtering so the same major can never
  be selected twice across dropdowns; minors restyled to the same
  searchable college-grouped dropdown the major picker already used
  (multi-select via toggle, not native `<select multiple>`); Major and
  Minors moved to a single row right under Campus; panel widened
  (26rem → 34rem); a light-grey X added to the panel's own top-right
  corner. Also built AIENG (Artificial Intelligence Engineering minor)
  from ONLY the courses in the bulletin's own Program Requirements table,
  per explicit instruction not to invent its real (but unlisted) hidden
  prereq chain — the resulting "could not schedule A-I 410" warning is
  documented as the correct, expected, bulletin-accurate behavior, not a
  bug, and locked in by a dedicated regression test. Branch-campus plan
  data explicitly deferred to a later session (§8's "Deferred" note).
  488 backend tests passing (was 480); verified live in-browser end-to-end.
- 2026-08-17 — Shipped §8, campus/location filtering: a new "Campus"
  dropdown in the chat panel (21 real PSU campus names, sourced from
  bulletins.psu.edu itself), backed by `PSU_CAMPUSES` and campus-aware
  `list_degree_plans`/`list_minor_plans` in the engine plus a new
  `GET /api/campuses` endpoint. Every existing plan defaults to University
  Park (no retroactive tagging needed — confirmed every major/minor this
  session was researched against UP bulletin pages specifically). Verified
  live in-browser both directions: switching to a non-UP campus (Erie)
  correctly empties the major/minor dropdowns, shows a real "no data yet"
  notice, and disables the prompt input; switching back to University Park
  fully recovers. 480 backend tests passing (was 474).
- 2026-08-17 — Shipped a second §7 minor batch, sourced directly from
  bulletins.psu.edu/programs/ instead of guessed: 4 real University Park
  CS/Math minors (MATHMIN, CMPENMIN, CYBERCF, ISTMIN), each cross-checked
  against multiple majors (not just CMPSC) rather than one. That broader
  testing caught two real bugs: a genuine PSU anti-requisite pair (MATH 232
  excludes MATH 230, already correctly encoded in `excludes` data from
  Feature 4) that made an early MATHMIN draft unschedulable, and a
  structural `merge_plans` limitation where widening into a major's own
  flattened "pick one of several" item can let the scheduler satisfy it
  with a different option than a minor's hidden-prereq chain needed
  (documented as a follow-up, not fixed generically). Researched but
  deliberately not built: the Artificial Intelligence Engineering minor,
  whose real prereq chain is 4 levels deep. New `TestCsAndMathMinorBatch`
  test class with explicit multi-major portability checks. 474 backend
  tests passing (was 465).
- 2026-08-17 — Shipped §7's minor-catalog follow-on batch: 5 real
  broad-appeal minors (CPTSC substituting for a nonexistent "Computer
  Science" minor, INTLBUS substituting for a nonexistent "Business" minor,
  PSYCH, ECON, CAS), each verified via `merge_plans` against CMPSC with 0
  "did not finish" warnings and a `minor:<CODE>` progress bucket matching
  its bulletin's stated credit total. One real hidden-prereq gap found and
  fixed (INTLBUS's SCM 301 needed STAT 200, not otherwise present for a
  non-business major). New `TestRealMinorBatch` test class, one test per
  minor. 465 backend tests passing (was 460).
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
- 2026-07-26 — Shipped six new majors (§1) per Aarush's direct request ("IT
  field, then business, math, english, science, and medical"): Nursing
  B.S.N., English B.A., Business B.S. (Intercollege), Cybersecurity
  Analytics and Operations B.S. (substituted for the general IST major,
  which has no on-campus plan), Mathematics B.S., and Biology B.S. Each
  shipped as its own commit with 0 warnings / 8-term graduation / a
  dedicated test class. Found and fixed one real scraper bug (boundary-regex
  gaps for "Recommended Corequisite:"/bare "enforced concurrent" labels),
  one real engine bug (duplicate-option items starving each other forever —
  fixed generally in `_pick_option`/`_ranked_options`, not by reordering
  data), and patched three more instances of the placement-gate prereq
  pattern (`CMPSC 101/121` requiring `MATH 21`/`110`). 69 backend tests
  passing (was 52).
- 2026-07-26 — Shipped all 10 Smeal College of Business majors (§1) per
  Aarush's follow-up request ("everything that falls under that branch...
  Accounting, finance, supply chain, ETC"): Accounting, Finance, Supply
  Chain and Information Systems, Marketing, Management, Actuarial Science,
  Business Analytics and Information Systems, Corporate Innovation and
  Entrepreneurship, Real Estate, and Risk Management (Enterprise Risk
  Management option). Each shipped as its own commit with 0 warnings /
  8-term graduation / a dedicated test class. Discovered and reused an
  identical First/Second Year "Smeal core" across 9 of the 10 majors
  (Actuarial Science starts with MATH 140/141 instead). Scraped two new
  department catalogs (BLAW, RM). Fixed a real bug in
  `_extract_major_from_prompt`: when two aliases matched at the same start
  position, the tie-break kept whichever was inserted first rather than the
  more specific one, so "business analytics major" would have silently
  matched the generic Intercollege BUSINESS alias — now the longer alias
  wins. Patched a fifth instance of the placement-gate prereq pattern
  (`ACCTG 211`/`SCM 200` requiring `MATH 21`). The option-deduplication
  engine fix from the Cybersecurity/Mathematics builds handled several of
  these majors' own overlapping "X or elective" bulletin slots correctly
  with zero data-level workarounds. 98 backend tests passing (was 69).
- 2026-08-10 — Shipped real Gen Ed course recommendations (§4), unblocking
  it: accessed `genedplan.psu.edu` (Aarush's logged-in session) for PSU's
  exact Gen Ed structure and confirmed every category has a public,
  scrapeable bulletin course list. Scraped ~4,460 approved courses across
  10 domains. Wired `GEN ED` slots to recommend real, eligible courses with
  the Firewall rule enforced (major-prefix courses excluded, except
  Inter-Domain). Found and fixed two real engine bugs (a Gen Ed slot never
  marked done, looping forever; the picked course's credit count overriding
  a slot's calibrated value, pushing Cybersecurity to a 9th term) and one
  API-layer bug (Gen Ed course titles silently dropped in favor of the bare
  code for any course outside an already-scraped department catalog). All
  18 majors re-verified at 0 warnings / 8 terms. Bookmarked two related
  asks for later: RateMyProfessor-based ranking (blocked — no
  course-to-instructor mapping exists anywhere in scraped data, and
  instructor assignments rotate every term) and chat-based transfer-credit
  capture (folds into the existing §5 effort once unblocked). 104 backend
  tests passing (was 98).
- 2026-08-10 — Wrote `PROJECT_VISION.md` from Aarush's own words (the
  project's mission, 7 core requirements, 3 nice-to-haves), then shipped
  two of the nice-to-haves he picked as next priority: a real by-category
  progress breakdown (`plan_progress()` now returns `by_category` —
  major/gen_ed/world_language/supporting/elective/other, each with a
  rounded percent) and a full frontend redesign — `@angular/router` added,
  `AppComponent` reduced to a thin shell around a sidebar nav + persistent
  chat toggle, all state extracted into `PlannerStateService`, six routed
  pages (Home, Flowchart, Progress, Recommendations, plus two "coming
  soon" stubs for General Education and Transferred Courses). Sketched the
  layout as a mockup and confirmed two real UX calls with Aarush before
  writing any code (chat as a toggle not a column; Home as a new dashboard
  not the old flowchart view). Caught and fixed a real bug during browser
  verification: `app.py` was passing `by_category`'s snake_case inner keys
  straight through, so the frontend's `cat.totalItems` was silently
  `undefined` and the Progress page rendered empty. 104 backend tests
  passing throughout (no backend regressions from the frontend work).
- 2026-08-10 — Grouped the chatbot's Major dropdown by college (native
  `<optgroup>`, extracted from each plan title's trailing "(...)"
  college name, one older-catalog-year label normalized so it doesn't
  fork into a duplicate group). Frontend-only; 104 backend tests
  unaffected.
- 2026-08-11 — Extended catalog-year back-referencing (§2) from
  CMPSC/PREMED to all 18 majors: 61 new historical `degree_plans/*.json`
  files (16 majors × 2022-2025, minus BAIS which doesn't exist before
  2025). Filtered the 16 research agents' reported year-over-year diffs
  down to the ones this schema actually represents, so only 8 majors
  needed a distinct historical variant (ENGL, NURS, BUSINESS, CYBER, FIN,
  BIOL, MATH, ACTSC) — the other 7 (MGMT, ACCTG, CIE, SCM, MKTG, REST, RM)
  reuse the current plan across all 4 archived years. Rewrote
  `TestHistoricalCatalogYears` to discover `(major, year)` pairs from disk
  instead of a hardcoded list, which caught a real gap (`FIN-2025.json`
  missing from the first build pass) that a fixed-list test would have
  silently skipped. All 87 (major, year) plans verified at 0 warnings /
  goal met. 105 backend tests passing (was 104).
- 2026-08-11 — Shipped Phase A of §1's rollout order: the three Eberly
  College of Science majors that share BIOL's already-scraped department
  catalogs — Biochemistry and Molecular Biology B.S. (Biochemistry option),
  Chemistry B.S. (Analytical/Environmental option), Statistics B.S.
  (Statistics and Computing option, distinct from ACTSC's existing
  Actuarial Statistics coverage). Needed zero new department catalog
  scraping. Patched a sixth instance of the placement-gate prereq pattern
  (`STAT 184` requiring `MATH 21`) — and for the first time, the fix needed
  `concurrent_groups` rather than just adding alternates, since the
  bulletin schedules `STAT 184` in the same term as `MATH 140` (same
  mechanism as `CHEM 110`'s original concurrency fix in §2). All three
  plans passed at 0 warnings / 8-term graduation on the first simulation.
  114 backend tests passing (was 105).
