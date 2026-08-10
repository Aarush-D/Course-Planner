# Course Planner — Project Vision

Written 2026-08-10, from Aarush's own words. This is the north star every
other doc (`EXPANSION_PLAN.md`, code comments, feature decisions) should
get checked against — when a new idea comes up, the first question is
"does this serve the mission below, and where does it fit."

## Mission

> A one-stop spot for PSU students to check their graduation progress and
> progression to date.

Aarush started this project because Penn State spreads the information a
student actually needs — major requirements, the flowchart, Gen Ed rules,
transfer credit equivalencies — across several disconnected university
tools (the Bulletin, LionPATH, the Gen Ed Planning Tool, the Transfer
Credit Tool, ...), each with its own UI and none of them talking to each
other. The Course Planner's job is to be the single place that already
knows all of it, for every student, every major, any time of day — not
just a nicer version of one of those tools.

## Core requirements (must-have)

These are load-bearing — every major and every feature eventually needs
all of them to actually deliver on the mission.

1. **Scale to every PSU major.** Not a subset — the whole catalog.
   *Status: `EXPANSION_PLAN.md` §1, in progress — 18 of ~194 majors built.*
2. **A real PSU flowchart per major**, built from the actual bulletin data,
   not a generic template. *Status: shipped for all 18 built majors —
   `degree_plans/{MAJOR}-{YEAR}.json` + `build_semester_flowchart()`.*
3. **Gen Ed integrated into the flowchart**, not bolted on separately —
   Gen Ed slots need to resolve to real courses to "maintain flowchart
   standards." *Status: `EXPANSION_PLAN.md` §4, shipped.*
4. **Keep the student on pace to graduate on time.** *Status: shipped —
   `build_full_plan()`'s goal-tracking (`goal.met`, term-by-term warnings)
   already does this for every major.*
5. **Multiple graduation-timeline options** (3-year, 4-year, etc.), not
   one fixed path. *Status: the engine already supports this
   (`grad_years` parameter, `TestYearPlanning` covers 3-year-with-summers)
   — but it isn't clearly surfaced as a first-class choice in the frontend
   yet. Likely needs UI work, not engine work.*
6. **Bring in PSU-approved transfer credits and have them actually update
   the flowchart** — not just a lookup table on the side. *Status:
   `EXPANSION_PLAN.md` §5 — distance ranking + schema + one real
   equivalency record shipped, blocked on scaling coverage data. This
   requirement reframes it: it's not a nice-to-have side tool, it's core
   to the mission, since "consolidate everything in one place" doesn't
   work if transfer credits live in a separate, disconnected flow.*
7. **Follow PSU's actual requirement types**: prescribed courses,
   department lists, Gen Ed, electives — each modeled faithfully, not
   flattened into one generic bucket. *Status: partially shipped. Prescribed
   courses and Gen Ed are real and course-level (§3–4). **Department-list
   "Supporting Course" slots are not** — every major built so far
   (`ACCTG 4XX`, `Supporting Course (department-approved list)`, etc.)
   still models these as generic placeholders, the same unsolved problem
   Gen Ed was before this session. This is a concrete gap the vision
   surfaces — see "Gaps" below.*

## Nice-to-haves

Valuable, not load-bearing — sequence these after the core list above is
in reasonable shape for a growing number of majors.

1. **A dedicated progress page** showing required-courses-for-graduation
   as a percentage, multi-page rather than squeezed into the chat panel.
   *Backend already computes the raw numbers (`progress.done_items` /
   `total_items`, `credits_done` / `total_credits`) — this is a frontend
   page, not new engine work.*
2. **Hamburger/sidebar navigation** — Home, General Education, Transferred
   Courses, Recommendations (exact set TBD, whatever reads cleanest).
   *Not started — the frontend is currently a single view
   (`app.component.ts` + chatbot/flowchart/recommendations panels), no
   routing or multi-page structure exists yet.*
3. **A cloned Transfer Credit Tool UI**, embedded as its own section, where
   a student can browse/add courses directly or tell the chatbot to add
   them. *Ties to the transfer-credit chat-capture idea already bookmarked
   in `EXPANSION_PLAN.md` §5 — this adds a UI-cloning dimension to that
   same bookmarked work, not a separate effort.*

## Gaps this vision surfaces (not previously tracked)

- **Department-list "Supporting Course" slots** aren't resolved to real
  courses. Every Smeal major, Math, Biology, etc. has these — they're
  currently the exact same kind of placeholder Gen Ed slots used to be.
  There's no scrapeable master list the way Gen Ed had one, though — "the
  department's approved list" isn't a single public bulletin page the way
  `bulletins.psu.edu/.../course-lists/quantification/` was, so this likely
  needs a different approach per department (probably: any course in that
  department's own scraped catalog, filtered by level/prereqs — worth
  scoping properly rather than assuming it's a straight repeat of the
  Gen Ed pattern).
- **Frontend information architecture** hasn't been designed around "one
  consolidated spot" yet — right now it's chat-first with a couple of
  panels, not a multi-page product with the sections Aarush describes.

## Open questions for Aarush

Sequencing calls only Aarush can make, given how much is now in scope:

1. Given 18/194 majors are built, does breadth (more majors) or depth
   (Supporting Course resolution, transfer credit scaling, the frontend
   redesign) come first?
2. For the frontend redesign (multi-page + hamburger nav) — worth a
   dedicated design pass before touching code, given it's a real UX
   decision, not just wiring?
3. Transfer credit scaling is still blocked on more sample data/PDFs from
   Aarush (per `EXPANSION_PLAN.md` §5) — is that still the plan, or is
   there a better source now?
