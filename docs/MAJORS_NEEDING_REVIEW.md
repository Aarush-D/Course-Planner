# Majors Needing Review — data-quality findings from the catalog-year/campus backfill

Findings surfaced while backfilling 2022-2025 catalog years and checking
Erie/Brandywine campus data for already-built majors (2026-09-17 onward).
These are **not** build failures — every major listed here already has a
working, tested plan file that reaches graduation with 0 warnings. What's
listed is a specific, cited discrepancy between the built file and a fresh
re-check of the real PSU bulletin, found by the agent building that major's
*historical* years while it had the bulletin open anyway. None were fixed
here because fixing them was outside that task's scope (historical years +
campus field only) — they need a deliberate, scoped fix pass.

Once you've reviewed and fixed (or decided to leave) an entry, remove it
from this file.

---

## Tier 1 — affects the current, live 2026 plan (real students use this one)

### CMPEN — Computer Engineering, B.S. (College of Engineering)
- **Years affected:** 2026 (current/live)
- **Cause:** PSU's own live bulletin page currently has two internally
  contradicting tabs. The "Program Requirements" tab has been substantially
  overhauled (CMPSC 150N added, CMPSC 122/132 replaced by CMPSC 222,
  CMPSC 311/473 replaced by CMPSC 315/316, ECON 102/104 removed, MATH 220/250
  dropped, total credits down from 128 to 106-108/127) — but the "Suggested
  Academic Plan" tab on the *same page* still shows the old, pre-overhaul
  sequence verbatim. `CMPEN-2026.json` matches the old (Suggested Plan)
  version.
- **To resolve:** wait for PSU to reconcile its own two tabs, or decide
  which tab to trust and rebuild the semester sequence from it.
- **Found during:** wave 3, 2026-09-17 (CMPEN historical backfill agent)
- **Source:** CMPEN-2026.json's own `source` URL, both tabs checked via raw HTML

### FORES — Forest Ecosystems, B.S. (College of Agricultural Sciences)
- **Years affected:** 2026 (current/live)
- **Cause:** Same class of issue as CMPEN. The live "Program Requirements"
  tab has been rewritten for 2026-27 (new Common Requirements including
  FOR 123N and a redefined FOR 204; the "Biodiversity and Conservation"
  option now lists FOR 201N, FOR/WFS 431, FOR 445, WFS/FOR 430, WFS/FOR 465,
  CHEM 110-or-130 instead of CHEM 202; 94-99 total major credits). The
  "Suggested Academic Plan" tab was not updated and still prints the
  pre-overhaul Forest Biology-era sequence (119-126 total credits).
  `FORES-2026.json` matches the stale Suggested Academic Plan tab.
- **To resolve:** same as CMPEN — needs a human decision once PSU's tabs
  agree, or a decision to sequence the new course list without a
  PSU-published ordering.
- **Found during:** wave 4, 2026-09-17 (FORES historical backfill agent)
- **Source:** FORES-2026.json's own `source` URL, both tabs + PDFs checked via raw HTML

### AFAM — African American Studies, B.A. (College of the Liberal Arts)
- **Years affected:** 2026 (current/live)
- **Cause:** AFAM 110N and AFAM 152/HIST 152 are swapped between Semester 1
  and Semester 2 relative to the real bulletin's Fall/Spring table order;
  the GEN ED (GQ) tag is also on the wrong semester (Fall in the file vs.
  Spring on the real page). Doesn't break graduation feasibility (no
  prereq depends on the swap), but the semester labels are wrong.
- **To resolve:** already being fixed in a separately-spawned session
  (`task_abe3950f`) as of 2026-09-17 — check whether that landed before
  doing anything further here.
