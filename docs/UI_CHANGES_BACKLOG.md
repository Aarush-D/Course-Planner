# UI Changes Backlog

Running list of UI/UX ideas that are worth doing but weren't done yet —
either real feature work, or lower-priority polish spotted while working on
something else. Pull items off this list into their own change when there's
time; add to it whenever something new comes up.

## Not started

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
