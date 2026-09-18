# Majors Needing Review — data-quality findings from the catalog-year/campus backfill

Findings surfaced while backfilling 2022-2025 catalog years and checking
Erie/Brandywine campus data for already-built majors (2026-09-17 onward).
These are **not** build failures — every major listed here already has a
working, tested plan file that reaches graduation with 0 warnings. What's
listed is a specific, cited discrepancy between the built file and a fresh
re-check of the real PSU bulletin.

A 2026-09-18 review pass went through every entry that was here: KINES,
HIST, CAMS, JOURN, DS, FRENCHBA, WFED, LHR, TURF, ARTH, PLSC, RHS, ANTH,
CMLIT, RPTM, and the IE/IEC/CED math-chain pattern were all re-verified
against a fresh bulletin fetch and fixed in place (see each file's own
"notes" field for the citation trail). STAT's finding turned out to be
resolved by design (the bulletin's own pool is intentionally open-ended,
and the file's specific pick is a real, valid, non-excluded member of it
-- no change needed). SPANBA's finding turned out to be a real, engine-
wide bug (not SPANBA-specific) and was spun out as its own dedicated
follow-up rather than tracked here.

Once you've reviewed and fixed (or decided to leave) an entry, remove it
from this file.

---

## Tier 1 — affects the current, live 2026 plan (real students use this one)

### CMPEN — Computer Engineering, B.S. (College of Engineering)
- **Years affected:** 2026 (current/live)
- **Cause:** PSU's own live bulletin page still has two internally
  contradicting tabs as of 2026-09-18 (re-verified, not just carried
  forward from the original finding). The "Program Requirements" tab is
  an *additive* rewrite of the intro sequence (CMPSC 150N + CMPSC 222
  layered on top of the still-present CMPSC 121/131/122/132, CMPSC
  315/316 in place of 311/473, ECON 102/104 removed; MATH 220/250 are
  actually still present on this tab, correcting the original finding's
  claim that they were dropped) totaling 106-108cr for the major alone.
  The "Suggested Academic Plan" tab (what `CMPEN-2026.json` matches)
  still prints the old pre-overhaul sequence, totaling 128-129cr.
- **To resolve:** wait for PSU to reconcile its own two tabs, or decide
  which tab to trust and rebuild the semester sequence from it. Don't
  guess a sequence PSU itself hasn't published an ordering for.
- **Found during:** wave 3, 2026-09-17; re-verified 2026-09-18 (still open)

### FORES — Forest Ecosystems, B.S. (College of Agricultural Sciences)
- **Years affected:** 2026 (current/live)
- **Cause:** Same class of issue as CMPEN, still unresolved as of
  2026-09-18. The live "Program Requirements" tab lists a rewritten
  Biodiversity and Conservation option (94-99cr) with several new FOR-
  department codes (FOR 201N, FOR/WFS 431, FOR 445, WFS/FOR 430, WFS/FOR
  465) that don't exist anywhere in this app's catalog data and have no
  published semester ordering. The "Suggested Academic Plan" tab (what
  `FORES-2026.json` matches) still prints the pre-overhaul Forest-Biology
  sequence (119-126cr).
- **To resolve:** same as CMPEN — needs a human decision once PSU's tabs
  agree, or a decision to sequence the new course list without a
  PSU-published ordering.
- **Found during:** wave 4, 2026-09-17; re-verified 2026-09-18 (still open)

### GEOSCI — Geosciences, B.S. (College of Earth and Mineral Sciences)
- **Years affected:** 2026 (current/live), inherited by 2022-2025
- **Cause:** PSU's own live bulletin page disagrees with itself: the
  "Program Requirements" prose section says "BIOL 110" by name, while the
  "Suggested Academic Plan" table on the same page shows the BIOL 114
  (3cr) + BIOL 115 (1cr) split that `GEOSCI-2026.json` actually uses. Not
  a scraping artifact — both sections were checked on the same fetched
  page, re-confirmed 2026-09-18.
- **To resolve:** decide which of PSU's own two answers to trust (the file
  currently follows the table, the more granular/authoritative source for
  scheduling); flag to PSU if it seems like their error.
- **Found during:** wave 4, 2026-09-17; re-verified 2026-09-18 (still open)

---

## Tier 2 — lower priority, disclosed modeling quirks (not urgent, don't block correctness)

| Major | Years | Cause |
|---|---|---|
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
