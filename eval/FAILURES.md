# Evaluation findings

Everything here was measured by `python eval/run_eval.py` (full output in
`eval/results/latest.json`, which also holds every individual violation and
failing case). Nothing in `Backend/`, the engine, or the data was changed to
produce these numbers, and nothing listed below has been fixed. Each item
needs a decision from the maintainer before any code changes.

How to read the severity labels: **suspected bug** means the behavior looks
wrong against the engine's own documented intent; **by design / known
trade-off** means the code comments say it is deliberate and the checker is
simply stricter than the engine; **unexplained** means I did not find the
cause and do not want to guess.

---

## 1. Plan validity (Eval 2)

Strict independent checker (`eval/independent_checker.py`), reading the raw
catalog JSON, no engine imports.

| Population | Plans with zero violations |
|---|---|
| Every `degree_plans/*.json` (all majors, all catalog years), default start | **657 / 1033** |
| 250 random completed-course sets (seed 20261015) | **124 / 250** |

If the placement-waivable developmental-math rows (section 1b) are excluded
the numbers become 676 / 1033 and 161 / 250. That is a secondary,
clearly-labelled figure; the headline stays the strict one.

Violation rows by cause (a "row" is one course in one location of one
response, so a single bad course can appear in several rows):

| Cause | Sweep rows | Random-set rows |
|---|---|---|
| 1a. Course's department is not in the plan's `departments` | 714 | 166 |
| 1b. Developmental-math prerequisite (strict checker does not waive) | 264 | 268 |
| 1c. Co-requisite partner never scheduled or completed | 46 | 18 |
| 1d. Other prerequisite gap, all MATH-ladder (unexplained) | 0 | 18 |

### 1a. Gen Ed picks skip prerequisite checking when the course's department catalog is not loaded  (suspected bug, high impact)

* **Top offender: `AGBM 106`**, 675 of the 714 sweep rows and 164 of the 166
  random-set rows. Example: `ACTING-2023`, Fall 2027, AGBM 106 is scheduled
  but needs one of AGBM 101, ECON 102, SCM 200, STAT 200, STAT 240, STAT 250
  and none is completed or in an earlier term.
* Other courses in this bucket, sweep rows: ACCTG 461 (18), CMPSC 200 (9),
  SCM 301 (5), MICRB 201 (5), EET 101 (2). Random-set rows: CMPSC 200 (1),
  EET 101 (1).