- **Found during:** wave 2, 2026-09-17 (AFAM discovered during a different major's cross-check)
- **Source:** bulletins.psu.edu/undergraduate/colleges/liberal-arts/african-american-studies-ba/, raw HTML

### KINES — Kinesiology, B.S. (College of Health and Human Development)
- **Years affected:** 2026 (current/live) — and by inheritance, likely 2022-2025 too, since those were built to mirror 2026
- **Cause:** Missing KINES 321, a real common requirement confirmed present
  in the current live bulletin's own Suggested Academic Plan table and in
  every archived edition back to 2022.
- **To resolve:** add KINES 321 to the appropriate semester; re-check
  whether adding it changes the credit total/term count.
- **Found during:** wave 2, 2026-09-17

### HIST — History, B.A. (College of the Liberal Arts)
- **Years affected:** 2026 (current/live), inherited by 2022-2025
- **Cause:** LA 283 is placed in Second Year **Fall** in the file, but both
  the live 2026-27 bulletin and the 2025-2026 archive show it in Second
  Year **Spring** (paired with two 100/200-level HIST courses). The file's
  own notes claim LA 283 "could not be confirmed to exist" — it does, just
  in a different semester than assumed.
- **To resolve:** move LA 283 to the correct semester; verify nothing else
  in that semester pairing depends on the current (wrong) placement.
- **Found during:** wave 3, 2026-09-17

### CAMS — Classical and Mediterranean Studies, B.A. (College of the Liberal Arts)
- **Years affected:** 2026 (current/live), inherited by 2022-2025
- **Cause:** Semester 1 and Semester 2 have two items swapped relative to
  the real bulletin: the real table puts "World Language Level 1" (4cr) in
  Fall 1 and the CAMS 00/100-level course in Spring 1; the file has it
  reversed.
- **To resolve:** swap the two items back to match the real semester order.
- **Found during:** wave 2, 2026-09-17

### JOURN — Journalism, B.A. (Donald P. Bellisario College of Communications)
- **Years affected:** 2026 (current/live), inherited by 2022-2025
- **Cause:** "CAS 100A (or 100B/100C)" and the "BA Knowledge Domain — IL
  Cultures" item are swapped between Semester 5 and Semester 6 relative to
  every real bulletin checked (2022 through the live 2026-27 page).
- **To resolve:** swap the two items back to the correct semesters.
- **Found during:** wave 3, 2026-09-17

### DS — Data Sciences, B.S. (Eberly College of Science)
- **Years affected:** 2026 (current/live)
- **Cause:** The file's own notes claim (1) the real total is "129cr, up
  from 126" and (2) that a "CMPSC 465 (or DS 305)" item was correctly
  removed after confirming it doesn't appear in the live bulletin. A fresh
  fetch of the live DS bulletin page (2026-09-17) shows neither holds
  today: the live page's own Suggested Academic Plan prints "Total Credits
  122" (matching the 2024/2025 archives exactly), and the removed item is
  still present on the live page.
- **To resolve:** re-verify against the current live page and correct the
  credit total / restore or re-justify the removed item.
- **Found during:** wave 3, 2026-09-17
- **Source:** bulletins.psu.edu/undergraduate/colleges/eberly-science/data-sciences-bs/

### FRENCHBA — French, B.A. (College of the Liberal Arts)
- **Years affected:** 2026 (current/live), inherited by 2022-2025
- **Cause:** Semester 4 (Second Year Spring) has only one 3cr GEN ED slot
  (13.5cr total), but a raw-HTML row-by-row parse of the live bulletin page
  (cross-checked against its own printed subtotal, which sums to exactly
  123) shows Second Year Spring should be SIX items totaling 16.5cr: FR
  201, FR 202, LA 283, two separate GEN ED items, and a B.A. Requirement.
- **To resolve:** add the missing items to Semester 4; re-verify credit
  totals across the rest of the plan.
- **Found during:** wave 3, 2026-09-17

### WFED — Workforce Education and Development, B.S. (College of Education)
- **Years affected:** 2026 (current/live), inherited by 2022-2025
- **Cause:** Semester 7 "Additional Courses" item models only `["WFED
  450"]`, but the live 2026-27 bulletin's Program Requirements table lists
  three real valid options for that same 3-credit requirement: LDT 100
  (World Technologies and Learning, still current), a real STS 245-family
  course, and WFED 450.
- **To resolve:** widen the item to include all three real options.
- **Found during:** wave 4, 2026-09-17

### LHR — Labor and Human Resources, B.A. (College of the Liberal Arts)
- **Years affected:** 2026 (current/live)
- **Cause:** The file's own notes claim it was "re-verified" against the
  live bulletin, but a fresh raw-HTML row-by-row parse (2026-09-17) of the
  actual current live page no longer matches what `LHR-2026.json` encodes.
  (Full diff wasn't captured in the agent's summary — needs a fresh
  side-by-side comparison.)
- **To resolve:** re-fetch the live bulletin page and diff line-by-line
  against the current file.
- **Found during:** wave 4, 2026-09-17

### GEOSCI — Geosciences, B.S. (College of Earth and Mineral Sciences)
- **Years affected:** 2026 (current/live), inherited by 2022-2025
- **Cause:** PSU's own live bulletin page disagrees with itself: the
  "Program Requirements" prose section says "BIOL 110" by name, while the
  "Suggested Academic Plan" table on the same page shows the BIOL 114
  (3cr) + BIOL 115 (1cr) split that `GEOSCI-2026.json` actually uses. Not a
  scraping artifact — both sections were checked on the same fetched page.
- **To resolve:** decide which of PSU's own two answers to trust (the file
  currently follows the table); flag to PSU if it seems like their error.
