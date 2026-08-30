# Onboarding & Dashboard Redesign Plan

Written 2026-08-30, from Aarush's own words (captured verbatim in "The
vision" below). **Not started — this is a plan to mark down for future
completion, not a description of anything built yet.** Answers the
question `PROJECT_VISION.md`'s "Open questions for Aarush" already flagged:
*"For the frontend redesign (multi-page + hamburger nav) — worth a
dedicated design pass before touching code?"* — yes, and here's the shape
of it.

## The vision, in Aarush's words

> Our whole plan is to start with a cool professional landing page, and
> the landing page has a login/signup button that the users can use to
> create a profile or log into theirs. After that there should be a brief
> orientation that helps the user personalize. For this, look into the
> things that will help us in general and remove redundancies elsewhere,
> keep it fast and with a good UX. Once the orientation [finishes], the
> user is given the tour and [led] to the dashboard. The dashboard should
> be pretty good (currently our app minus redundancies, plus anything else
> it will benefit from).

Target flow: **Landing → Sign up / Log in → Orientation (personalize) →
Interactive tour → Dashboard.**

## The load-bearing discovery: most of this already exists

Every stage of the target flow already has a real, working building block
somewhere in the app. This is a **consolidation and re-sequencing project,
not a from-scratch build** — that changes both the scope and the risk
profile a lot, and should shape how this gets estimated whenever it's
picked up.

| Stage | What already exists | Where |
|---|---|---|
| Landing page | A genuinely designed, 6-section marketing page (problem statement → how it works → AI-vs-engine distinction → scale/credibility → judgment calls → closing CTA), real type system (Fraunces + IBM Plex), already live at the GitHub Pages root. | `docs/index.html` |
| Sign up / log in | Full email+password auth, `/login` route, sign-up/sign-in mode toggle, "Real account — saves your plan" badge, cross-link to the demo/no-account path. | `Frontend/src/pages/student-login-page/`, `Frontend/src/services/supabase.service.ts` (`signUpStudent`/`signInStudent`) |
| Orientation / personalize | The exact "personalize your plan" form (campus, major, minors, number of majors, start year, graduation timeline) already exists and is already shown as a first-visit modal today. | `Frontend/src/components/planner-setup/` (reused both as the onboarding modal's content and as the standalone `/your-plan` page) |
| Tour | Already rebuilt this session: 5 steps, sidebar overview → chat → "tell it what you've taken" → theme → help. | `Frontend/src/services/tour.service.ts`, `Frontend/src/components/tour-overlay/` |
| Dashboard | The current Home/Flowchart/Progress/Recommendations pages, already behind a router + persistent sidebar shell. | `Frontend/src/pages/home-page/`, `Frontend/src/app.component.html` |

**What's actually missing**: the *sequencing and wiring* between these
pieces, and the CTA framing. `docs/index.html`'s primary button currently
says "Launch the planner ↗" and drops straight into the Angular app's Home
page — no signup/login step at all today. The Angular app's own first-visit
modal ("Welcome to your Course Planner...") also runs independently of
login state — it doesn't know or care whether anyone is signed in. These
two things were built at different points this session for different
reasons and were never connected into one flow.

## Redundancies to remove (per "remove redundancies elsewhere")

Today, a first-time signed-out visitor who clicks through everything sees
**three** separate "getting started" surfaces in a row, not one coherent
orientation:

1. The onboarding intro modal — "Take the interactive tour" / "Give me a
   quick explanation instead" / "Skip — let me set up my plan now"
   (`app.component.html`, gated on `!planner.onboarded()`).
2. A *second*, separate modal — "A few things to set up once" — which is
   just `<app-planner-setup>` shown inline as a modal.
3. The same `<app-planner-setup>` form again, permanently, on `/your-plan`.

None of this is wrong, exactly — (1) and (2) are sequential, and (3) is a
legitimate "come back and change it later" surface — but stacked together
for a first-time visitor it reads as two modals in a row before they reach
anything real, and the tour-vs-setup ordering isn't obviously motivated.
The redesign should fold (1) and (2) into **one** orientation step
(personalize *is* the orientation — no separate "do you want a tour or an
explanation or to skip" branch point needed if signup already implies
"yes, I want to be here"), with the tour offered as a single, skippable
step immediately after, not as one of three competing choices upfront.
`/demo-login`'s "try a demo student" path is a legitimate *separate* entry
point for a visitor who wants to look around before committing to an
account — it should probably move to be a secondary link on the landing
page itself ("just want to look around first?") rather than buried in the
sidebar nav where it competes for attention with real navigation.

## Two things to fold in from the same conversation

**A course prerequisite/unlock explorer, next to the Flowchart.** Requested
separately, same session: a search box where a student can look up *any*
course (not just ones already on their own unlock map) and see what it
requires and what it unlocks next. This is a real, separate feature — not
part of the onboarding/dashboard sequencing work above, but belongs on the
resulting "dashboard" as a Flowchart-adjacent panel. Needs its own scoping
pass (does the backend have a way to look up prereqs/unlocks for an
arbitrary course code today, independent of a student's own major and
completed courses, or does that need new engine support?) before this can
be estimated — not yet investigated.

**A larger seeded test dataset for course info/timing.** Also requested,
phrasing was terse ("creating a larger data base with test data for
testing the course information/timing, testing and making a course
selection/planning area") — read as: the course-explorer feature above (and
course-information display generally) deserves more thorough test coverage
than today's per-major regression suite gives it, specifically around
*timing* edge cases (fall-only/spring-only offerings, multi-year catalog
changes, a course that moves prerequisite requirements between catalog
years). **This needs a real scoping conversation before it's actionable** —
open questions: is this new `Backend/tests.py` fixtures, a real seeded
Supabase dataset, or something else? What "timing" edge cases matter most?
Flagging the ambiguity here rather than guessing at a shape.

## Design guidance for whenever this gets built

- The landing page is the one surface in this whole app where an
  editorial, "make an opinionated visual statement" treatment is
  appropriate — the dashboard/app itself should stay the utilitarian,
  information-dense tool it already is. When this is picked up, load the
  `design-taste-frontend` or `high-end-visual-design` skill specifically
  for the landing page's own polish pass (it already has real bones —
  this would be a refinement, not a rebuild), and consider a
  `web-design-guidelines` review pass once the new signup/orientation flow
  is wired up, since that's new interactive surface the existing landing
  page never had.
- "Keep it fast and good UX" — concretely, that likely means: no more than
  one orientation screen (reuse the existing `planner-setup` form as-is,
  don't add new fields), the tour stays skippable at every step (already
  true), and sign-up shouldn't block on anything not already required
  today (email + password only, matching the existing `signUpStudent`).

## Open questions for Aarush before implementation starts

1. Should signing up become the *only* path into the app from the landing
   page, or does "Launch the planner" (no account) stay as an equally
   prominent option, with the demo-student path as a third, quieter link?
   The current login page's own copy ("Completely optional — you can keep
   planning with no account at all") suggests accounts should stay
   optional — worth confirming that's still true once there's a dedicated
   landing-page CTA specifically for signing up.
2. Does the orientation step gain any new fields specific to
   personalization beyond what `planner-setup` already collects (campus,
   major, minors, timeline), or is the existing form genuinely enough?
3. Scope and shape of the course prerequisite/unlock explorer and the
   test-dataset request above — both need their own short scoping pass
   before they're estimable, not just green-lit as-is.
