# Graduate Programs & World Campus — scoping research

This app is currently scoped to **University Park undergraduate** majors and
minors only. `Backend/planner_engine.py`'s `PSU_CAMPUSES` constant already
lists `"World Campus"` as campus #20 in the selectable dropdown (see
`Backend/planner_engine.py:57`), and the campus-filtering plumbing described in
`BRANCH_CAMPUS_FINDINGS.md` treats it as just another string — but **zero**
plan file in `Backend/degree_plans/` or `Backend/minors/` sets `"campus"` to
anything other than the implicit `"University Park"` default (confirmed:
`grep -rl '"campus"' Backend/degree_plans Backend/minors` returns 0 files).
World Campus is a placeholder today, not a real feature. There is also
currently zero graduate-program support of any kind — no directory, no
loader, no schema field for degree level.

This file researches what real build-out of each would require, so a future
session doesn't have to re-derive it. Nothing in this file changes any code
or data.

## How the current engine works (for reference)

`Backend/planner_engine.py`'s `load_degree_plan` reads
`degree_plans/<MAJOR>-<YEAR>.json` — a rigid, PSU-flowchart-shaped model:
a `semesters` array (`index` 1-8, one Fall/Spring term each) of `items`,
where each item is either `{"type": "course", "options": [...], "credits": N}`
(one or more interchangeable course codes) or `{"type": "slot", ...}` (a
GEN ED / elective slot, optionally with a `gen_ed` domain tag or a regex
`match` pattern). `build_full_plan`/`recommend_semester` walk this list in
order, gating each course on `Course.prereq_groups` (an AND-of-OR structure
scraped from each course's own "Enforced Prerequisite at Enrollment" bulletin
text) and packing a max-credits-per-term budget. `plan_progress` marks an
item done when a completed course matches one of its `options`.

`load_minor_plan` (`Backend/minors/<CODE>-<YEAR>.json`) is the same course/slot
item vocabulary but **flat** — a single `requirements` list, no semester
structure at all, since minors aren't published as term-by-term flowcharts.
This flat shape turns out to be the closer analog for most of what follows
below, for both graduate programs and World Campus.

---

## Part 1 — Graduate Programs

### How bulletins.psu.edu structures the graduate bulletin

The graduate bulletin root (`https://bulletins.psu.edu/graduate/`) is not
organized primarily by college the way the undergraduate bulletin is. It's a
search-first hub with three parallel program-type listings, all reachable
from `https://bulletins.psu.edu/graduate/programs/`:

- **Graduate Major Degree Programs** — the bulk of the catalog, one page per
  program (e.g. `.../graduate/programs/majors/computer-science-engineering/`)
- **Graduate Minor Programs** — only ~13 of these exist university-wide
- **Graduate Certificate Programs** — 200+, a much bigger and more granular
  category than undergraduate certificates

Program names carry a campus/unit suffix the same way undergrad major tiles
do, e.g. `Computer Science (Capital)`, `Computer Science (Great Valley)`,
`Business Administration (Smeal - M.B.A., D.B.A.)`, `Business Administration
(Smeal - Ph.D., M.S.)` — note research-track and professional-track MBA-family
programs are **separate program pages**, not options within one page.

Each program page does follow the same CourseLeaf-generated section
convention as an undergrad major page (`_admissionrequirementstext`,
`_degreerequirementstext`, `_coursestext`, etc. as separate anchors/PDFs), so
the scraper conventions this app already uses for undergrad program pages
would mechanically extend to grad pages. The content within those sections is
where things diverge sharply from undergrad.

### Three real programs, fetched directly

**1. Computer Science and Engineering** —
`https://bulletins.psu.edu/graduate/programs/majors/computer-science-engineering/`

Three separate degree options live on *one* program page, each with its own
credit total and structure:

- **M.Eng. (Master of Engineering)** — 30 credits min (≥18 at 500/800-level,
  ≥6 at 500-level), **non-thesis**, culminates in a master's paper during
  CSE 594. This is the one program found in this research pass that **does**
  publish a real semester-by-semester sequence: Fall (CMPSC 465 + 6cr
  security/database or architecture/embedded electives + 3cr special topics
  = 12cr), Spring (12cr of CSE 500-589/597), Summer (CSE 820 Software &
  Hardware Project Management 3cr + CSE 594 Research Topics 3cr = 6cr).
