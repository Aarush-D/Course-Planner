# Branch Campus Findings — parked for later

This app is currently scoped to **University Park only** (per explicit
instruction). Every major/minor build this session has been University
Park's own version. Along the way, research repeatedly surfaced real PSU
programs that only exist at branch ("Commonwealth") campuses or World
Campus, not at University Park. Those got skipped or substituted rather
than built — this file is where that research is kept, so that whenever
branch-campus support actually gets built, it's a lookup, not a re-scrape.

**Not exhaustive.** This only captures branch-campus findings that came up
incidentally while researching University Park programs. A real branch-campus
build-out needs its own systematic pass per campus, not just this list.

## How campus scoping shows up on bulletins.psu.edu

Every program page has a "Where can I complete this program?" section
listing its real campus(es). Many majors have multiple tiles on the
`/programs/` page — one per campus — with the *same* major name but a
different plan code and (often) a materially different curriculum per
campus. The pattern seen repeatedly:
- `<Major>, B.S. (Business)` or similar suffix → University Park
- `<Major>, B.S. (Behrend)` → Erie
- `<Major>, B.S. (Capital)` → Harrisburg
- `<Major>, B.S. (Berks)`, `(Altoona)`, `(Abington)` → those campuses

## Confirmed branch-campus-only or World-Campus-only programs

Found while researching University Park minors/majors this session —
each of these was checked directly against its own bulletin page's
"Where can I complete this program?" section, not assumed:

| Program | Real campus | Found while researching |
|---|---|---|
| Computer Science, Minor | Behrend (Erie) and Capital (Harrisburg) only — two separate pages, neither at UP | CPTSC minor (substituted with Computational Sciences) |
| Business Administration, Minor | Capital (Harrisburg), with an Abington option | Business & Management minor batch (substituted with Entrepreneurship and Innovation) |
| Management Information Systems, Minor | Behrend (Erie) only | Business & Management minor batch (substituted with Information Systems Management, a real Smeal/UP minor) |
| Human Resource Management, Minor | Capital (Harrisburg) | Business & Management minor batch (substituted with Labor and Human Resources, a real Liberal Arts/UP minor) |
| Business, Minor | "University College" — Commonwealth Campus administrative unit, not UP | Business & Management minor batch |
| Organizational Leadership, Minor | Explicitly designed for World Campus (online) students, not an in-person UP offering | Business & Management minor batch (skipped; not clearly UP) |
| Accounting, B.S. | Multiple campus-specific variants exist (Abington, Altoona, Behrend/Erie, Berks) alongside the real UP one (suffixed "(Business)") | General majors research — UP variant is what's built |

## What a real branch-campus build-out would need

1. **Per-campus program lists.** The `/programs/` page's tile hover text
   ("Campus: X") is the source of truth per program — it needs a systematic
   pass, not incidental discovery like this file.
2. **A campus dimension on `degree_plans`/`minors` file naming or metadata.**
   Right now every plan file assumes University Park; a real multi-campus
   model needs either a `campus` field in the JSON or a folder-per-campus
   layout, plus catalog data scoped per campus (some branch campuses don't
   offer every course a UP plan expects).
3. **Frontend campus selector wired to filtering, not just display.** The
   campus dropdown in the chat panel already exists and is sent to the
   backend, but `/api/degree-plans` and `/api/minor-plans` don't currently
   filter by it — every major/minor returned is the UP one regardless of
   the selected campus. That's the actual gap to close first.
4. **Re-verify prereq/catalog data per campus.** Course offerings and even
   course numbering can differ by campus (e.g., some campuses cap out
   before 400-level courses); a UP-verified prereq chain isn't guaranteed
   to hold at a branch campus.

## Next step, when it's time

Start with #3 above (wire the existing campus selector to actually filter),
since the UI plumbing already exists — that's the highest-leverage, lowest-effort
first slice, and would surface exactly how much of #1/#2/#4 is needed for
the specific campus picked first.

