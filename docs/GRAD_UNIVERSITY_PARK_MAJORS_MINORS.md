# Graduate majors & minors offered at University Park

Real data pulled directly from `bulletins.psu.edu/graduate/` (the official
PSU Graduate Bulletin), for the eventual grad-student expansion scoped in
[`GRAD_AND_WORLD_CAMPUS_FINDINGS.md`](GRAD_AND_WORLD_CAMPUS_FINDINGS.md).
This file is **data only** — nothing here is wired into the app yet. It
exists so the actual "which grad programs does University Park offer"
research doesn't have to be redone when that build-out happens.

## Methodology (read this before trusting any single row)

PSU's graduate program directory tags a program with a campus suffix —
`(Behrend)`, `(Capital)`, `(Great Valley)` — whenever it's **also** offered
at a commonwealth campus alongside University Park. That convention is
reliable: every satellite-campus variant checked was correctly suffixed,
so removing those from the raw list was mechanical and low-risk.

It is **not** reliable for Hershey. The bulletin's own campuses page states
Hershey (Penn State College of Medicine) focuses on "Biomedical Sciences,
Neuroscience, Anatomy, Public Health Sciences and related fields" — but
several Hershey-*only* programs in that family (Anatomy, Biomedical
Sciences, Biostatistics, Clinical Research, Epidemiology, Laboratory Animal
Medicine, Public Health) appear in the raw directory with **no campus
suffix at all**, indistinguishable at a glance from a real University Park
program. This was caught empirically, not assumed: fetching each
program's own bulletin page and reading its `Campus(es):` field showed
Hershey, not University Park, for every one of them. A generic
"no suffix = University Park" rule — the same rule this repo already uses
for the undergraduate catalog — would have silently mislabeled all seven
as University Park majors.

**What that means for this file:**
- Every program listed below in the "College of Medicine-adjacent" risk
  zone (biomedical/clinical/health-science subject matter) was
  individually fetched and its own `Campus(es):` field read directly —
  11 programs, listed under "Individually verified" below.
- Everything else (engineering, business, arts, humanities, sciences,
  education, agriculture, social sciences) relies on the directory's own
  campus-suffix tagging, which — unlike Hershey — checked out accurate
  everywhere it was spot-checked. This is the same confidence level the
  existing undergraduate catalog in this repo was built on.
- This has **not** been verified program-by-program the way the 150+
  undergraduate majors were. Before building a real `Backend/grad_plans/`
  file for any specific program, verify that program's own bulletin page
  directly — same discipline as everywhere else in this repo.

### Individually verified (fetched each program's own bulletin page)

| Program | Campus(es) per its own bulletin page | In list below? |
|---|---|---|
| Anatomy | Hershey (Ph.D., M.S.) | No — excluded |
| Biomedical Sciences | Hershey (Ph.D., M.S.) | No — excluded |
| Biostatistics | Hershey (Ph.D.) | No — excluded |
| Clinical Research | Hershey (M.S.) | No — excluded |
| Epidemiology | Hershey (Ph.D.) | No — excluded |
| Laboratory Animal Medicine | Hershey (M.S.) — DVM/VMD prerequisite | No — excluded |
| Public Health | Hershey (M.P.H., Dr.P.H.) + World Campus (M.P.H.) | No — excluded |
| Bioethics | University Park | Yes (dual-title only, see below) |
| Pathobiology | University Park (Ph.D., M.S.) | Yes |
| Integrative and Biomedical Physiology | University Park (Ph.D., M.S.) | Yes |
| Microbiome Sciences | University Park | Yes (dual-title only, see below) |
| Clinical and Translational Sciences | **Both** Hershey and University Park — jointly administered by the College of Health and Human Development (UP) and College of Medicine (Hershey) | Listed separately — dual-title only, not a standalone major |

**Dual-title-only programs** (Bioethics, Microbiome Sciences, Clinical and
Translational Sciences) confer no degree on their own — a student earns a
Ph.D. "in \[primary major\] and \[dual-title\]" through their actual home
program. They're listed here because they're real University Park
academic programs a grad student engages with, but they don't work like
a normal standalone major/minor pick.

**Not independently re-verified, but excluded for the same
subject-matter/program-code reason as the seven confirmed above:**
Clinical and Translational Sciences' Hershey half aside, no other
PHS-coded (Public Health Sciences department) program was found in the
raw directory — the seven above appear to be the complete Hershey-only
set among what the initial listing surfaced. If a future pass finds
another unsuffixed biomedical/clinical program, verify it individually
before trusting either way.

---

## Graduate majors at University Park

Grouped by the college/department tag the bulletin gives multi-campus
programs; ungrouped entries had no tag in the source directory (single
University Park offering).

### Smeal College of Business
Accounting, Business Administration (M.B.A., D.B.A.), Business
Administration (Ph.D., M.S.), Business Administration (Intercollege),
Finance

### College of Engineering
Civil Engineering, Electrical Engineering, Mechanical Engineering

### College of Health and Human Development
Kinesiology

### Eberly College of Science
Neuroscience

### College of the Liberal Arts
Bioethics *(dual-title only — see above)*