- **M.S.** — 30 credits min (≥21 at 500-level+, ≥15 of CSE 500-level
  specifically), split into **Thesis** (≥6cr thesis research, CSE 600/610) or
  **Scholarly Paper** (3cr CSE 594 + 3cr more CSE 500-589/597) tracks — a real
  branch point with a different credit mix per track, not just a checkbox.
- **Ph.D.** — 33 credits minimum without a prior M.S., or 21 credits with one
  in a related field; dissertation-based, with qualifying exam (by 3rd
  semester), comprehensive exam (after coursework), and a final defense —
  none of which are courses.
- **Admission**: GRE required for M.S./M.Eng applicants (not required for
  Ph.D. applicants), statement of purpose, resume/CV, 3 recommendation
  letters, TOEFL/IELTS for international applicants.

**2. Business Administration (Smeal — M.B.A., D.B.A.)** —
`https://bulletins.psu.edu/graduate/programs/majors/business-administration-smeal-professional/`

- **M.B.A.** — 35 credits total at the 500/800 level (≥18 at 500/800, ≥6 at
  500 specifically). Presented as a **flat list** of required courses with no
  semester sequence at all: Quantitative Analysis (2cr), Economics for
  Managers (2cr), Marketing Management (2cr), Management (2cr), Team Process
  and Performance (2cr), Ethical Leadership (2cr), Negotiation Theory and
  Skills (1cr), Supply Chain and Operations Management (2cr), Financial
  Accounting (2cr), Business Statistics (2cr), Communication Skills for
  Management (4cr), Managerial Accounting (2cr), Emerging Technology Trends
  (1cr), Finance (2cr), Global Business Environment (1cr), Global
  Perspectives (1cr), Global Immersion (1cr), Leadership Immersion (2cr),
  Strategic Management (2cr) = 35cr.
- No thesis; culminating experience is the capstone course itself (Strategic
  Management), not a separate research product.
- **Admission**: explicitly **no GMAT/GRE** ("scores will not be accepted"),
  application essay, resume emphasizing professional experience, 3
  professional references. Pre-program competency expectations in
  accounting/economics/math/statistics exist but are satisfied by background,
  not a bulletin course chain.

**3. Mechanical Engineering** —
`https://bulletins.psu.edu/graduate/programs/majors/mechanical-engineering/`

- **M.S. only** (no M.Eng. option for this department) — 30 credits at
  400-level or higher (≥20 earned at Penn State, ≥18 at 500-600 level, ≥12 in
  ME specifically, 3cr math from an approved list). Also a flat list, no
  semester sequence.
- **Option A (Thesis)**: 24 course credits + 6 thesis credits.
  **Option B (Paper)**: 30 course credits, no thesis — and *"online students
  may only choose this option"*, an explicit thesis/non-thesis gate tied to
  delivery mode that has no undergrad equivalent.
- **Ph.D.**: 30 credits above the master's before the comprehensive exam;
  the page itself calls the Ph.D. structure "quite flexible, with minimal
  formal requirements" — i.e. deliberately not a fixed checklist.
- **Admission**: bachelor's degree "in a suitable engineering field"
  (an unenforceable, non-course prerequisite), no GRE accepted.

### Data model implications for graduate programs

**The single biggest finding: most graduate programs are NOT semester-flowchart-shaped.** Of the three fetched, only CSE's M.Eng. option
publishes real Fall/Spring/Summer course assignments; the M.B.A. and the ME
M.S. are both flat requirement lists (closer to how this app's
`minors/*.json` already models a minor than to `degree_plans/*.json`'s rigid
8-semester walk). That means a graduate loader can't assume one shape the way
`load_degree_plan` safely assumes "8 semesters" today — it needs per-program
judgment about whether a real sequence exists, exactly the same editorial
call already made per-major for undergrad, just with a different default
(flat, not semester) in the common case.

Concretely, extending the existing schema/engine would need to handle:

1. **Multiple named degree options per program, each with its own credit
   total and course pool.** Today one `major` code maps to one plan file with
   one requirement structure. A graduate program needs, at minimum, an extra
   dimension: `CMPSC` (grad) → `M.S. (thesis)` / `M.S. (scholarly paper)` /
   `M.Eng.` / `Ph.D.`, each a genuinely different plan, not a filter over one
   shared list. The cleanest fit within the existing file-per-plan convention
   is one JSON file per **program + degree-option** pair (e.g.
   `CSE-MENG-2026.json`, `CSE-MS-THESIS-2026.json`), the same way this app
   already gives CMPSC and a hypothetical second major separate files, rather
   than trying to cram branching logic into `merge_plans` (which exists to
   combine *independent* requirement sets, not to pick *one of several*
   mutually exclusive tracks within a single program).