---

# 2026-08-20 update — systematic scoping pass

Everything below is new research done to turn the incidental findings above
into an actionable, costed plan. Unlike the section above, this pass
deliberately picked real majors/minors already built for UP and checked
their actual bulletin pages at branch campuses, side by side.

## 0. Correction: item #3 above is already done

Before doing new research, I checked whether "wire the campus selector to
filter" (the prior doc's recommended next step) was still open. It is not —
it shipped in commit `59429fa3` ("Add real minor catalog, N-major/minor chat
UI, and two real-usage fixes"):

- `Backend/planner_engine.py` `list_degree_plans()`/`list_minor_plans()`
  already accept a `campus` param, default every plan file without a
  `campus` key to `"University Park"`, and filter on exact (case-insensitive)
  match. `PSU_CAMPUSES`/`DEFAULT_CAMPUS` already live at the top of that file
  (lines 30–60), exactly where this task description pointed.
- `GET /api/campuses` (`Backend/app.py:304-306`) already returns the full
  campus list; `GET /api/degree-plans` and `GET /api/minor-plans` already
  accept `?campus=`.
- `Backend/tests.py` (~lines 8096–8130) already covers this: campuses
  endpoint contents, default-all-UP behavior, empty result for a non-UP
  campus, and that filtering to `University Park` explicitly matches the
  unfiltered result.
- The frontend (`Frontend/src/components/chatbot/chatbot.component.ts`/`.html`)
  already has a bound `<select>` for campus, sends `campusChanged`, and
  already shows an honest empty state: *"We don't have degree plan data for
  {campus} yet — only University Park is supported right now."*
  (`chatbot.component.html` line ~29, gated by a `noProgramsForCampus()`
  computed signal).

So the "cheap plumbing" phase is finished and shipped. What's actually
missing is **real per-campus data** — there is no campus for which any
`degree_plans/` or `minors/` file has anything other than an implicit/no
`campus` key (= University Park). The rest of this update is about what it
costs to add real data for a second campus.

## 1. How much curriculum actually differs by campus (the key question)

Checked 5 majors already built at UP — CMPSC, BIOL, BUSINESS, MGMT, NURS —
against their real bulletin pages, plus branch-campus equivalents where they
exist. **The answer is "it depends entirely on the major," split into two
genuinely different patterns:**

### Pattern A — same degree, same courses, everywhere ("2+2 / shared curriculum")

**Management, B.S.** (`bulletins.psu.edu/undergraduate/colleges/smeal-business/management-bs/`) —
one single program page lists 20 campuses ("Abington, Altoona, Berks, Beaver,
Brandywine, DuBois, Erie, Fayette, Greater Allegheny, Harrisburg, Hazleton,
Lehigh Valley, Mont Alto, New Kensington, Shenango, Schuylkill, University
Park, Wilkes-Barre, Scranton, York") with **one course list, one plan code,
one credit total** (120 min). The only campus-specific rule: "eighteen
upper-level credits must be completed with Management faculty at University
Park" — i.e. this is a 2+2 pathway, not a different curriculum. Same courses
everywhere; only the *location* of the final two years differs.

**Business, B.S. (Intercollege)** (`bulletins.psu.edu/undergraduate/colleges/intercollege/business-bs/`) —
same pattern: one page, one course list (ECON 102/104, BA 321/322/420/421/422W,
FIN 301, IB 303, MGMT 301, MIS 301, MKTG 301, SCM 301, etc.), 18 campuses
listed. The only real per-campus variation is which of the 7 *options*
(Accounting, Business Analytics, Entrepreneurship, Financial Services,
Health Services, Individualized, Management and Marketing) each campus
offers — e.g. Entrepreneurship is "Altoona, World Campus" only; Health
Services is "Abington, Lehigh Valley, World Campus" only. The base
curriculum is identical; only which optional specialization track is
locally available changes.

**Biology, B.S.** (`bulletins.psu.edu/undergraduate/colleges/eberly-science/biology-bs/`) —
same pattern again: one page, 11 campuses listed (Abington, Altoona, Beaver,
Berks, Brandywine, Harrisburg, Lehigh Valley, Schuylkill, Scranton,
University Park, York), one shared 124-credit core curriculum. Only the six
*options* (Ecology, General Biology, Genetics/Dev Bio, Neuroscience, Plant
Biology, Vertebrate Physiology) are campus-restricted — e.g. Neuroscience
and Plant Biology are University Park-only; Ecology is Altoona + UP only.

**Nursing, B.S.N.** (`bulletins.psu.edu/undergraduate/colleges/nursing/nursing-bsn/`) —
same shape: one page, one shared course list per option (General Nursing,
RN-to-BSN, Second Degree), but the *options themselves* are campus-gated
(General Nursing: Altoona, Erie, Fayette, Mont Alto, Schuylkill, Scranton,
UP; RN-to-BSN: Abington, Shenango, UP, World Campus) and there's a hard
rule that "students start and remain at the campus of admission for the
entire program" — plus UP students specifically spend a full year in
residence at Hershey Medical Center. So Nursing curriculum is shared, but
*campus and option are coupled together* in a way that matters for planning
(you can't offer "Nursing @ Erie" using the General Nursing option's UP-only
Hershey-year requirement).

### Pattern B — genuinely different degree per campus ("separate programs, same name")

**Computer Science, B.S.** is the outlier and the important counter-example.
University Park's version, which is what `CMPSC-2026.json` already builds,
is `Computer Science, B.S. (Engineering)` — a College of Engineering degree,
127 credits minimum, plan code `CMPSC_BS`, requiring CMPSC 221, 222, 315,
316, 320, 360, 461, 465, 483W plus a CMPEN foundation choice
(`bulletins.psu.edu/undergraduate/colleges/engineering/computer-science-bs/`).

Two other, **structurally different** CS bachelor's programs also exist and
share the same major name:

- `Computer Science, B.S.` at Abington/Harrisburg
  (`bulletins.psu.edu/undergraduate/colleges/abington/computer-science-bs/`,
  `.../colleges/capital/computer-science-bs/`) — **120 credits**, run out of
  those campuses' own CS departments (not Engineering), a completely
  different required-course list (CMPSC 312, 330, 360, 430, 460, 462, 463,
  469, 472, 487W, 488 instead of UP's 221/222/315/316/320/461/465/483W), and
  a Data Science option (with `DS 220`) that doesn't exist in the UP
  Engineering version at all. This is not "the same degree at a different
  location" — it's a different program that happens to share a name and a
  major code prefix.
- `CSENG_BS`-coded seats at Beaver, Brandywine, and Hazleton, still tied to
  the *same* Engineering CMPSC_BS curriculum page as UP, just with a lower
  admission GPA threshold (2.60 vs. UP's 3.20) as a 2+2 feeder pathway —
  this one IS Pattern A (shared curriculum, different admission gate).

So even within a single already-built major (CMPSC), there are three
distinct cases in play at once: Pattern A (Beaver/Brandywine/Hazleton
2+2 into the same degree UP has), Pattern B (a wholesale different CS degree
at Abington/Harrisburg with a different course list and credit total), and
"not offered at all" everywhere else.

### Bottom line for feasibility/cost

Pattern A is by far the common case among the majors sampled (Management,
Business, Biology, Nursing, and 3 of Computer Science's 4 campus variants).
For Pattern A majors, branch-campus support really is close to "just a
lookup table of which campuses offer this major/option," because the
`semesters` course sequence in the existing UP JSON is still correct data —
only the metadata (which campuses can use this plan, which options are
gated to which campus) changes.

But Pattern B is real and not rare enough to ignore — Computer Science, one
of the 5 sampled, needs an entirely separate `degree_plans` file for
Abington/Harrisburg because the required courses and credit total are
different, not just relabeled. Any honest scoping plan has to assume a
nontrivial fraction of majors (my rough estimate, unverified beyond this
5-major sample: probably higher for majors with campus-specific home
departments — Engineering, Business/Smeal-adjacent professional programs —
lower for College of Liberal Arts / Eberly Science majors that are
UP-administered with 2+2 feeder pathways) will need Pattern B treatment and
must be checked individually, not assumed away.

## 2. Does the bulletin publish which campus offers which course? No.

Fetched `bulletins.psu.edu/university-course-descriptions/undergraduate/cmpsc/`
(the same page family `Courseplanner.py`'s `scrape_psu_dept_catalog()`
already scrapes — see `Backend/Courseplanner.py:111`) and checked the
per-course metadata directly. Confirmed: course description pages show
course number/title, credits, description, prerequisites/concurrent
requirements, cross-listings, Gen Ed designations, and learning objectives —
**no campus or location field at all**, for CMPSC 465 or any other course
on the page.

This means "does Altoona offer CMPSC 465 in Fall 2027" is **not answerable
from the bulletin** (the data source this app already scrapes) at all. That
question only has an answer in PSU's actual term-by-term Schedule of
Classes / LionPATH, which is a different, non-bulletin system this app has
no access to and would need a new scraper/API for. Concretely: this app can
determine "is Altoona *authorized* to offer this major/option" (bulletin
program pages, already scrape-able) but not "will this specific course
actually run at Altoona next semester" (scheduling data, not published in
the bulletin at all). Any campus-aware course-sequencing feature has to be
scoped around that hard limit — it can honestly say "this major has a
program at Altoona, offering these required courses," but can't verify
term-by-term availability without a fundamentally different data source.

## 3. Minor availability by campus — two different disclosure patterns

Checked 6 already-built minors' real bulletin pages, specifically their
availability language:

| Minor | URL | Availability pattern |
|---|---|---|
| Biology, Minor | `.../eberly-science/biology-minor/` | Open — "may be completed at any campus location offering the specified courses." No explicit campus list; advising contacts shown for UP, Abington, Altoona, Berks, Brandywine, Erie, Mont Alto, Scranton, Schuylkill, York |
| Chemistry, Minor | `.../eberly-science/chemistry-minor/` | Open — same boilerplate; advising contacts only at UP, Altoona, Berks, Erie |
| Economics, Minor | `.../liberal-arts/economics-minor/` | Open — same boilerplate; advising contacts only at UP and World Campus |
| History, Minor | `.../liberal-arts/history-minor/` | Open — same boilerplate; advising contacts at UP, Abington, Altoona, Berks, Shenango, World Campus, York |
| Mathematics, Minor | `.../eberly-science/mathematics-minor/` | Open — same boilerplate; advising contacts only at UP, Altoona, Harrisburg |
| Computational Sciences, Minor | `.../engineering/computational-sciences-minor/` | Open — same boilerplate; only UP has an advising contact listed |

**None of these 6 have an explicit "Where can I complete this program?"
campus list** the way the prior research's *restricted* minors did
(Computer Science Minor at Behrend/Capital only, Business Administration
Minor at Capital only, MIS Minor at Behrend only, HR Management Minor at
Capital only — see table above). Instead, general department minors use
open boilerplate language ("any campus offering the specified courses") and
list per-campus academic-advising contacts, which is a soft signal of
practical availability, not a formal enrollment restriction.

This means minors bifurcate into two very different data-quality
situations:
- **Restricted/professional minors** (small number, mostly
  Business/Smeal-adjacent and a few Engineering ones found so far): the
  bulletin gives an authoritative, small, explicit campus list — cheap and
  reliable to encode.
- **Open/general department minors** (the majority — everything sampled
  this pass): the bulletin explicitly declines to give a campus list and
  punts to "wherever the courses are offered," which is exactly the
  unknowable-from-the-bulletin course-scheduling question from finding #2.
  The advising-contact list is a reasonable proxy (a campus without a
  contact for that minor probably doesn't practically support it) but it is
  a proxy, not the source of truth, and should be labeled as such if used.

## 4. Data model implications

Given 1–3, here's what's cheap vs. expensive, concretely:

**Cheap — no new JSON schema field types needed:**
- The `campus` field and campus-filtering logic already exist
  (`Backend/planner_engine.py`) and already default missing `campus` to
  University Park. No engine change needed for Pattern A majors/minors.
- For a **Pattern A major** (Management, Business, Biology-core, Nursing
  General/RN-to-BSN as long as the option-campus coupling is respected):
  adding a branch campus is *not* "build a new `semesters` array" — the
  existing UP `semesters` sequence is still curriculum-accurate. It's
  closer to adding a `campuses: [...]` (or `available_campuses`) list to
  the existing plan JSON, and, where a plan bundles multiple `options`
  (Biology's six options, Business's seven), adding a `campuses` restriction
  per-option rather than per-file. That's a metadata-only change to files
  that already exist.
- For **restricted minors** with an explicit bulletin campus list: a single
  `campus` (or `campuses`) field per minor file, same shape as majors.

**Expensive — genuinely new per-campus data collection:**
- For a **Pattern B major** (Computer Science's Abington/Harrisburg
  variant, and structurally any major whose branch-campus version is a
  separate bulletin page under that campus's own college, not a shared
  Intercollege/UP-college page): this needs an actual new file,
  e.g. `degree_plans/CMPSC-ABINGTON-2026.json`, built the same way the
  existing UP files were — full bulletin research, full course-by-course
  entry, its own `semesters` sequence — because the course list, credit
  total, and department are genuinely different. This is full research
  effort per campus per major, not a metadata tweak. The filename/lookup
  scheme would need a `campus` (or a campus suffix in the filename, mirrring
  how catalog years are already suffixed) to disambiguate `CMPSC-2026.json`
  (UP, Engineering) from a hypothetical `CMPSC-2026-ABINGTON.json`
  (Abington/Harrisburg, standalone CS dept).
- For **open/general minors**: there is no authoritative per-campus course
  list to scrape from the bulletin (finding #2/#3) — determining real
  availability needs either (a) accepting the advising-contact-list proxy
  and labeling it as approximate, or (b) a genuinely different data source
  (schedule of classes / LionPATH), which is out of scope for a
  bulletin-scraping app as currently architected.
- **Prereq/catalog re-verification per campus** (item #4 from the original
  doc) is still real: `Courseplanner.py`'s dept-catalog scraper pulls from
  the same UP-agnostic university-wide course description pages checked in
  finding #2, so course *descriptions and prerequisites* are actually
  campus-independent (one CMPSC 465 description serves the whole university)
  — that part turns out to be cheaper than the original doc assumed. What's
  NOT campus-independent, and would need fresh bulletin research per
  campus, is the *degree-plan course sequence and requirement list* for any
  Pattern B major.

**Net shape of the schema change:** the existing `campus`-field-on-plan-JSON
design already wired into `planner_engine.py` is the right shape for
Pattern A. Pattern B needs the same field, just on genuinely new files.
Nothing found in this pass suggests the existing UP files need restructuring
— they stay as-is; the additive cost is per-campus-per-major research, sized
very differently depending on which pattern that major/campus pair falls
into.

## 5. Realistic phased build plan

**Phase 0 (done, shipped in `59429fa3`):** campus selector wired to real
`?campus=` filtering, `/api/campuses`, honest empty state, tests. No new
data collection. Confirmed complete in this pass (see section 0 above).

**Phase 1 — metadata-only Pattern A pass for one already-built, high-value
major.** Pick one Pattern-A major already in `degree_plans/` (Management or
Business are the cleanest examples found — single shared curriculum, wide
campus list, no per-option campus coupling to model beyond what's already
optional). Add a `campuses` (or `available_campuses`) field to that one
JSON file, verified against its real bulletin "campus availability" text,
prioritizing campuses NOT on the 2025-approved closure list (DuBois,
Fayette, Mont Alto, New Kensington, Shenango, Wilkes-Barre, York are
closing after Spring 2027 per Penn State's Board of Trustees vote —
building fresh per-campus data for those specific 7 is likely wasted effort
given the timeline). No new `semesters` research needed — this is a
one-file metadata edit plus a bulletin citation. Lowest cost, immediately
shippable, and it validates the schema end-to-end (engine filter → API →
frontend selector already all support it) with one real non-UP data point
instead of only "everything defaults to UP."

**Phase 2 — extend Phase 1's metadata pass to the rest of the already-built
Pattern A majors/minors.** Systematically check each of the ~230 existing
`degree_plans`/`minors` files' real bulletin page for its actual multi-campus
availability (most won't need new `semesters` data, per finding #1), and add
`campuses` metadata where the shared-curriculum pattern holds. This is
still "no new course-sequence research," but it is a full pass over the
existing catalog (not just 5 samples), so proportionally more bulletin-fetch
work than Phase 1, sized to the number of files, not the number of campuses.
Also encode the small set of *restricted* minors' explicit campus lists
found already (Computer Science Minor, Business Administration Minor, MIS
Minor, HR Management Minor from the original doc, plus any more found
during the pass).

**Phase 3 — first Pattern B major, one campus.** Pick the clearest Pattern B
case already confirmed (Computer Science at Abington/Harrisburg) and build
it as a genuinely new file the same way the original UP majors were built —
full bulletin research, full `semesters` sequence, its own prereq
verification. This is the first phase whose cost scales with real new
content, not just metadata, and it's the phase that will reveal whether the
filename/lookup convention (`<MAJOR>-<CAMPUS>-<YEAR>.json` or similar) needs
engine changes beyond what `list_degree_plans`/`list_minor_plans` already
support via the `campus` JSON field.

**Phase 4 — course-offering honesty pass.** Once Phases 1–3 establish which
majors/minors a campus can support at all, decide how to handle the
finding-#2/#3 gap (term-by-term course scheduling isn't in the bulletin).
Realistic options, increasing in cost: (a) label campus-filtered plans as
"program requirements — verify course scheduling with your campus advisor"
(no new data source, just honest UI copy — cheap); (b) build a LionPATH/
Schedule-of-Classes scraper or API integration to get real per-term,
per-campus course offerings (a genuinely new, separate data pipeline,
expensive, and PSU may not expose this publicly the way the bulletin is
public). Given the cost gap, (a) is the realistic near-term choice; (b)
should be treated as a separate, much larger project if ever pursued.

## Recommended first phase

**Phase 1 as scoped above: add real `campuses` metadata to one already-built
Pattern-A major (Management or Business), verified against its actual
bulletin page, prioritizing non-closing campuses.** Reasoning:

- The engine, API, and frontend already fully support a `campus` field on
  plan JSON (Phase 0 is done) — this phase is pure data, zero code changes.
- It's the cheapest possible next slice that produces a real, non-UP,
  bulletin-verified data point instead of speculative schema design.
- Management and Business are the two clearest Pattern-A cases found in
  this pass: one shared course list, wide campus list, no confusing
  per-option campus coupling to get wrong on a first attempt (contrast with
  Biology/Nursing, whose *options* are campus-gated, or Computer Science,
  which is Pattern B and needs a whole new file).
- It deliberately avoids the two genuinely expensive open questions found
  in this pass — Pattern B majors (new `semesters` research) and
  open-minor course-availability (no bulletin source of truth) — so it can
  ship on its own without pulling in Phase 3/4 scope.
- It naturally surfaces, with one concrete example, exactly how much
  process/tooling Phase 2's "walk all 230 files" pass will need, before
  committing to that larger effort.
