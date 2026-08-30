# QA Findings — 2026-08-30

Results of an extensive testing pass: backend regression suite, frontend
type-checking, a functional sweep of every page/flow via the Browser tool,
and a live probe of the advisor-workspace Supabase schema. One entry per
issue found, in order of severity.

## Fixed

1. **Security: advisor-only RLS policies checked "is logged in," not "is
   actually an advisor."** `review_requests`, `plan_comments`, and
   `meeting_proposals` all had `to authenticated using (true)` policies.
   Safe only because the sole sign-up path was advisor-only — the moment a
   second public sign-up path exists (student accounts), any student
   session would satisfy these checks and could list every other student's
   review request, forge a comment that looks like it came from an advisor,
   or load the advisor dashboard directly. Confirmed live by signing up a
   disposable account with no `advisor_profiles` row and verifying the old
   policies didn't distinguish it from a real advisor. **Fix**: new
   `supabase/migrations/0003_restrict_advisor_only_policies.sql` adds an
   `is_advisor()` helper and rewrites every affected policy to require a
   matching `advisor_profiles` row — needs to be run in the Supabase SQL
   editor (see the delivery note at the end of this work).

2. **Real bug: a student could never accept/decline a proposed meeting.**
   `ReviewRequestService.setMeetingStatus()` did a direct anonymous
   `.update({status}).eq('id', meetingId)` on `meeting_proposals`, but that
   table only ever granted `anon` UPDATE, never SELECT — and PostgREST
   needs SELECT on any column referenced in an UPDATE's WHERE clause.
   Confirmed live via curl: the anonymous update returned
   `42501 permission denied for table meeting_proposals`, and the row's
   status never actually changed. The straightforward fix
   (`grant select ... to anon`) was rejected because it would let anyone
   list every advisor-student meeting on the platform — the same
   enumeration risk `review_requests`' own SELECT policy was built to
   avoid. **Fix**: same `0003` migration adds a `respond_to_meeting_proposal`
   SECURITY DEFINER RPC scoped to one id, and
   `Frontend/src/services/review-request.service.ts` now calls it instead
   of the direct table update.

3. **Dark-mode contrast bug on the Recommendations page.**
   `recommendations.component.html` used a one-off teal accent
   (`text-teal-700`, `bg-teal-50 border-teal-200`, `hover:border-teal-400`)
   with no `dark:` variants — the only teal usage anywhere in the frontend
   outside the Progress page's intentional multi-color category map.
   Confirmed live: in dark mode the course title rendered as
   `rgb(15,118,110)` (medium teal) on a near-black card background, and the
   priority-score badge kept its light mint background entirely unstyled
   for dark mode. **Fix**: swapped to the indigo pairing already used
   elsewhere on the same page (`text-indigo-700 dark:text-indigo-300`,
   `bg-indigo-50 dark:bg-indigo-950`, etc.) — confirmed live post-fix,
   renders as light indigo on dark indigo, correct contrast.

## Fixed (second pass — verifying the four workstreams end to end)

4. **Tour tooltip rendered off-screen on the new "Your sidebar" step.**
   `tour-overlay.component.ts`'s `_tooltipPlacement()` picked between
   `'above'`/`'below'` by checking whether there was room in either
   direction — a check that assumed the target was small. The sidebar
   consolidation (12 steps → 5) made the first step target the whole
   `<nav>`, which spans the full viewport height, so there was no room in
   *either* direction and the logic always fell through to `'below'`,
   placing the tooltip's `top` past the bottom edge of the screen (confirmed
   live: `top: 671px` on a `649px`-tall viewport — the tooltip was
   completely invisible, though its content was correctly in the DOM).
   **Fix**: added a `'beside'` placement mode, used when the target's height
   leaves no real room above or below — positions the tooltip to the right
   of the target instead, anchored near the top of the viewport. Confirmed
   live post-fix: "Step 1 of 5 — Your sidebar" now renders fully visible;
   walked through all 5 steps end to end with correct content at each.

