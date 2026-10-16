# PSU Course Planner

An AI College Course Planner for Penn State students. Students chat naturally —
*"I am a CMPSC major and I completed CMPSC 131 and calc 1. What should I take
next?"* — and the planner detects their major, matches their courses against
the real PSU bulletin, recommends a prerequisite-safe next semester from the
official advising flowchart, ranks every eligible course with a weighted score,
and lays out the full path to graduation — including the exact catalog year
that applied when the student actually started college.

## Status

| Area | Status |
|---|---|
| Core planning engine — Computer Science (CMPSC) | ✅ Shipped |
| Core planning engine — Premedicine (PREMED) | ✅ Shipped |
| Historical catalog years, 2022–2026 | ✅ Shipped |
| Chat-based start-year detection | ✅ Shipped |
| Semester-by-semester flowchart view | ✅ Shipped |
| Transfer Credit Tool (PA community colleges) | 🚧 In progress — distance ranking live; equivalency data being collected |
| General Education course fulfillment | ✅ Shipped — real recommendations across all 10 domains, per-requirement progress, course browsing/search, and multi-domain overrides |
| Remaining PSU majors | 🚧 In progress — 161 of ~194 majors built so far (see docs/EXPANSION_PLAN.md) |

Full technical roadmap, open design questions, and rollout plan:
[docs/EXPANSION_PLAN.md](docs/EXPANSION_PLAN.md).

## Architecture

| Layer | Technology |
|---|---|
| Frontend | Angular standalone components, signals, Tailwind, Mermaid |
| Backend | Flask + flask-cors, deterministic planning engine |
| Data | Scraped PSU bulletin catalogs, degree-plan JSON per major/catalog-year |
| AI | Local Ollama (llama3 for phrasing, nomic-embed-text for RAG) |

**Key principle: the LLM never decides.** Eligibility, recommendations,
scores, semester plans, and every Mermaid diagram are all computed
deterministically in Python ([Backend/planner_engine.py](Backend/planner_engine.py)).
Ollama only rephrases the verified facts conversationally (with RAG advising
notes as background) — and everything works with Ollama off (`USE_OLLAMA=0`).

- **Degree plans** ([Backend/degree_plans/](Backend/degree_plans/)) — one JSON
  per `{major, catalog year}` (e.g. `CMPSC-2026.json`, `PREMED-2023.json`)
  built from the official advising flowchart or the bulletin's own Suggested
  Academic Plan tab: semesters, courses, option groups, GEN ED/elective slots,
  credits, Entrance-to-Major flags. **Add a major or year = add a file** —
  `engine.load_degree_plan(major, catalog_year)` picks the exact year
  requested, falling back to the latest if that year isn't built yet.
- **Catalogs** ([Backend/catalogs/](Backend/catalogs/)) — auto-scraped from
  bulletins.psu.edu per department with enforced prereq/concurrent rules,
  including the historical bulletin archive (`bulletins.psu.edu/archive/...`)
  for past catalog years.
- **Weighted ranking** — every eligible course gets a score:
  +50 base, +100 official flowchart, +40 next flowchart semester, +30 core,
  +5/unlocked course (cap +40), +20 interest match, special-topics/internship
  courses excluded unless asked for.
