# PSU Course Planner

An AI College Course Planner for Penn State students. Students chat naturally —
*"I am a CMPSC major and I completed CMPSC 131 and calc 1. What should I take
next?"* — and the planner detects their major, matches their courses against
the real PSU bulletin, recommends a prerequisite-safe next semester from the
official advising flowchart, ranks every eligible course with a weighted score,
and lays out the full path to graduation.

## Architecture

| Layer | Technology |
|---|---|
| Frontend | Angular standalone components, signals, Tailwind, Mermaid |
| Backend | Flask + flask-cors, deterministic planning engine |
| Data | Scraped PSU bulletin catalogs, degree-plan JSON per major/year |
| AI | Local Ollama (llama3 for phrasing, nomic-embed-text for RAG) |

**Key principle: the LLM never decides.** Eligibility, recommendations,
scores, semester plans, and the Mermaid diagram are all computed
deterministically in Python ([Backend/planner_engine.py](Backend/planner_engine.py)).
Ollama only rephrases the verified facts conversationally (with RAG advising
notes as background) — and everything works with Ollama off (`USE_OLLAMA=0`).

- **Degree plans** ([Backend/degree_plans/](Backend/degree_plans/)) — one JSON per
  `{major, catalog year}` (e.g. `CMPSC-2026.json`) built from the official
  advising flowchart PDF: semesters, courses, option groups, GEN ED/elective
  slots, credits, Entrance-to-Major flags. **Add a major or year = add a file.**
- **Catalogs** ([Backend/catalogs/](Backend/catalogs/)) — auto-scraped from
  bulletins.psu.edu per department with enforced prereq/concurrent rules.
- **Weighted ranking** — every eligible course gets a score:
  +50 base, +100 official flowchart, +40 next flowchart semester, +30 core,
  +5/unlocked course (cap +40), +20 interest match, special-topics/internship
  courses excluded unless asked for.
- **Chat parsing** — major aliases (Computer Science→CMPSC…), course codes in
  any format (`CMPSC131`, `CMPSC-131`), spoken names ("calc 1"→MATH 140),
  completion wording ("I took…"), and removal wording ("I dropped…").
  The backend returns `state` in every response as the source of truth.

## Run locally

**Terminal 1 — Ollama** (optional but recommended):

```bash
ollama serve
ollama pull llama3:latest
ollama pull nomic-embed-text:latest
```

**Terminal 2 — Backend** (Flask on :5000):

```bash
cd Backend
python3 -m venv ../.venv          # first time only
source ../.venv/bin/activate
pip install -r requirements.txt   # first time only
python rag_index.py               # build the RAG index (needs Ollama)
python app.py
```

**Terminal 3 — Frontend** (Angular on :3000, proxies `/api` → :5000):

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

Covers major parsing, course parsing (all code formats + aliases), state
merging with removal language, prerequisite eligibility, flowchart-vs-catalog
weighting, ranking order, Mermaid validity/fallback, API response shape, and
the full acceptance prompt.

## Example prompts

- `I am a freshman with no credits. What should I take?`
- `I am a CMPSC major. I have completed CMPSC 131, CMPSC 132, MATH 140, and MATH 141. I want courses that follow the official advising path, unlock upper-level classes, and help with software engineering internships. What should I take next?`
- `I also finished PHYS 211.` *(state persists across messages)*
- `I dropped MATH 141.` *(removal)*
- `Can I take CMPSC 465 next semester?` *(question — doesn't mark it completed)*

## API

- `GET /api/health` → `{"status": "ok"}`
- `GET /api/degree-plans` → available `{major, catalog_year}` plans
- `POST /api/plan` — body: `{"prompt": "...", "dept": "CMPSC", "completed": [...]}`.
  Returns `state`, `eligible`, `graph`, `rag_response`, `llm_flowchart`, and a
  structured `coursePlan` (recommendations with score/source/reasons, tips,
  nextSemester, fullPlan, progress, matched courses).

## Adding a new major / catalog year

1. Copy `Backend/degree_plans/CMPSC-2026.json` → e.g. `CMPEN-2027.json`.
2. Fill in the semesters from that major's flowchart PDF
   (`options`, slot `label`s, `credits`, `etm` flags, `departments`).
3. Restart the backend — it appears in `/api/degree-plans` and the UI dropdown.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Replies are plain bullet lists | Ollama isn't running — start `ollama serve` (the app still works deterministically) |
| `Could not reach Ollama` from `rag_index.py` | Start Ollama and `ollama pull nomic-embed-text:latest` |
| A flowchart course is "unschedulable" | Its bulletin prereq references a course not in the plan — check the degree-plan JSON `options` (see CMPSC/CMPEN 315 cross-listing) |
| Catalog seems stale | Delete `Backend/catalogs/<dept>_catalog.json`; it re-scrapes on next request |
| Port 3000 in use | `npm run dev` picks the port from `Frontend/vite`/angular config; update `proxy.conf.json` if you change the backend port |
| CORS errors in production | Set `CORS_ORIGINS` env var to your real origin(s) |