2. **Thesis/dissertation research credits don't fit the `{"type": "course",
   "options": [...], "credits": N}` item shape.** "≥6 credits of CSE
   600/610" is a repeatable, variable-credit placeholder, not a fixed course
   a student either has or hasn't taken — structurally closer to the
   variable-credit internship workaround already used for undergrad
   capstones (e.g. `RHS-2026.json`'s note on modeling RHS 495A's 6-12cr range
   at a single representative value) than to a normal prereq-gated course,
   and that workaround is already an approximation for undergrad, let alone
   for something as open-ended as dissertation research.
3. **Admission requirements have no representation in the schema at all, and
   arguably shouldn't be forced into it.** GRE/GMAT score thresholds,
   "bachelor's degree in a related field," letters of recommendation, work
   experience — none of these are courses, none belong in `prereq_groups`,
   and none of this app's prereq machinery (`prereqs_satisfied`, ETM flags,
   `Course.prereq_groups`) has any way to represent "a numeric test score" or
   "a qualitative field-relatedness judgment." This is real information
   worth surfacing to a prospective student, but it's a pre-application
   eligibility checklist, categorically different from the in-progress
   prereq-chain tracking this engine does today — it would need its own
   separate, non-course-shaped feature, not an extension of `Course`.
4. **Non-course milestones** (qualifying exam, comprehensive exam,
   dissertation defense) are real "requirements" a Ph.D. student must clear,
   but they're not courses and carry no credits — `plan_progress`'s whole
   model of `credits_done / total_credits` and one-completed-course-per-item
   doesn't have a slot for "cleared a milestone."
5. **Where a real semester sequence exists (like CSE's M.Eng.), the existing
   `semesters[].items[]` shape reuses almost as-is** — same course/slot
   vocabulary, same prereq-gating logic, just far fewer terms (3, not 8) and
   with Summer as a real numbered term rather than an optional accelerator.

### Recommended first phase — graduate

Build **exactly one degree option** of **one program**, and pick the one that
minimizes the unknowns above rather than the most popular program: **CSE's
M.Eng.**, because it already has a real published semester sequence (no
"invent an ordering the bulletin never specified" judgment call needed) and
is non-thesis (no variable-credit thesis-research item to fake). Concretely:

- New `Backend/grad_plans/` directory (not `degree_plans/`, to keep degree
  level unambiguous rather than overloading the `major` field), one file,
  reusing the `semesters[].items[]` shape verbatim.
- Treat GRE/admission requirements as **inert metadata only** (a `notes` or
  `admission` string block, never gated or checked) — the engine has no way
  to enforce them and shouldn't pretend to.
- Explicitly skip the M.S. and Ph.D. options for this program in phase one —
  each is a materially different structure (flat list; thesis-credit
  placeholder; non-credit milestones) that deserves its own follow-up rather
  than being forced into the M.Eng.'s shape.
- Skip catalog scraping for 500/800-level graduate courses in this phase
  too if `scrape_psu_dept_catalog` doesn't already reach them — that's a
  prerequisite dependency worth confirming before promising a working
  `recommend_semester` for even this one plan.

**Honest scope note:** even this minimal slice is a bigger lift than it looks
— a new directory, a new loader mirroring `load_degree_plan`, a frontend
degree-level selector, and confirming the catalog scraper covers grad-level
courses, all before a single additional program is added. Any real breadth
beyond this one plan (dozens of programs across colleges, each independently
deciding thesis vs. non-thesis credit splits, whether a semester sequence
exists at all, and what its admission checklist looks like) is a **larger
effort than the entire undergraduate major+minor catalog built so far** —
undergrad had one recurring shape (8-semester flowchart) that mostly just
needed re-scraping department by department; grad has a materially different,
non-uniform shape *per program* that needs a fresh editorial judgment call
every single time, the same way picking CSE's M.Eng. over its own M.S./Ph.D.
siblings just required one.

---

## Part 2 — World Campus

### What's already latent in this app's own data

Before any new research: three plan files already built this session
silently ran into World Campus and left a trail in their `notes` fields —
worth surfacing since they're evidence the "University Park" default isn't
even accurate for every file currently in the repo:

| File | What the note says |
|---|---|
| `Backend/degree_plans/BUSINESS-2026.json` (and its 2022-2025 siblings) | "this Intercollege Business B.S. has NO University Park offering — it exists only at Commonwealth Campuses **and World Campus**" |
| `Backend/degree_plans/ESUS-2026.json` | "World Campus only — the bulletin's Suggested Academic Plan is published for World Campus, not University Park" |

Neither file sets a `"campus"` field (none do, repo-wide), so both are
currently mis-labeled as University Park by `DEFAULT_CAMPUS`'s fallback —
a real, if minor, existing data-accuracy gap independent of any future
World Campus feature work.

### Confirming a real World Campus program page directly

Fetched `https://bulletins.psu.edu/undergraduate/colleges/earth-mineral-sciences/energy-sustainability-policy-bs/`
(the ESUS plan's own source) directly: it states **Begin Campus: World
Campus / End Campus: World Campus**, confirming it's genuinely World-Campus-only,
not a UP program that happens to also be offered online. Its Suggested
Academic Plan lists Year 1 as (31 credits total): EMSC 302 (1cr), PLSC 1
(3cr), EMSC 240N (3cr), EGEE 120 (3cr), EGEE 102 (3cr), CAS 100 (3cr), GEOG
30N (3cr), ENGL 15 (3cr), plus three Gen Ed slots (3cr each) — and, critically,
the page itself notes **"the Bulletin only permits the listing of courses as
'years'"** rather than Fall/Spring terms. `ESUS-2026.json`'s own notes concur:
"the source's own year-by-year table lists uneven per-year credit totals
(31/33/30/26) rather than a clean semester split."

This is the single most important structural finding for World Campus:
**bulletins.psu.edu itself, for at least some World-Campus-only majors,
publishes sequencing at YEAR granularity, not semester granularity** — a real
mismatch with `degree_plans/*.json`'s `semesters[].index` (1-8, one per
Fall/Spring term) baked into `load_degree_plan`/`recommend_semester`. This
isn't a scraper artifact; it reflects that World Campus students don't move
through a fixed Fall/Spring cohort the way a residential student does —
courses run across standard Fall/Spring/Summer terms (16/16/12.5 weeks, same
official academic calendar per `registrar.psu.edu`) but also in additional
shorter 6- and 7.5-week sessions with rolling start dates, so a rigid
"semester 3 = these exact 5 courses" flowchart is a poor fit even when the
underlying course list is otherwise identical to a campus version.