- **Chat parsing** — major aliases (Computer Science→CMPSC, premed→PREMED…),
  course codes in any format (`CMPSC131`, `CMPSC-131`), spoken names ("calc
  1"→MATH 140), completion wording ("I took…"), removal wording ("I
  dropped…"), and a stated start year ("oh, I started school in 2022") that
  overrides the catalog year even if the "Started college" dropdown was never
  touched. The backend returns `state` in every response as the source of
  truth, and the frontend syncs every dropdown from it — nothing is
  client-side-only.

## Year planning

- **Started college / Graduate in / Allow Summer Courses** controls drive
  which catalog year's requirements apply and how the full graduation plan is
  simulated (`Backend/planner_engine.py:build_full_plan`) — including real
  term labels (`Fall 2026`, `Spring 2027`, …), a lower summer credit cap, and
  a warning (with a suggestion to enable summers) if the goal doesn't fit.
- A course the student says isn't offered in summer ("CMPSC 360 isn't
  available in summer") gets excluded from summer terms and rescheduled,
  substituting an alternate option where the degree plan has one.
- Catalog year is **never** a sticky client value — it's derived fresh from
  `start_year` on every request, so a later correction always wins instead of
  being silently overridden by a stale remembered year.

## Visualizations

Three deterministic (no-LLM) Mermaid views of a student's plan, all built the
same way — real prerequisite data in, styled `classDef`/`class` out:

- **Progress flowchart** — completed courses → recommended next semester,
  with arrows.
- **Course Unlock Map** — a flat 3-tier snapshot: completed (green) →
  unlocked next (blue) → future unlocks (grey), Entrance-to-Major courses
  highlighted red.
- **Semester flowchart** (toggle next to the card-based "Path to Graduation"
  view) — the *entire* remaining path, one subgraph per real simulated term:
  completed (green) → the very next term (red) → every term after that
  (grey), with prerequisite arrows colored to match their source node.

## Transfer Credit (in progress)

Foundation for recommending nearby PA community colleges where a student
could take a course that PSU accepts for transfer, ranked by distance and (as
data becomes available) by how many of the student's remaining courses
actually transfer there:

- `Backend/data/pa_zip_coords.json` — 1,798 real PA zip codes with lat/lng
  (Census Gazetteer-derived), so distance ranking needs no external
  geocoding API.
- `Backend/data/pa_community_colleges.json` — all 16 PA community colleges
  with their real LionPATH institution IDs.
- `Backend/transfer_credit.py` — Haversine distance ranking, an
  `EquivalencyRecord` cache schema confirmed against a real PSU Transfer
  Credit Tool export, and expiry-prioritized refresh scheduling
  (`soonest_expiring()` — re-check whichever cached course-acceptance is
  closest to expiring, not on a flat calendar).
- `POST /api/transfer-credit` — live today for distance ranking; course
  coverage will fill in as more equivalency data is collected (LionPATH's
  tool has no public API, so this is manually seeded from real exports).

See [docs/EXPANSION_PLAN.md §5](docs/EXPANSION_PLAN.md) for the full design
and current data-collection status.

## Run locally

**Terminal 1 — Ollama** (optional but recommended):

```bash
ollama serve
ollama pull llama3:latest
ollama pull nomic-embed-text:latest
```

**Terminal 2 — Backend** (Flask on :5001 — port 5000 is taken by macOS AirPlay Receiver; override with the `PORT` env var):

```bash
cd Backend
python3 -m venv ../.venv          # first time only
source ../.venv/bin/activate
pip install -r requirements.txt   # first time only
python rag_index.py               # build the RAG index (needs Ollama)
python app.py
```

**Terminal 3 — Frontend** (Angular on :3000, proxies `/api` → :5001):

```bash
cd Frontend
npm install                       # first time only
npm run dev
```

Open http://localhost:3000.

Configuration is environment-driven — see [Backend/.env.example](Backend/.env.example).

## Tests

```bash
cd Backend
USE_OLLAMA=0 python tests.py
```

1,716 tests (pytest's own collected count, as of the eval-harness branch) covering: major parsing (including Premed aliases and a course-code
mentioned mid-sentence not shadowing an explicit major statement), course
parsing (all code formats + aliases), state merging with removal language,
prerequisite eligibility (including AND-vs-OR prereq-group regressions),
chat-based start-year detection and override, every catalog year for every
major independently reaching graduation with zero warnings, flowchart-vs-
catalog weighting, ranking order, Mermaid validity/fallback for all three
visualizations (including pixel-level color-class assertions), Gen Ed course
recommendations and overrides across all 10 domains, minors and double-major
merging, the Transfer Credit distance/ranking/refresh logic, API response
shape, and the full acceptance prompt.

## Evaluation

`eval/run_eval.py` measures four things and writes `eval/results/latest.json`
(date, commit hash, model name, dataset sizes, and every metric with its
numerator and denominator). Findings and individual failures are written up in
[`eval/FAILURES.md`](eval/FAILURES.md).

The first run found two engine bugs (a Gen Ed prerequisite bypass and an
open-elective co-requisite gap); both were fixed in commit `d79e6db1`.
Results below are from that fixed code. The "before" column is the same
harness against the commit just before the fix.

```bash
python eval/run_eval.py                      # all four (needs Ollama for evals 3 and 4)
python eval/run_eval.py --only parser,plans  # the two deterministic ones, no LLM
```

Results from commit `d79e6db1` (local `llama3:latest` via Ollama):

| # | Eval | Before fix (`8664b19c`) | Now | Notes |
|---|---|---|---|---|
| 1 | Parser, exact match | 55 / 84 | **55 / 84 (65.5%)** | 84 synthetic messages; 0 false changes on the 28 no-change and question cases |
| 1 | Parser, recall of courses that should be added | 16 / 41 | **16 / 41 (39.0%)** | precision 16 / 16; unchanged, parser was not touched |
| 1 | Parser, major / start year, when a change was expected | 16 / 23, 3 / 10 | **16 / 23, 3 / 10** | |
| 2 | Plan validity, every degree plan, zero violations | 657 / 1033 (63.6%) | **1033 / 1033 (100%)** | independent checker, strict |
| 2 | Plan validity, 250 random completed sets, zero violations | 124 / 250 (49.6%) | **205 / 250 (82.0%)** | seed 20261015; 239 / 250 if placement-waivable math rows are excluded |
| 3 | LLM: reply shown to student names a code not in the facts | 0 / 45 | **0 / 49** | after the app's own guard |
| 3 | LLM: raw model reply names such a code | 6 / 60 | **6 / 60 (10.0%)** | guard discarded 11 / 60 this run (15 / 60 on the previous run) |
| 3 | LLM: plan differs, LLM on vs off | 0 / 60 | **0 / 60** | control (off vs off) also 0 / 60 |
| 4 | RAG retrieval Recall@4 | 29 / 29 | **29 / 29 (100%)** | `Backend/rag_eval.py`; see limits |

The 45 random sets that still fail: 34 fail only on developmental-math rows
the engine waives through placement, and 11 contain rows that trace to the
checker not modelling the engine's honors-variant and "a higher math course
proves a lower one" equivalences (see `eval/FAILURES.md`, section 1). No
remaining row has been shown to be an engine bug. That is a statement about
what this checker can see, not proof the plans are correct.

### Limitations

* **Synthetic data.** The parser messages and LLM prompts were written by the
  harness author, not collected from students. No real student messages or
  transcripts are stored. The parser set in particular reflects one person's
  idea of messy phrasing, so 65.5% is a number about this set, not about
  real traffic.
* **Same catalog source as the engine.** The plan-validity checker is
  independent code (it imports nothing from the planner) but reads the same
  `catalogs/*.json` and `data/course_aliases.json` the engine uses. It can
  catch the engine misusing the data; it cannot catch the data being wrong,
  or the catalog disagreeing with PSU's real rules.
* **The strict checker does not model placement or course equivalence.**
  201 random-set rows are developmental-math prerequisites the engine waives
  through its ALEKS placement model, and 28 more come from honors variants
  (MATH 140H for MATH 140) or a higher math course standing in for a lower
  one. The engine treats all of these as satisfied by design. They stay in
  the headline rather than being waived, so the strict number is a floor.
* **Random completed sets are generated from the plan's own prerequisite
  structure, not from real transcripts.**
* **The RAG questions name the exact course code or title** they are looking
  for, so near-perfect recall is expected and says little about paraphrased
  questions.
* **One local model, run once per prompt.** LLM replies vary between runs
  (the guard discarded 13, 14, 15 and 11 of 60 across four runs). Groq and larger
  models were not tested.
* **CI runs evals 1 and 2 only** and reports; it does not fail on a metric
  threshold because none has been agreed. Evals 3 and 4 need a live LLM and
  the gitignored RAG index, so they are run by hand.

## Example prompts

- `I am a freshman with no credits. What should I take?`
- `I am a CMPSC major. I have completed CMPSC 131, CMPSC 132, MATH 140, and MATH 141. I want courses that follow the official advising path, unlock upper-level classes, and help with software engineering internships. What should I take next?`
- `I am a premedicine student. I've completed BIOL 110, CHEM 110, CHEM 111, MATH 140, and ENGL 15.`
- `Oh, I started school in 2022.` *(switches to that catalog year's real requirements, even without touching the dropdown)*
- `I also finished PHYS 211.` *(state persists across messages)*
- `I dropped MATH 141.` *(removal)*
- `CMPSC 360 isn't available over the summer.` *(with "Allow Summer Courses" on, reschedules it)*
- `Can I take CMPSC 465 next semester?` *(question — doesn't mark it completed)*

## API

- `GET /api/health` → `{"status": "ok"}`
- `GET /api/degree-plans` → available `{major, catalog_year, title}` plans
- `POST /api/plan` — body: `{"prompt", "major", "completed", "start_year", "grad_years", "allow_summer", "summer_unavailable"}`.
  Returns `state`, `eligible`, `graph`, `rag_response`, `llm_flowchart`, and a
  structured `coursePlan` (recommendations with score/source/reasons, tips,
  nextSemester, fullPlan, progress, matched courses, `unlockMap`,
  `semesterFlowchart`).
- `POST /api/transfer-credit` — body: `{"zip_code", "courses": [...]}`.
  Returns PA community colleges ranked by distance (and, once cached, by how
  many of the requested courses transfer there).

## Adding a new major / catalog year

1. Copy an existing plan (e.g. `Backend/degree_plans/PREMED-2026.json`) →
   e.g. `CMPEN-2027.json`.
2. Fill in the semesters from that major's official flowchart PDF or the
   bulletin's Suggested Academic Plan tab (`options`, slot `label`s,
   `credits`, `etm` flags, `departments`).
3. Run `USE_OLLAMA=0 python tests.py` and simulate the new plan's full path
   to graduation — it must reach 0 warnings before it's trustworthy.
4. Restart the backend — it appears in `/api/degree-plans` and the UI
   dropdown automatically (one entry per major; catalog year is picked up
   from the "Started college" control, not baked into the dropdown).

See [docs/EXPANSION_PLAN.md §1](docs/EXPANSION_PLAN.md) for the discovery
process used to find every PSU major and college, and the phased rollout
plan for building them out.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Replies are plain bullet lists | Ollama isn't running — start `ollama serve` (the app still works deterministically) |
| `Could not reach Ollama` from `rag_index.py` | Start Ollama and `ollama pull nomic-embed-text:latest` |
| A flowchart course is "unschedulable" | Its bulletin prereq references a course not in the plan — check the degree-plan JSON `options` (see CMPSC/CMPEN 315 cross-listing) |
| Catalog seems stale | Delete `Backend/catalogs/<dept>_catalog.json`; it re-scrapes on next request |
| "Port 5000 in use" / API not reachable | macOS AirPlay Receiver owns port 5000 — the backend runs on **5001** by default. Change it with the `PORT` env var and keep `Frontend/proxy.conf.json` in sync |
| Port 3000 in use | Stop the other process or change the Angular dev-server port in `Frontend/angular.json` |
| CORS errors in production | Set `CORS_ORIGINS` env var to your real origin(s) |
| "Started college" dropdown doesn't reflect a chat correction | Make sure `AppComponent` is passing `[activeStartYear]`/`[activeGradYears]` down to `ChatbotComponent` — these sync from `plan.state`, not local-only signals |
