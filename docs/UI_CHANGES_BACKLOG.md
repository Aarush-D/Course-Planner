# UI Changes Backlog

Running list of UI/UX ideas that are worth doing but weren't done yet —
either real feature work, or lower-priority polish spotted while working on
something else. Pull items off this list into their own change when there's
time; add to it whenever something new comes up.

## Not started

- **Claim seats for everything you scheduled — BUILT, THEN PULLED BACK OUT.**
  Written, reviewed by two sessions, merged, and then deliberately reverted
  (see below) because the design question underneath it isn't settled yet.

  *The gap it was solving:* "Add to Schedule" works signed **out** — it's
  only planner state. Holding a real seat never can: `course_enrollments`
  is FK'd to `auth.users` and `claim_course_seat` rejects a null
  `auth.uid()`. So a student can line up a whole term, sign in, and still
  hold nothing, with the only route to a seat being to reopen each course
  modal and hit Apply one at a time.

  *What was built:* a prompt above the Weekly Schedule, shown only to a
  signed-in student with scheduled-but-unclaimed courses, and one button
  that applies for all of them sequentially, reporting per-course outcomes
  truthfully ("2 seats held, 1 waitlisted") rather than as blanket success.
  Plus `CourseEnrollmentService.getMyEnrollments()` — one query instead of
  N `get_my_enrollment` round-trips.

  *Why it was pulled:* the signed-in path was never verified by anyone. The
  e2e suite could only assert the prompt stays hidden for signed-out
  visitors; driving the real thing needs credentials for a student account,
  which the automated suite doesn't have. Shipping a seat-claiming action
  that no one had ever actually watched run was the wrong trade, and the
  interaction design ("what SHOULD happen when a logged-out student who
  lined up courses signs in?") deserves deciding on purpose rather than
  being settled by whatever the first implementation happened to do.

  *The code is not lost.* Branch `wip/claim-all-seats` holds it at its
  final reviewed state (`34c21444` + `740edd22`), including the review
  round with `course-planner-ff`. Restore with a cherry-pick or by
  reverting the revert; nothing needs rewriting from scratch.

  *Before picking it back up, decide:* (1) should the scheduled-course list
  survive a page load at all — today it's memory-only, so a student who
  signs in via an emailed confirmation link arrives with an empty list and
  the prompt never appears (see the anonymous-plan-persistence question);
  (2) should applying be one button for everything, or per-course
  confirmation, given that a full course silently becomes a waitlist spot;
  (3) how the demo/QA path gets a signed-in account so this can actually be
  tested before it ships next time.


- **Recommendations page score badge.** Each recommended course shows a raw
  internal ranking number (e.g. "260", "235" — see
  `components/recommendations/recommendations.component.html`). That number
  isn't explained anywhere and doesn't mean anything to a student looking
  at it cold. Consider replacing it with something the student can actually
  read — a qualitative label, a relative bar, or just dropping it and
  relying on list order.

- **Broader spacing audit.** The per-page card padding is now consistent
  (`p-6` everywhere — see the 2026-08-28 commit), but there hasn't been a
  pass on vertical rhythm between sections *within* a page (gaps between
  headings/cards/lists), or on the flowchart/chatbot's own internal
  spacing. Worth a dedicated look rather than guessing broadly.

- **Keyboard focus-state audit.** Most interactive elements have
  `focus-visible:ring-2 focus-visible:ring-indigo-300`, but this hasn't been
  checked element-by-element (custom dropdowns in
  `components/planner-setup/planner-setup.component.html`, the Mermaid
  diagram's own DOM, tour overlay controls) for a student navigating by
  keyboard only.

- **Long minor names in the dropdown list.** The minor search dropdown
  (`planner-setup.component.html`) shows the full label including the
  college in parens, e.g. "Information Sciences and Technology for
  Mathematics, Minor (Eberly College of Science)" — fine as a chip (shows
  just the code) but a little cluttered as a dropdown row for minors with
  long names. Worth checking whether truncation or a smaller/muted college
  suffix reads better.

- **Tour overlay mount/unmount fade.** Toast exit and the 3 app.component.html
  modals now fade via `motion` (see `Frontend/src/animations/modal-fade.ts`),
  but the tour overlay's own appear/disappear is still an instant cut —
  deliberately deferred, since `TourService.active.set(false)`
  (`services/tour.service.ts`) is called from inside the service itself,
  not the component, so animating it means giving the service DOM
  awareness it doesn't have today (a real refactor of its public surface,
  not a drop-in addition). Its existing step-to-step position transition
  should stay untouched either way.

## Done (for reference — see commit history for exact diffs)

- Full app-wide dark mode toggle, defaulting to light/white background for
  first-time visitors (`605a6d83`, `0adb6aa0`).
- Loading skeleton for the Home page, matching Flowchart/Recommendations
  (`605a6d83`).
- "Your plan" sidebar icon gets a gear head to distinguish it from the demo
  student icon (`11a335fd`).
- Interactive tour updated with a step for the theme toggle and an
  explanation of the gear icon (`0adb6aa0`).
- Icons on Home's "Jump to" links; Progress page bars color-coded by
  requirement type (`ffe0dfc6`).
- Toast confirmations for actions taken outside the chat panel — removing a
  completed course, adding/removing a minor (`4c362f7d`).
- Warmer "In Construction" empty states for General Education and
  Transferred Courses (`8aa0e712`).
- Slightly larger base text (root font-size 16px → 17px) and consistent
  `p-6` card padding across pages (`263405f8`).
- Toast confirmations extended to the rest of the consequential silent
  actions: transcript upload, adding a minor from Recommendations' "cheap
  minors" cards, choosing a major/extra major, a major-count reduction that
  would drop an already-picked major, and checking "I'm undecided" when it
  would clear existing majors/minors (`076a3c0c`).
- First Behrend (Erie campus) demo student — Mechanical Engineering, B.S.
  (`MEBH`) — and fixed `loginAsDemoStudent` to actually load a non-default
  campus's plans first, a gap every prior (University Park) profile never
  exposed (`6853cb82`).
- Real animation via `motion` (motion.dev) for toast exit and the 3 header
  modals' open/close — fade+scale instead of an instant cut, respecting
  `prefers-reduced-motion` (`650510d4`).
