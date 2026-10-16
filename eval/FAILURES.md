# Evaluation findings

Everything here was measured by `python eval/run_eval.py` (full output in
`eval/results/latest.json`, which also holds every individual violation and
failing case).

Status: the first run found two engine bugs, items 1a and 1c below. Both
were fixed in commit `d79e6db1` after approval and are marked **FIXED**; the
numbers in section 1 show before and after. Everything else listed here is
unfixed. No change was made to the harness to improve any number.

How to read the severity labels: **bug** means the behavior looked wrong
against the engine's own documented intent; **by design / known trade-off**
means the code comments say it is deliberate and the checker is simply
stricter than the engine; **checker limitation** means the engine is behaving
as documented and the independent checker does not model that rule.

---

## 1. Plan validity (Eval 2)

Strict independent checker (`eval/independent_checker.py`), reading the raw
catalog JSON, no engine imports.

| Population | Before fix (`8664b19c`) | After fix (`d79e6db1`) |
|---|---|---|
| Every `degree_plans/*.json` (1033 plans), zero violations | 657 / 1033 | **1033 / 1033** |
| 250 random completed-course sets (seed 20261015), zero violations | 124 / 250 | **205 / 250** |
| Random sets, if developmental-math rows are excluded | 161 / 250 | 239 / 250 |

Violation rows by cause (a "row" is one course in one location of one
response, so a single bad course can appear in several rows):

| Cause | Sweep before | Sweep after | Random before | Random after |
|---|---|---|---|---|
| 1a. Department not in plan's `departments` (**FIXED**) | 714 | 0 | 166 | 0 |
| 1b. Developmental-math prerequisite | 264 | 0 | 268 | 201 |
| 1c. Co-requisite partner never scheduled (**FIXED** for open electives) | 46 | 0 | 18 | 10 |
| 1d. MATH-ladder prerequisite not literally completed | 0 | 0 | 18 | 18 |

### 1a. Gen Ed picks skipped prerequisite checks when the course's department catalog was not loaded  (bug, FIXED in d79e6db1)

* **Top offender: `AGBM 106`**, 675 of the 714 sweep rows and 164 of the 166
  random-set rows. Example: `ACTING-2023`, Fall 2027, AGBM 106 scheduled
  needing one of AGBM 101, ECON 102, SCM 200, STAT 200, STAT 240, STAT 250
  with none completed. Others: ACCTG 461 (18 sweep rows), CMPSC 200 (9),
  SCM 301 (5), MICRB 201 (5), EET 101 (2).
* **Cause.** `AGBM 106` is in the Gen Ed GQ list, not the plan JSON, and
  `catalogs/agbm_catalog.json` has its real prerequisites. A plan's merged
  catalog only holds its own `departments`, so for a plan without AGBM the
  course was absent and `_pick_gen_ed_course` skipped every prerequisite,
  co-requisite and exclusion check (`if course:`). The same pattern in the
  plan-item candidate loop let SCM 301 and MICRB 201 through.
* **Fix.** Checks now fall back to the full catalog (`_course_for_checks`
  in `Backend/planner_engine.py`). Side effect worth knowing: with nothing
  completed, the default GQ autofill for COMM changed from AGBM 106 to
  CMPSC 203, and CMPSC 101 is no longer offered as Gen Ed to a student who
  has not completed MATH 21 (in the `/api/gen-ed-autofill` path, which
  takes no placement). Three tests that hardcoded the old picks were
  updated; the all-plans-graduate gate still passes.

### 1b. Developmental math prerequisites  (by design / known trade-off)

* A row lands here when every unmet alternative is a MATH 1 to 41 course.
  Before the fix, 247 sweep rows were `CMPSC 101`; all disappeared with the
  1a fix. What remains, random sets: `CHEM 110` needs
  `MATH 22` (116 rows), `STAT 200` (22), `STAT 184` (11), `CMPSC 101` (9),
  `STAT 250` (6), `EME 210` (6), `ACCTG 211` (6), `DS 200` (5), plus a tail.
