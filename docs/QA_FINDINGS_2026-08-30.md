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
