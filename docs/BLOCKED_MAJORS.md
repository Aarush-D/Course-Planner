# Blocked Majors — Needs Human Input

Majors that were actually attempted during the "all PSU majors" build-out
(see [EXPANSION_PLAN.md](EXPANSION_PLAN.md) §1) but hit a wall only a human
can resolve — genuine ambiguity in the source data, not a bug to fix or a
missing catalog to scrape. This is **not** a backlog of majors that simply
haven't been started yet; that list is the rollout order in
EXPANSION_PLAN.md §1. A major only lands here after a real build attempt.

Once you've made the call on an entry below, remove it from this file and
either build the major (if you gave enough info to proceed) or note in
EXPANSION_PLAN.md that it's being skipped permanently (if it's not worth
building).

---

## Psychology, B.S. (College of the Liberal Arts)

- **Attempted:** 2026-08-11 (Phase B build cycle)
- **Blocker:** the bulletin's Suggested Academic Plan only shows generic
  placeholders — "Option Course," "Option Supporting Course," "200-level
  PSYCH (Groups A/B/C)" — with no real course codes anywhere on the page.
  The major has 5 named Options (Behavioral and Health Neuroscience,
  Community, Health, Life Span, and General Option — exact list unconfirmed),
  each requiring 24-27 credits of option-specific courses, and I don't know
  which one you want modeled, or have the real course lists for the
  200-level Groups A/B/C or 400-level pools that every other major's plan
  has had.
- **To unblock:** tell me which Option to build (or say "General Option" /
  whichever is most common), and if you have it, a link to a page or PDF
  with the actual per-option course lists — the bulletin's own program page
  didn't have them, unlike every other major built so far.
- **Source checked:** https://bulletins.psu.edu/undergraduate/colleges/liberal-arts/psychology-bs/

---

## Environmental Engineering, B.S. (College of Engineering)

- **Attempted:** 2026-08-11 (Phase D build cycle)
- **Blocker:** unlike every other major built so far, this bulletin page has
  no Suggested Academic Plan section at all — confirmed by fetching it
  twice, once generically and once looking specifically for that section.
  The page only has Overview, "How to Get In," and Program Requirements
  (Prescribed/Additional/Supporting Courses + Gen Ed, organized by
  category, not by semester). The individual courses themselves are named
  (CE 370/371/475/476, CE 402, CE 472W, EGEE 470, plus ~46 credits of
  supporting math/chem/physics/mechanics courses and 15 credits of named
  technical electives), so this isn't Psychology's "don't even know which
  option" problem — it's that no one at PSU has published an official
  semester-by-semester ordering for this major, so building one myself
  would mean guessing at prerequisite sequencing PSU itself hasn't
  committed to in writing.