### A second data point: the same major, two different presentations

Searched for a major that exists both at a physical campus **and** via World
Campus completion, to see whether the *curriculum* differs or just the
*presentation*: **Software Engineering, B.S.** — administratively based at
Erie/Behrend, with **"Begin at any campus, complete at Erie or World
Campus."**

- The Behrend bulletin page's own Suggested Academic Plan
  (`.../colleges/behrend/software-engineering-bs/`) is a standard Year
  1-4 × Fall/Spring table, e.g. First Year Fall: CHEM 110 (3cr) + CHEM 111
  (1cr) + CMPSC 121 (3cr) + ENGL 15/30H (3cr) + MATH 140 (4cr) + PSU 7 (1cr)
  = 15cr — structurally identical to every `degree_plans/*.json` this app
  already builds.
- The World Campus **marketing/enrollment** page for the same degree
  (`worldcampus.psu.edu/.../penn-state-online-software-engineering-bachelors-degree/courses`)
  presents the *same* 126 required credits as a **flat category list**
  instead — Prescribed Courses (86cr), Additional Courses (6cr), Supporting
  Courses (9cr), Electives (1cr), Gen Ed (by domain, not term) — with no
  Fall/Spring/Year structure at all.

Read together with the ESUS finding, this suggests two distinct sources with
two different shapes for what may be the *same underlying requirements*:
`bulletins.psu.edu` (this app's existing scrape target) tends to carry
*some* sequencing (semester for a physical-campus major, year-level for a
World-Campus-only major), while `worldcampus.psu.edu` (a separate site this
app has never scraped) presents things flat regardless. That's good news for
reuse — `bulletins.psu.edu`'s own program pages remain the right source, no
new site needs to be added to the scraper — but it also means a World Campus
build can't assume every program looks like ESUS's year-table; each program
needs the same "does this one have real sequencing or not" check graduate
programs need.

### Data model implications for World Campus

1. **Course numbering and Gen Ed structure are NOT different** for World
   Campus — courses are the same PSU course codes with the same catalog
   entries; nothing found in this pass suggests a parallel numbering scheme
   or a different Gen Ed domain list. This is the good news: `prereq_groups`,
   `norm_code`, and the Gen Ed domain data (`data/gen_ed_courses.json`) should
   all keep working unmodified for World Campus content.
2. **Term granularity is the real mismatch**, and it's uneven across
   programs: some World-Campus-only majors (ESUS) publish year-level
   sequencing only; a major offered at both a physical campus and World
   Campus (Software Engineering) may have a real semester sequence at its
   home campus that simply isn't republished at year/semester granularity on
   the World Campus consumer-facing page at all. `degree_plans/*.json`'s
   `semesters[].index` field would need to either (a) tolerate a coarser
   "year" grouping for World-Campus-only majors — e.g. one synthetic
   `index` per year instead of per term, closer to how `minors/*.json`
   already tolerates having no term structure at all — or (b) fall back to
   the flat `minors`-style `requirements` list for any World Campus program
   whose only public sequencing is year-level, same judgment call as
   graduate programs above.
3. **The existing `campus` field mechanism (from the branch-campus research)
   is architecturally sufficient for World-Campus-*exclusive* majors** like
   ESUS or the Intercollege Business B.S. — tag the one plan file
   `"campus": "World Campus"` and the filtering described in
   `BRANCH_CAMPUS_FINDINGS.md` #3 (wiring `/api/degree-plans` to actually
   filter by campus) handles it, once that wiring exists. It is **not**
   sufficient for a major like Software Engineering that's genuinely
   completable at more than one campus with potentially different
   sequencing per campus — that needs either multiple plan files per major
   (one per completion campus, already how `BRANCH_CAMPUS_FINDINGS.md`
   frames the general branch-campus problem) or a single file that can
   express "same requirements, but this campus's version has no semester
   sequence, just a flat list."
