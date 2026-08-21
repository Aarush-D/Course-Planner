# Backlog — Deferred Items to Revisit

A single reference list of everything that's been intentionally set aside, across every part of this
project — compliance follow-ups, data/feature work, and infrastructure decisions. Nothing here is started;
this exists so it doesn't fall through the cracks. Update this file (don't create a new one) as items get
resolved or new ones come up.

---

## Compliance & legal

Triggered by `docs/COMPLIANCE_AUDIT.md` — revisit each when its trigger condition happens, not before.

- **Real legal review of the Privacy Policy and Terms of Service.** Both are grounded in an honest audit of
  what the app actually does, but neither has been reviewed by an actual attorney. Do this before treating
  either as legally sufficient, and definitely before any real commercial launch.
- **GDPR applicability** — revisit if the app ever targets or monitors EU users at real scale (real accounts,
  behavioral tracking). Not triggered today: no accounts, no tracking.
- **CCPA/CPRA applicability** — revisit if the project ever has real revenue/user-count that could cross
  California's coverage thresholds. Not triggered today.
- **Business formation (LLC/corporation, tax ID, registration)** — only relevant if this becomes a real
  commercial product. A decision for Aarush/Suryansh/Justin with real advisors, not something to resolve in
  code.
- **`.venv/` git history cleanup** — removing it from *current* tracking (done) doesn't purge the old blobs
  from git history or the GitHub remote. A full purge needs `git filter-repo`/BFG, which rewrites every
  commit hash and breaks existing clones — a real, invasive decision that needs explicit sign-off, not
  something to do unilaterally. No real secret was found inside it, so this is a repo-hygiene/size issue,
  not an urgent security one.
- **Full accessibility audit** — current state is a partial pass (every input labeled, icon buttons have
  aria-labels). A real WCAG pass (axe-core/Lighthouse, keyboard-nav walkthrough, screen-reader test) hasn't
  been done.
- **Full security pen-test pass** — core checks are done (no exposed secrets, no SQL/command injection
  surface, CORS restricted, debug mode off by default, live-tested the Ollama integration). Not yet done:
  XSS-injection attempts into the chat textbox, rate-limit/spam abuse testing, and a real dependency
  vulnerability scan (`pip-audit`, `npm audit`).
- **Revisit Database Security and Authentication sections of the compliance audit** the moment real accounts
  or a database (Supabase, most likely — see below) get added. Both sections are currently N/A specifically
  *because* neither exists yet.

## Infrastructure — when to actually add it, not before

- **Supabase** (hosted Postgres + auth) — earns its place the moment real student accounts (login,
  persistence across devices) get built. Don't add it speculatively.
- **Upstash (Redis)** — earns its place for rate-limiting/caching once the backend is actually deployed
  publicly and facing real concurrent traffic. Don't add it speculatively.
- **Ollama Cloud vs. self-hosted** — cloud mode is wired up and tested (`OLLAMA_API_KEY` env var). The free
  tier caps at **1 concurrent generation** (verified live, not just from docs) — fine for light/spread-out
  usage, a real bottleneck under any burst of simultaneous users. Revisit (Pro plan, or a different hosted
  provider) once real concurrent load is a live problem, not before.
- **Deploying the backend + frontend publicly** — gunicorn/Procfile are ready (see the Tier-0 scaling work).
  Not yet deployed anywhere. See the hosting-cost writeup for the actual free-tier plan when this happens.

## Academic data / feature work

- **ALEKS math placement exam** — how it factors into a first-year student's plan (does it gate which MATH
  course a freshman can start in?), similar to the placement-gate prereq patterns the engine already models
  (e.g. STAT 184/200 needing MATH 21). Not researched yet.
- **High school / transfer credit intake** — AP courses, A-Levels, existing college credit transfers, and
  CLEP exams, and how each maps onto real degree-plan course codes. Check what the existing "Transferred
  courses" page already handles before scoping new work — likely partial overlap.
- **Branch campus Phase 2** — extend the Phase 1 metadata-only campus pass (done for one major) to the rest
  of the ~230 existing degree-plan files. Sized to the number of files, not the number of campuses — see
  `docs/BRANCH_CAMPUS_FINDINGS.md` §5 for the full phased plan.
- **Branch campus Phase 3** — first "Pattern B" major (a campus with a genuinely different curriculum, not
  just the same courses), needs a wholesale new `degree_plans` file, not just metadata.
- **Branch campus Phase 4** — course-offering honesty pass, once Phases 1–3 establish which courses are real
  at which campuses.
- **Graduate programs** — scoped in `docs/GRAD_AND_WORLD_CAMPUS_FINDINGS.md` Part 1; recommended first phase
  identified there but not built.
- **World Campus** — scoped in the same doc, Part 2; the "no University Park offering" mislabeling issue was
  already fixed as part of the multi-campus schema work, but the recommended first phase (year-vs-semester
  granularity for at least one real World Campus program) isn't built yet.
- **Smeal College of Business minor-declaration restrictions** — researched, no bulletin-published evidence
  found (same pattern as an earlier Data Science/CMPSC finding). No further action pending unless Aarush
  finds a different source.

## Team / attribution

- **Full last names for Suryansh and Justin** — currently credited as "Suryansh S." and "Justin H." (first
  initial only, per Aarush's instruction) across all showcase materials (digital doc, leave-behind, poster,
  landing page). Update everywhere consistently once/if full names are wanted, and note their specific
  contribution areas as those become concrete.