5. **Header buttons (Preferences/theme/help) could clip off-screen with the
   chat panel open, at narrower viewport widths.** Each of the 3 buttons
   independently hardcoded its own `right` offset, tuned for exactly 2
   buttons; adding the new Preferences control as a 3rd pushed the row's
   total width past what was available between the sidebar and the (fixed
   441px-wide) chat panel at narrower widths. Confirmed live: at a 533px
   viewport with chat open, the Preferences button's computed `right:
   552.5px` placed it entirely off the left edge of the screen (unreachable
   — a real usability bug, not just cosmetic). Note: the *original* 2-button
   layout was already marginal at this width (the theme toggle was
   partially clipped even before this session's changes) — this pass made
   an existing edge case worse, not introduced a new class of bug from
   scratch. **Fix**: replaced the 3 independently-`right`-positioned buttons
   with one `flex` row (single `right` offset, internal `gap`) in
   `app.component.html`, and made `PreferencesPanelComponent`'s own root
   `relative` instead of self-positioning `fixed` — removes the fragile
   hardcoded-per-button math entirely, so future buttons added to this row
   don't need new magic numbers. Confirmed correct at a realistic desktop
   width (1440px, comfortable gap before the chat panel) — see "Documented,
   not fixed" below for the residual narrow-viewport limit this doesn't
   (and can't, on its own) solve.

6. **Course rating submit — error path confirmed correct.** With
   `course_ratings` not yet migrated live, submitting a rating 404s as
   expected; confirmed the component's `catch` branch fires correctly (red
   error toast: "Couldn't submit your rating — try again in a moment.") and
   the modal stays open with the student's star pick and review text intact
   for a retry, rather than silently failing or losing their input.

7. **Student sign-up — confirmed graceful without migration 0005 live.**
   Signed up a real test account; it succeeds and lands on `/your-plan`
   even though the `student_plans` table doesn't exist yet (the autosave
   attempt 404s in the console but never surfaces to the student or blocks
   the sign-in). Sign-out correctly clears the session (nav reverts to
   "Sign in to save your plan") without disturbing the in-memory plan or
   the anonymous flow — confirms the "optional, additive" design goal
   actually holds up live, not just in the code's intent.

## Blocked pending the 3 unrun migrations

`0003_restrict_advisor_only_policies.sql`, `0004_course_ratings.sql`, and
`0005_student_plans.sql` are all written and committed but **not yet run**
against the live Supabase project (confirmed via direct REST probes: the
`respond_to_meeting_proposal` RPC, `course_rating_summary` view, and
`student_plans` table all 404). Everything above that depends on them
degrades gracefully in the meantime, but is not yet fully verified
end-to-end:
- Meeting accept/decline (needs `0003`'s new RPC).
- A submitted course rating actually persisting and appearing in aggregate
  (needs `0004`).
- A signed-in student's plan actually surviving a refresh (needs `0005`).
- The security fix itself doing its job (needs `0003`, plus a second real
  student account to test against — the RLS tightening can't be observed
  as "no longer exploitable" until there's an exploit attempt to run).

Re-verify all four once the migrations are applied.

## Confirmed working (no issues found)

- Backend: `python3 -m pytest tests.py -q` — 1468/1468 passing, fresh run.
- Frontend: `npx tsc --noEmit` — clean.
- Every router page (Home, Flowchart, Progress, General Education,
  Transferred Courses, Recommendations, Your Plan, Privacy, Terms,
  Demo Login) — no console errors across a full navigation sweep.
- Demo-student login flow (Alex Chen profile) — loads correctly, plan data
  renders end-to-end on Flowchart/Recommendations.
- "Request advisor review" — creates a real `review_requests` row via the
  `create_review_request` RPC (confirmed 200, returns the new id), and the
  resulting `?review=` link renders the student's read-only view correctly
  in a fresh tab.
- Advisor sign-in → dashboard → individual review page — all render
  correctly with real data, no console errors.

## Documented, not fixed (out of scope for this pass)

- **`build_semester_flowchart` and related fields are now dead code.**
  GitHub issue #2's fix (commit `bd35b305`) removed every frontend
  reference to the semester-by-semester Mermaid diagram, but
  `Backend/planner_engine.py`'s `build_semester_flowchart`, the matching
  `app.py` response field, and `course-plan.model.ts`'s `semesterFlowchart`
  type are still present and now unused. Not removed here — Flask stays
  untouched per this project's established precedent of keeping Supabase
  and UI work additive/isolated from the deterministic engine. Worth a
  dedicated small cleanup pass later.
- Everything already tracked in `docs/COMPLIANCE_BACKLOG.md` (security
  pen-test, accessibility audit, grade-minimum tracking, etc.) — unchanged,
  still deferred, not re-litigated here.

## Backlog cleanup

Removed the "Compact the 'Path to Graduation' flowchart" item from
`docs/UI_CHANGES_BACKLOG.md` — it described a collapse-completed-semesters
toggle for the semester-by-semester Mermaid diagram, which no longer
exists (see above).
