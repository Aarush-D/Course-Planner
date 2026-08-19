# Backend testing

`tests.py` is the whole suite (556 tests as of this writing) run with
`unittest`, discoverable and runnable via `pytest`. CI (`.github/workflows/ci.yml`)
runs it on every push and pull request, plus a separate job that builds the
Angular frontend to catch compile/type errors.

## Running locally

```bash
cd Backend
pip install -r requirements-dev.txt
python -m pytest -v          # whole suite
python -m pytest -v -k CMPSC # just tests whose name matches "CMPSC"
python -m unittest tests -v  # equivalent, no pytest needed
```

## What's in the suite

The classes fall into a few natural categories. There's no enforced marker
system separating them (pytest markers would work but add ceremony this
suite hasn't needed) — the class name and docstring tell you which kind
you're looking at:

**Engine/mechanism unit tests** — exercise one function or one narrow
behavior in isolation, usually with small hand-built fixture plans rather
than a real major's data. Examples: `TestMajorParsing`,
`TestStartYearParsing`, `TestCourseParsing`, `TestStateMerging`,
`TestBulkCompletion`, `TestConversationalReply`, `TestExclusionConstraint`,
`TestPlanMerging`, `TestOptionRankingPrefersLoadBearingPrereqs`,
`TestEligibility`, `TestWeightedRanking`, `TestAndOrPrereqParsing`,
`TestMermaid`, `TestSemesterFlowchart`. New engine logic should get a test
here first, against a minimal fixture — it's faster to write and faster to
debug than reaching for a real major's plan.

**API contract tests** (`TestApiShape`) — hit the Flask app through
`app.test_client()` and check response shape, status codes, and payload
validation (`test_invalid_payload`, `test_transfer_credit_requires_zip`,
`test_transfer_credit_rejects_out_of_scope_zip`). These are what would
catch a route regression or a broken JSON contract before a frontend
consumer ever sees it.

**Regression tests, one class per major/minor** — roughly 160 classes named
`Test<Major>Plan` (e.g. `TestChemistryPlan`, `TestNursingPlan`). Each loads
that major's real `degree_plans/<CODE>-2026.json` against the real scraped
`catalogs/*.json` data and asserts `build_full_plan` reaches graduation with
zero warnings (`test_full_plan_reaches_graduation_in_*_years`), plus one or
more sequencing tests asserting a specific real prerequisite chain lands in
the right term order. These are both regression tests (they'll fail the
moment a catalog re-scrape changes a course's real prereqs in a way a plan
no longer accounts for — which is exactly what happened with the AND/OR
parenthesis prereq-parsing bug) and a live correctness check against PSU's
actual bulletin data.

**Edge-case / robustness tests** — `TestPlanEngineRobustness` specifically
holds regressions found by real bugs during the major build-out (e.g.
`test_duplicate_option_plan_terminates` — two items sharing a first-choice
option must not infinite-loop; `test_blocked_first_option_falls_back_to_second`
— an item must fall through to its second option when the first is
prereq-blocked). `TestAndOrPrereqParsing`'s
`test_nested_and_inside_parens_falls_back_to_one_merged_group` documents a
deliberately-permissive fallback for a genuinely ambiguous prerequisite
structure. When you fix a real bug, the regression test belongs next to
this pattern — a fixture that reproduces the exact failure mode, not a
retest of something already covered.

## Adding a new major or minor

Follow the shape of an existing `Test<Major>Plan` class: `setUp` loads the
plan + merged catalog once, one test asserts `warnings == []` and
`goal["met"]`, and any test covering a specific real prerequisite chain
explains *why* that chain matters in its docstring (what the real PSU
prerequisite is, and what bug or edge case it guards against) — see
`TestNursingPlan` or `TestChemistryPlan` for the pattern.

## Adding a new engine feature

Add a fixture-based unit test class first (small hand-built `plan`/`catalog`
dicts — see `TestPlanEngineRobustness` for the shape), then confirm nothing
in the ~160 real-major regression classes broke. Don't add a new
major-specific regression test to prove a general engine feature works —
that pattern belongs to the fixture-based classes.

## CI

`.github/workflows/ci.yml` runs on every `push` and `pull_request`:
- **backend-tests**: installs `requirements-dev.txt`, runs the full suite via
  `pytest`, uploads a JUnit XML report as a build artifact and (same-repo
  pushes/PRs only, not forks) publishes inline pass/fail annotations.
- **frontend-build**: `npm ci && npm run build` in `Frontend/` — there's no
  Angular unit-test harness set up yet (no `karma.conf.js`, no `*.spec.ts`
  files), so this job's job is to catch TypeScript/template compile errors,
  not component behavior. Adding real component tests (Jasmine/Karma or
  migrating to Jest, per Angular's current recommendation) is a separate,
  larger effort not covered here.

A CI failure on `backend-tests` means either a real regression, or (as of
this writing) one of the ~103 majors/minors still mid-fix after the
AND/OR-parenthesis prerequisite-parsing bug fix — check whether the failing
class is in that in-progress list before assuming a new bug.
