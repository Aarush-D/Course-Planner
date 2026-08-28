# UI Changes Backlog

Running list of UI/UX ideas that are worth doing but weren't done yet —
either real feature work, or lower-priority polish spotted while working on
something else. Pull items off this list into their own change when there's
time; add to it whenever something new comes up.

## Not started

- **Compact the "Path to Graduation" flowchart.** On a plan with several
  semesters left, each term's subgraph in the semester-by-semester Mermaid
  diagram (`components/flowchart/flowchart.component.ts`,
  `build_semester_flowchart` in `Backend/planner_engine.py`) can run 800px+
  tall, so seeing the whole path takes a lot of scrolling. A "collapse
  completed semesters" toggle (or collapse-by-default with an expand
  control) would help. This is real feature work, not a quick visual
  tweak — needs a decision on where the toggle state lives and how it
  interacts with the existing zoom controls.

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