* The engine has an explicit ALEKS/high-school-calculus placement model
  (`Backend/math_placement.py`) that waives these stepping stones. The
  strict checker does not model placement, and the harness's synthetic
  students state no placement. Left in the headline on purpose.

### 1c. Co-requisite partner never scheduled  (bug, FIXED for open electives; one residual)

* **Cause.** `_pick_open_elective` checked prerequisites and exclusions but
  never co-requisites, so an open-elective slot ("400-level BMB, CHEM or
  MICRB elective", "General Technical Elective") could pick CHEM 408
  without CHEM 452, BMB 403 without BMB 402, AERSP 403A without AERSP 462,
  CHEM 451 without CHEM 450, MUSIC 414, CMPSC 413. 46 sweep rows and 18
  random-set rows before the fix.
* **Fix.** It now takes the term's already-picked codes and requires the
  partner to be completed or picked.
* **Residual: 10 random-set rows, all `CMPSC 121`** (needs MATH 110 or
  MATH 140 concurrently). In all three affected sets (53, 59, 119) the
  student completed `MATH 140H` or `MATH 140B`, an honors/variant of
  MATH 140. The engine treats a completed variant as the base course
  (`_completed_variant`); the independent checker's alias file does not map
  it. **Checker limitation**, not an engine bug.
* **Data issue, not engine:** `BA 420` lists `BA 321 or BA 420` as its own
  concurrent requirement in the scraped catalog. Its one sweep row no longer
  appears after the fix; I did not trace why.

### 1d. MATH-ladder prerequisites not literally in the completed set  (checker limitation, now explained)

Earlier recorded as unexplained. I traced the completed sets for all
sample ids involved; every one is a case the engine models and the checker
does not:

| Sample | Course | Needs | Student completed | Engine rule |
|---|---|---|---|---|
| 28, 204 | MATH 111, FDSC 405 | MATH 110 | MATH 140 | a completed higher course proves a lower one unnecessary (`math_placement.py` docstring) |
| 180 | MET 111, MET 213 | MATH 26 or 81 | MATH 140, 21, 22 | same ladder rule |
| 170 | MET 206 | MATH 22 or 82 | MATH 21, 41, 81, 83 | MATH 41 shares tier 2 with 22 and 26 |
| 44, 48, 241, 249, 59 | PHYS 250, STAT 301 | MATH 140 or 110 | MATH 140H / 140B | honors/variant counted as the base course |

None of these has been shown to be an engine bug. Whether the engine's
ladder rule matches PSU's real policy is a separate question this eval
cannot answer, because the checker shares the engine's catalog data.

---

## 2. Parser accuracy (Eval 1)

84 synthetic, hand-labelled messages. Labels describe what the student
means, not what the engine returned; no label has been revised after seeing
results (`meta.label_revisions` is empty).

* **Exact match: 55 / 84 (65.5%).** Per-field accuracy: major 77/84,
  start_year 77/84, added 69/84, removed 81/84, wanted 81/84,
  excluded 80/84. These per-field numbers are inflated by the 14 no-change
  and 14 question cases that most fields trivially get right; the
  "when a change is expected" figures in `latest.json` are the honest
  ones (added 12/27, start_year 3/10, major 16/23).
* **False positives: 0** on every field when nothing should change
  (denominators 57 to 77). The parser errs toward doing nothing.
* **The one wrong-direction error:** `mix-08` ("Biology major, started 2022,
  completed BIOL 110 and CHEM 110, I want to take BIOL 220W") put BIOL 110
  and CHEM 110, which were *completed*, into `wantedCourses` and recorded
  none of the other facts. Suspected bug (a completion statement leaking
  into the wanted list). It is the only failing case that is wrong rather
  than merely incomplete.

Failure clusters (all 29 failing case ids are in `latest.json`):

| Cluster | Cases | Example |
|---|---|---|
| Subject-only aliases not resolved ("bio major", "mechanical engineering", "premed") | major-03, 06, 07, mix-04, 05, 07, 08 | "I'm a bio major" |
| Started-year phrasings other than "started in/class of" | year-03, 06, 07, 08, mix-01, 02, 08 | "My first semester was Fall 2022" |
| Completions not detected with certain verbs/shapes | done-05, 07, 08, 11, 14, 17, 18, mix-01 to 04, 06, 07 | "Completed CHEM 110 and CHEM 112", "i've done cmpsc 360 and cmpsc 465 over the summer" |
| Removal phrasings | rem-04, 06, 09 | "uncheck CMPSC 131", "I withdrew from MATH 141" |
| Wanted / excluded phrasings | want-04, excl-04, excl-05, both-01 | "I would rather avoid CMPSC 473" |

Single-course control: `I have completed CMPSC 360` is recorded correctly,
so these are phrasing-coverage gaps, not a broken completion path.

`excl-05` ("skip CMPSC 465 for now") is a debatable label: "skip for now"
could reasonably be read as deferral rather than exclusion. It is counted as
a failure as labelled.

---

## 3. LLM faithfulness (Eval 3)

Model: `llama3:latest` via local Ollama (the app default; no Groq key set).
60 synthetic prompts, 12 (major, completed) contexts times 5 templates
(next / why / unlock / plan / ready). Each prompt is sent three times: LLM
off, LLM off with a different `PYTHONHASHSEED` (control), LLM on. Numbers
below are from the run against `d79e6db1`.

**(b) Plan differs, LLM on vs off: 0 / 60 (0%).** The control (LLM off vs
LLM off, different hash seed) is also 0 / 60. `graph.edges` order is
nondeterministic even with the LLM off, so edges are sorted and
`rag_response` excluded before comparing. This matches the design: the LLM
only rephrases text. The Gen Ed and co-requisite fixes did not change this.

**(a) Course codes not in the verified facts.** Two readings, because the
app's own guard (`_phrased_reply_stays_grounded`, `Backend/app.py:2930`)
makes the headline one good by construction:

| Reading | Result |
|---|---|
| Reply **shown to the student** names a catalog code not in the facts, the prompt, or the completed list | **0 / 49** |
| **Raw model output**, before the guard, names such a code | **6 / 60 (10.0%)** |
| Raw model output names a code-shaped token in no catalog | 0 / 60 (1 / 60 on the previous run) |
| Raw model replies the guard discarded (any reason) | **11 / 60 (18.3%)** |
| Prompts actually phrased by the model | 49 / 60 (81.7%) |

So the user-visible rate is 0%, but only because the guard discards a
fraction of the model's replies. Without it roughly 1 in 10 replies would
have named a course the engine never produced.

* All six raw failures are `unlock` or `ready` prompts, naming codes such as
  `CMPSC 205`, `CMPSC 348`, `STAT 418`, `BIOL 224` that were not in the
  engine's reply text. I have not checked whether those are true statements
  about the catalog; the metric is "not in the verified facts", not "false".
* 11 discards but 6 raw replies with an unsupported code: the other 5 were
  discarded for reasons my metric does not model (the guard also rejects a
  reply that flips an eligibility verdict, and its code filter is not
  identical to mine). Not broken down further.
* **LLM output varies run to run.** Across four full runs the guard
  discarded 13, 14, 15 and 11 of 60. Treat these as a sample from one local
  model, not a constant. A larger hosted model (Groq) was not tested.
* 60 hand-written prompts from 12 contexts is a small, templated sample.

---

## 4. RAG retrieval (Eval 4)

* **Recall@4: 29 / 29 (100%)**; hit-rank distribution: rank 1 = 27,
  rank 2 = 1, rank 3 = 1. No misses.
* This number is not strong evidence. The 29 questions in
  `Backend/rag_eval.py` mention the exact course code or title being
  retrieved, so a dense retriever finds them almost by construction. It
  would be unusual to score below this. A harder set (paraphrase only, no
  code or title) is the real test and does not exist yet.
* The RAG index file is gitignored (`*_index.json`), so this eval cannot run
  in CI and must be run by hand with Ollama up.