- **To unblock:** either confirm you want me to construct a plausible
  8-semester sequence myself from the listed prereqs (I can do this — CE's
  own prereq chains are already in the catalog from the Civil Engineering
  build — but it would be my ordering, not PSU's), or point me at an
  advising-office PDF/flowchart if one exists outside the bulletin.
- **Source checked:** https://bulletins.psu.edu/undergraduate/colleges/engineering/environmental-engineering-bs/

---

## Information Sciences and Technology, B.S. (College of Information Sciences and Technology)

- **Attempted:** 2026-08-11 (IST college build cycle)
- **Blocker:** the bulletin page itself has no Suggested Academic Plan
  section — it only states the major requires 125 credits, has entrance
  courses (IST 110, IST 140, IST 210, IST 220), and offers two options
  ("Information Systems: Design & Development," World Campus only, and
  "Information Technology: Integration & Application," explicitly marked
  "currently unavailable"). The page's own suggested-academic-plan PDF
  (fetched directly) contains no course table at all — just a
  campus-closure notice and Begin/End Campus metadata. This is the same
  class of gap as Environmental Engineering (no PSU-published
  semester-by-semester ordering exists to build from), compounded by one
  of the two options being explicitly unavailable and the other being
  World-Campus-only with unpublished specifics.
- **To unblock:** either point me at an advising-office PDF/flowchart for
  the "Information Systems: Design & Development" option if one exists
  outside the bulletin, or confirm you want me to construct a plausible
  8-semester sequence myself from IST's general department course
  descriptions (entrance courses + prescribed/option/supporting categories)
  — it would be my ordering, not PSU's.
- **Source checked:** https://bulletins.psu.edu/undergraduate/colleges/information-sciences-technology/information-sciences-technology-bs/
  and its suggestedacademicplantext.pdf

---

## Elementary and Kindergarten Education, B.S. (College of Education)

- **Attempted:** 2026-08-12 (College of Education, second batch)
- **Blocker:** different in kind from every other entry here — this isn't
  a data gap I could fix or guess around, it's that the program itself
  has been closed since 2010. The bulletin page states in plain text:
  "PROGRAM CURRENTLY ON HOLD; NOT ACCEPTING NEW STUDENTS" (effective
  September 10, 2010). It lists requirement categories (prescribed/
  additional/supporting/option-specific courses, two options: Early
  Childhood PK-3 and Elementary K-6) but no semester-by-semester
  Suggested Academic Plan, consistent with a program that stopped
  admitting students 15+ years ago and was never given one. Building a
  plan for it would let a student "declare" a major Penn State hasn't
  accepted anyone into since 2010.
- **To unblock:** confirm whether you still want this built (e.g. for
  historical/completeness reasons) despite the hold — if so I can
  construct a plausible sequence from the listed requirement categories,
  same as the Environmental Engineering case. Otherwise, tell me to mark
  it permanently skipped in EXPANSION_PLAN.md rather than revisit it in
  a future rollout batch.
- **Source checked:** https://bulletins.psu.edu/undergraduate/colleges/education/elementary-kindergarten-education-bs/

---

## World Languages (K-12) Education, B.S. (College of Education)

- **Attempted:** 2026-08-12 (College of Education, second batch)
- **Blocker:** same kind of blocker as Elementary and Kindergarten
  Education in this file — not a data gap, a closed program. The
  bulletin states in plain text that the program is "currently on hold
  and NOT accepting new students" as of April 25, 2024. Unlike Elementary
  and Kindergarten Education, this one does have a full, detailed
  Suggested Academic Plan published (five language-option variants:
  French, German, Latin, Russian, Spanish), so building it would be
  technically straightforward — the blocker is purely that PSU isn't
  admitting anyone into it right now.
- **To unblock:** confirm whether you still want this built despite the
  hold (e.g. for historical/completeness reasons, or because it might
  reopen) — if so, tell me which language option to build (Spanish has
  the most complete data in what I fetched). Otherwise, tell me to mark
  it permanently skipped in EXPANSION_PLAN.md.
- **Source checked:** https://bulletins.psu.edu/undergraduate/colleges/education/world-languages-k-12-education-bs/

---

## Architecture, B.S. (College of Arts and Architecture)

- **Attempted:** 2026-08-12 (Arts and Architecture, opening batch)
- **Blocker:** two separate, compounding issues, confirmed via both the
  bulletin page and its own suggested-academic-plan PDF. First, this is
  not an independently-enrolled major: "Enrollment in the pre-professional
  (ARCBS) program is limited to those students who transfer from the
  professional (BARCH) program" — a student can't start here, only
  transfer in after already being in the 5-year B.Arch program. Second,
  its own Suggested Academic Plan PDF contains no semester-by-semester
  course table at all, just the program description and eligibility text
  quoted above — the same "PSU never published a real sequence" gap as
  Environmental Engineering and Information Sciences and Technology B.S.
  earlier this session. Built `Architecture, B.Arch.` (the actual
  5-year professional program students enroll in directly) instead, which
  does have a full plan.
- **To unblock:** if you specifically want the ARCBS pre-professional
  variant modeled too (e.g. for students who've already transferred in
  from B.Arch), point me at an advising-office PDF/flowchart, or confirm
  you want me to construct a plausible sequence from the listed
  requirement categories (81 major credits, 45 Gen Ed, 12 elective) —
  it would be my ordering, not PSU's.
- **Source checked:** https://bulletins.psu.edu/undergraduate/colleges/arts-architecture/architecture-bs/
  and its suggestedacademicplantext.pdf

---

## Art, B.A. (College of Arts and Architecture)

- **Attempted:** 2026-08-12 (Arts and Architecture, second batch)
- **Blocker:** most of the curriculum is concretely specified (ART 11,
  110, 111, 122Y, ARTH 111/112, plus an enumerated "Additional Courses"
  menu of real codes — ART 211/220/223/230/240/250/260/280, DART
  202-206, PHOTO 100/201/202), but the 21-credit "Supporting Coursework"
  block requires picking one of five Areas of Concentration (ceramics,
  drawing and painting, new media/digital arts, photography, sculpture)
  worth 15 of those 21 credits, and the bulletin's own program
  requirements page states explicitly that it does **not** list course
  codes for any concentration — it directs students to LionPATH or an
  academic adviser instead. This is the same shape of gap as Psychology:
  a named set of options with zero course-code data behind any of them,
  not a bug I can fix by re-reading the page differently.
- **To unblock:** tell me which concentration to build (ceramics,
  drawing and painting, new media/digital arts, photography, or
  sculpture), and if you have it, a link to a page or PDF with the real
  per-concentration course list — the bulletin's own program page
  doesn't have them.
- **Source checked:** https://bulletins.psu.edu/undergraduate/colleges/arts-architecture/art-ba/
  (Suggested Academic Plan and Program Requirements sections)

---

## Art, B.F.A. (College of Arts and Architecture)

- **Attempted:** 2026-08-12 (Arts and Architecture, second batch)
- **Blocker:** same shape of gap as Art, B.A. above, and worse in
  degree: 24 of the program's 47 required 300/400-level studio credits
  must come from one of the same five Areas of Concentration (ceramics,
  drawing and painting, new media/digital arts, photography, sculpture),
  and the bulletin names zero course codes for any of them — only the
  15-credit "Beginning-Level Studio Options" pool (ART 201/203/211Y/217/
  220/223/230/240/250/260/280/296/297/299, DART 202-206/213/297, PHOTO
  100/201/202) is enumerated with real codes. The program also runs
  continuous portfolio review (non-qualifying students are moved back to
  the B.A.), which the planner schema doesn't model, but that's a minor
  secondary issue next to the missing concentration data.
- **To unblock:** same as Art, B.A. — tell me which concentration to
  build, and point me at a source with the real per-concentration
  300/400-level course list if you have one.
- **Source checked:** https://bulletins.psu.edu/undergraduate/colleges/arts-architecture/art-bfa/

---

<!--
Template for future entries — copy this block per blocked major:

## {Major Name}, {Degree} ({College})

- **Attempted:** {date} ({build cycle / phase})
- **Blocker:** {what's genuinely ambiguous or missing — not a bug, a real
  data gap or decision only a human can make}
- **To unblock:** {exactly what you need from the user to proceed}
- **Source checked:** {bulletin URL(s) actually fetched}
-->