### No campus/college suffix in the source directory (single UP offering)
Accounting Analytics, Acoustics, Additive Manufacturing and Design,
Aerospace Engineering, African American and Diaspora Studies, African
Studies, Agricultural and Biological Engineering, Agricultural and
Environmental Plant Science, American Studies, Ancient Mediterranean
Languages, Ancient Mediterranean Studies, Animal Science, Anthropology,
Applied Artificial Intelligence for Business Transformation, Applied
Behavior Analysis, Applied Linguistics, Architectural Engineering,
Architecture, Art, Art Education, Art History, Artificial Intelligence,
Asian Studies, Astrobiology, Astronomy and Astrophysics, Athletic
Training, Biobehavioral Health, Biochemistry Microbiology and Molecular
Biology, Biogeochemistry, Bioinformatics and Genomics, Biology,
Biomedical Engineering, BioRenewable Systems, Biotechnology, Business
Analytics, Chemical Engineering, Chemistry, Classics and Ancient
Mediterranean Studies, Climate Science, Communication Arts and
Sciences, Communication Sciences and Disorders, Communications,
Community and Economic Development, Comparative and International
Education, Comparative Literature, Computer Science and Engineering,
Corporate Innovation and Entrepreneurship, Counselor Education,
Criminal Justice, Criminal Justice Policy and Administration,
Criminology, Curriculum and Instruction, Cybersecurity Analytics and
Operations, Data Analytics, Demography, Ecology, Economics, Ecosystem
Management and Administration, Education, Education Policy and
Leadership, Education Development and Community Engagement,
Educational Leadership, Educational Psychology, Educational Theory and
Policy, Energy and Mineral Engineering, Energy Environmental and Food
Economics, Engineering, Engineering and Computing Systems, Engineering
at the Nano-scale, Engineering Design and Innovation, Engineering
Science and Mechanics, Engineering, Law, and Policy, English, Enterprise
Architecture and Business Transformation, Entomology, Environmental
Engineering, Food Science, Forensic Science, Forest Resources, French
and Francophone Studies, Geodesign, Geographic Information Systems,
Geography, Geosciences, German, Global Economic and Business
Relations, Health Administration, Health Policy and Administration,
High Performance Sport, Higher Education, History, Homeland Security,
Hospitality Management, Human Development and Family Studies, Human
Resources and Employment Relations, Industrial Engineering, Informatics,
Information Science, Information Systems, Integrative and Biomedical
Physiology, International Affairs, International Agriculture and
Development, Labor and Global Workers' Rights, Landscape Architecture,
Language Science, Leadership Development, Learning, Design, and
Technology, Lifelong Learning and Adult Education, Linguistics,
Marketing Analytics and Insights, Mass Communications, Materials
Science and Engineering, Mathematics, Media Studies, Meteorology and
Atmospheric Science, Microbiome Sciences *(dual-title only)*, Molecular,
Cellular and Integrative Biosciences, Music, Music Education, Nuclear
Engineering, Nursing, Nutritional Sciences, Operations Research,
Organization Development and Change, Pathobiology, Philosophy, Physics,
Piano Performance, Plant Biology, Plant Pathology, Political Science,
Project Management, Psychology, Psychology of Leadership, Public
Administration, Public Policy, Quality and Manufacturing Management,
Real Estate Analysis and Development, Recreation, Park, and Tourism
Management, Renewable Energy and Sustainability Systems, Rural
Sociology, Russian and Comparative Literature, School Psychology,
Social and Behavioral Neuroscience, Social Data Analytics, Social Work,
Sociology, Software Engineering, Soil Science, Spanish, Spatial Data
Science, Special Education, Speech-Language Pathology, Statistics,
Strategic Communications, Strategic Management and Executive
Leadership, Supply Chain Management, Systems Engineering, Taxation,
Teaching and Curriculum, Teaching English as a Second Language,
Theatre, Transdisciplinary Research on Environment and Society,
Turfgrass Management, Visual Studies, Wildlife and Fisheries Science,
Women's, Gender, and Sexuality Studies, Workforce Education and
Development

*(~175 majors total. Source: `bulletins.psu.edu/graduate/programs/` full
directory, satellite-campus-suffixed duplicates and confirmed-Hershey
programs removed per the methodology above.)*

## Graduate minors at University Park

All 13 university-wide graduate minors, per the same source directory.
None carried a campus suffix and none overlap the biomedical/clinical
subject matter that made individual verification necessary above, so
these are treated as University Park with the same confidence as the
"no suffix" majors list:

- Computational Materials Graduate Minor
- Computational Science Graduate Minor
- Electrochemical Science and Engineering Graduate Minor
- Engineering Leadership and Innovation Management Graduate Minor
- Gerontology Graduate Minor
- Holocaust and Genocide Studies Graduate Minor
- Information and Communication Technologies for Development Graduate Minor
- Jewish Studies Graduate Minor
- Latin American Studies Graduate Minor
- Latina and Latino Studies Graduate Minor
- Literary Theory, Criticism, and Aesthetics Graduate Minor
- Science, Technology, and Society Graduate Minor
- Social Thought Graduate Minor

## What this doesn't cover

- **Graduate certificates** — 180+ of them exist; out of scope for this
  pass since the user asked specifically for majors and minors.
- **Which specific degree(s)** each major confers (M.S./Ph.D./M.Eng./etc.),
  course requirements, or admission requirements — this file is a name
  list, not program data. `GRAD_AND_WORLD_CAMPUS_FINDINGS.md` Part 1
  already did that deep-dive for one real example (CSE's M.Eng.) and
  explains why every program needs its own such pass — there's no single
  repeatable shape the way undergrad's 8-semester flowchart was.
- **Degree-level data model, loader, frontend selector** — none of that
  exists yet. See `GRAD_AND_WORLD_CAMPUS_FINDINGS.md`'s "Recommended
  first phase" section for the concrete next step.