* **Mechanism (read from the code, not yet confirmed with a debugger).**
  `AGBM 106` is in the Gen Ed GQ list (`data/gen_ed_courses.json`), not in
  the plan JSON, and `catalogs/agbm_catalog.json` does have its real
  prerequisites. But the engine only merges catalogs for the plan's own
  `departments`, so for a plan whose departments exclude AGBM the course is
  absent from the merged catalog. `_pick_gen_ed_course`
  (`Backend/planner_engine.py:761`) then does `course = catalog.get(code)`
  and `if course:` guards every prerequisite / co-requisite / exclusion
  check, so a course that is not in the merged catalog is offered with no
  prerequisite check at all. The function's own docstring says this is
  deliberate ("most Gen Ed courses aren't in any scraped catalog, so they're
  offered prereq-unchecked").
* **Why it is still a finding:** that comment's premise is false for AGBM 106
  and the other courses above, because a scraped catalog for them exists on
  disk. The engine therefore knows the prerequisites and does not apply them.
  The visible effect is a plan that schedules a course the student cannot
  register for.
* **Possible fix directions (not applied):** check prerequisites against
  `load_full_catalog()` for Gen Ed picks, or fall back to the full catalog
  when the department is not in the plan's merged catalog.

### 1b. Developmental math prerequisites  (by design / known trade-off)

* A row lands here when every unmet alternative is a MATH 1 to 41 course.
  Sweep: `CMPSC 101` (247 rows), `MATH 110` (12), `SCM 200` (5). Random
  sets: `CHEM 110` needs `MATH 22` (116), `CMPSC 101` (73), `STAT 200` (22),
  `STAT 184` (11), `STAT 250` (6), `EME 210` (6), `ACCTG 211` (6),
  `DS 200` (5), and a tail of eight more courses.
* The engine has an explicit ALEKS/high-school-calculus placement model
  (`Backend/math_placement.py`) that treats tier-1/2 stepping stones as
  waived, and this is surfaced to the student as such. The strict checker
  does not model placement, so it flags these.
* Left in the headline on purpose: whether a student has placed past
  MATH 21/22 is a fact about the student, and the harness's synthetic
  students do not state one. This is reported, not hidden.

### 1c. Co-requisite partner is never scheduled  (suspected bug)

* Sweep rows, course (missing partner): `CHEM 408` (`CHEM 452`) 12,
  `AERSP 403A` (`AERSP 462`) 10, `BMB 403` (`BMB 402`) 10, `CHEM 451`
  (`CHEM 450`) 6, `MUSIC 414` (one of ten `STRNG 3xx` courses) 5,
  `CMPSC 413` (`CMPSC 463`) 2.
* Random-set rows: `CMPSC 121` (needs `MATH 110` or `MATH 140` concurrent)
  10, `AERSP 403A` 3, and one each for `BME 403` (`BME 402`), `ENGL 10`
  (`ENGL 15`), `CMPSC 413`, `CHEM 408`, `CHEM 451`.
* The course is scheduled but its required concurrent partner appears in
  neither the same term, an earlier term, nor the completed set.
* **Self-referential catalog entry:** `BA 420` lists `BA 321 or BA 420` as
  its own concurrent requirement (1 sweep row). That is a data issue in the
  scraped catalog, not an engine issue.

### 1d. MATH-ladder prerequisites not in the completed set  (unexplained)

18 random-set rows, all in sets where the completed list lacks the MATH
course that the plan then relies on:

| Sample | Plan | Course | Needs one of |
|---|---|---|---|
| 28 | ESP-2022 | MATH 111 | MATH 110 |
| 44, 48, 241, 249 | INTSC-2026, MICRB-2024, EET-2025, MICRB-2025 | PHYS 250 | MATH 140, 22, 26, 41, 81, 82 |
| 59 | ASTRO-2025 | STAT 301 | MATH 110, MATH 140 |
| 170 | METBH-2022 | MET 206 | MATH 22, MATH 82 |
| 180 | IBE-2022 | MET 111, MET 213 | MATH 26, MATH 81 |
| 204 | FDSC-2023 | FDSC 405 | MATH 110 |

`math_placement.py` documents that tier-3+ courses (MATH 110 / 140) are
**not** waived by placement and "still have to be actually taken", so these
are not obviously explained by the placement model. I have not traced a
single one through the engine. Treat as unexplained until someone does.

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

Model: `llama3:latest` via local Ollama (the app default; no Groq key was
set). 60 synthetic prompts, 12 (major, completed) contexts times 5 templates
(next / why / unlock / plan / ready). Each prompt is sent three times: LLM
off, LLM off with a different `PYTHONHASHSEED` (control), LLM on.

**(b) Plan differs, LLM on vs off: 0 / 60 (0%).** The control (LLM off vs LLM
off, different hash seed) is also 0 / 60, so the comparison is not hiding
noise. `graph.edges` order is nondeterministic even with the LLM off, so
edges are sorted and `rag_response` is excluded before comparing. This
matches the design: the LLM only rephrases text.

**(a) Course codes not in the verified facts.** This has two readings, and
the app's own guard (`_phrased_reply_stays_grounded`, `Backend/app.py:2930`)
makes the headline one trivially good:

| Reading | Result |
|---|---|
| Reply **shown to the student** names a catalog course code not in the facts, the prompt, or the student's completed list | **0 / 45** |
| **Raw model output**, before the guard, names such a code | **6 / 60 (10.0%)** |
| Raw model output names a code-shaped token that is in no catalog | 1 / 60 (`MATH 136`, prompt `llm-06-next`) |
| Raw model replies the guard discarded (any reason) | **15 / 60 (25.0%)** |
| Prompts that fell back to the deterministic text because the LLM reply was discarded | 15 / 60, so only 45 / 60 (75.0%) were actually phrased by the model |

So the user-visible rate is 0%, but only because the guard throws away a
quarter of the model's replies. Without the guard roughly 1 in 10 replies
would have named a course the engine never produced.

* All six raw failures are `unlock` or `ready` prompts. The model named
  codes such as `CMPSC 205`, `CMPSC 348`, `STAT 418`, `BIOL 230M`, `BMB 251H`
  that were not in the engine's reply text. I have not checked whether these
  are true statements about the catalog; the metric is "not in the verified
  facts", not "false".
* 15 discards but only 6 raw replies with an unsupported code: the other 9
  were discarded by the guard for reasons my metric does not model (it also
  rejects a reply that flips an eligibility verdict, and its code filter is
  not identical to mine). I did not break these down further.
* **LLM output varies run to run.** Across three full runs the guard
  discarded 13, 14 and 15 of 60. Treat the 6 / 60 and 15 / 60 as a sample
  from one local model, not a stable constant. A larger hosted model
  (Groq) would behave differently and was not tested.
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