4. **Nothing found suggests World Campus needs a fundamentally different
   *prerequisite* model** — unlike graduate programs, there's no admission-
   score gating or thesis-track branching unique to World Campus itself.
   The gap is purely about how sequencing is (or isn't) published, not about
   a different requirement vocabulary.

### Recommended first phase — World Campus

Two candidates worth doing in this order, both small:

1. **Fix the existing mislabeling first — near-zero cost.** Tag
   `BUSINESS-2026.json` (and its 2022-2025 siblings) and `ESUS-2026.json`
   with `"campus": "World Campus"` (or, more accurately for Business, a
   value acknowledging it's also at Commonwealth Campuses — worth a product
   decision, not a research one). This alone doesn't require anything new
   architecturally: `PSU_CAMPUSES`, `DEFAULT_CAMPUS`, and the `campus` field
   convention already exist; it's purely a data-correctness fix that happens
   to also be the very first real World Campus plan(s) with an accurate
   campus tag, once `/api/degree-plans` actually filters by campus per
   `BRANCH_CAMPUS_FINDINGS.md` #3.
2. **Then build one net-new World-Campus-only program end to end** — ESUS is
   already 90% done as a proof point (it's already in `degree_plans/`,
   already flagged as World-Campus-only in its own notes, already
   demonstrates the year-vs-semester granularity problem) — the real
   remaining work is deciding how `semesters[].index` should represent
   "year, not term" for this one file (or converting it to the flat
   `minors`-style shape instead) and confirming `recommend_semester`'s
   term-by-term simulation still produces something meaningful when terms
   are actually years.

**Honest scope note:** World Campus is a smaller and more mechanical lift
than graduate programs — the course/prereq vocabulary carries over untouched,
and the campus-filtering scaffolding from the branch-campus research already
covers the World-Campus-*exclusive* case. The real cost is the same
per-program editorial judgment call as everything else in this app
(does this major have real sequencing or not, and at what granularity),
multiplied across however many of PSU's ~40 fully-online bachelor's programs
get built — smaller than the graduate effort, but still a multi-program,
multi-session undertaking, not a single afternoon's wiring change.