- **Found during:** wave 4, 2026-09-17

### TURF — Turfgrass Science, B.S. (College of Agricultural Sciences)
- **Years affected:** 2026 (current/live)
- **Cause:** The file's own notes claim it "confirmed via bulletin
  cross-check" that a real 3cr Elective belongs in Semester 5 (moved there
  from Semester 6). A fresh raw-HTML table parse of the current live page
  AND all four archived editions contradicts this claim — needs a
  side-by-side re-check to determine which placement is actually correct.
- **To resolve:** re-verify the Elective's real semester placement against
  a fresh fetch; the agent's own re-check disagreed with the file's
  existing justification.
- **Found during:** wave 4, 2026-09-17

---

## Tier 2 — lower priority, disclosed modeling quirks (not urgent, don't block correctness)

| Major | Years | Cause |
|---|---|---|
| **ARTH** (Art History, B.A.) | 2026 | File's own notes say "two advising-note sub-rules aren't modeled," but the live bulletin actually has three (Architectural History inclusion, 400-level Supporting Courses, Prehistoric-1600 Supporting Courses). |
| **PLSC** (Political Science) | 2026 (all years) | PSU's own bulletin lists STAT 184 as 2cr in every edition including live, but STAT 184's own course-description page says 3cr since 2024-25. File already trusts the 3cr value — a judgment call, not an error, but worth confirming. |
| **RHS** (Rehabilitation and Human Services) | 2026 | File's notes claim "no real, reliably-citable department-approved course list could be found" for a Supporting Courses pool, but one is directly present and citable on the live page (CNED 401/416/421/422, RHS 226/410/428/433, SPLED 400/461). |
| **ANTH** (Anthropology) | 2026, inherited by 2025 | Semesters sum to 123 target-credits; live bulletin's own printed total is 124 (1cr gap). |
| **CMLIT** (Comparative Literature) | 2026, inherited by 2022-2025 | Semester-target credits sum to 123; source bulletin states Total Credits 126 (~3cr gap, already disclosed in the file's own notes). |
| **RPTM** (Recreation, Park, and Tourism Management) | 2026 | GQ Gen-Ed requirement placed in Fall (Semester 1) where the live bulletin's table puts it in Spring (Semester 2); target_credits strings don't always sum to their listed items. |
| **SPANBA** (Spanish) | 2026 (engine-wide) | Engine schedules courses using the shared catalog's credit value, not the plan item's stated credits (e.g. LA 83 stated 1.5cr but catalog says 1.0cr) — a pre-existing engine/data quirk, not specific to SPANBA. |
| **IE / IEC / CED** | 2026 (all three) | Each file's notes claim a MATH 3→4→21→22 remedial chain is needed because MATH 21 "requires" earlier courses, but `Backend/catalogs/math_catalog.json` shows MATH 21's own prereq_groups as empty. Possibly a recurring pattern worth a broader check across other majors using the same chain. |
| **STAT** (Statistics) | 2026 (all years) | Semester 7 hardcodes "CMPSC 432" for a "Select 9 credits from CMPSC 221 or 400-level CMPSC courses" pool; not explicitly named as required in any archived bulletin's Program Requirements text. Pre-existing modeling choice, not introduced by the backfill. |
| **GD** (Graphic Design) | 2022, 2023, 2024 | Shared, undated catalog (`gd_catalog.json`) reflects today's live prerequisites, not that era's real ones — see architectural note below. Already excluded from the test suite with a citation, not silently passing. |
| **SUR** (Surveying Engineering) | 2022, 2023 | Same undated-catalog issue as GD, for SUR 241/222/362/etc. Already excluded from the test suite with a citation. |

### Cross-cutting architecture note (affects GD, SUR, and possibly others not yet found)
The shared `Backend/catalogs/*.json` prerequisite data is a single, undated
snapshot of *today's* live course descriptions. When PSU repoints a
still-catalogued legacy course code's prerequisite at a newer course that
didn't exist in an older catalog year (e.g. GD 200's real prereq today is
GD 107, which didn't exist before 2025), any historical plan using that
legacy code can never satisfy the prereq check, even though the
*historical* plan itself is correct. This is a real engine gap — no
catalog-year-scoped prereq data — not a per-major bug. Worth a dedicated
fix if it keeps recurring as more majors get backfilled.
