# Peer Coordination, Networking, and the Single Biggest Advising Problem

Research pass conducted 2026-09-02, going deeper on one specific question raised after
`docs/ADVISING_RESEARCH_FINDINGS.md` (which documented real, sourced advising *failures*): what is the
single biggest problem students actually face in advising/degree-planning, what's the strongest-evidence
fix, and — specifically — does the research support three features the team is actively deciding on right
now: a seat-limited course-registration waitlist system, a "take this class with your friends"
coordination feature, and an opt-in LinkedIn-based classmate-networking feature.

Read `docs/ADVISING_RESEARCH_FINDINGS.md`, `docs/ADVISING_RESEARCH_COVERAGE.md`, and
`docs/ADVISING_RESEARCH_TEST_OUTPUTS.md` first — this file does not repeat what's already established
there (the ~50%-never-told-if-on-track finding, the transfer-credit-loss pattern, the "signed off then
reversed" failure mode, etc.). It goes to new sources on new sub-questions.

**Sourcing caveat carried forward:** Reddit is still inaccessible to this research agent (confirmed again
this pass — same "domain not accessible" behavior as the original research). Nothing below leans on
Reddit. Two sources (Sacerdote's Dartmouth data, the Feld & Zölitz Dutch business-school data) are
international/non-US or decades old; flagged inline. Two PDFs (a Fairlie/Robles/Gross working paper and a
Bridgewater State paper) would not render as text for direct fetching — those findings are reported via
secondary summaries with the primary URL still given, and are marked as such below.

---

## Part 1 — The single biggest problem, and why it's not (only) "bad advice"

The original research pass found the ~50% gap in "was I ever told if I'm on track" (Inside Higher Ed /
Student Voice survey) and treated it as the headline finding. Going deeper into advisor-capacity and
course-access research suggests that gap is a *symptom* of two compounding structural problems, both with
harder, more causal evidence behind them than the survey data:

### Advisors are structurally too thin to do individualized work
- NACADA's 2011 National Survey of Academic Advising found a **median caseload of 296 advisees per
  full-time professional advisor**. ([nacada.ksu.edu/Advisor Load](https://nacada.ksu.edu/Resources/Clearinghouse/View-Articles/Advisor-Load))
- Industry benchmarking puts effective caseloads at roughly 175–300 students per advisor, with the 2030
  Boyer Commission recommending 250:1 — and **California community colleges averaging roughly 600
  students per advisor**, more than double the upper end of what's considered workable.
  ([eab.com/glossary/student-to-advisor-ratio](https://eab.com/glossary/student-to-advisor-ratio/))
- NACADA itself is explicit that it does **not** publish a recommended ratio, because caseload alone
  doesn't capture workload — but every source found agrees the realistic caseload most advisors actually
  carry leaves very little individualized time per student, which is the same root cause the original
  pass identified in the FAMU/UP Baguio/Rutgers cases (advisors making mistakes or giving generic answers
  under time pressure, not out of incompetence).
- **Penn State specifically:** as in the original pass, no public PSU-specific advisor-to-student ratio
  could be found. Penn State's own assessment guidelines ask each college to *describe* its
  advisee-to-adviser ratio and consultation frequency, but don't publish the resulting numbers.
  ([advising.psu.edu/assessment-guidelines](https://advising.psu.edu/assessment-guidelines)) The Daily
  Collegian "four different advisers" story (already in the original findings file) remains the only
  concrete PSU-specific data point found on advising continuity. No public source substantiates or
  quantifies a "proportional advising model" specifically — that term did not surface in any PSU page
  found by this pass either.

### Students structurally cannot get seats in the courses their plan requires
This is the part the original pass didn't dig into, and it turns out to be the more rigorously
*causally* documented harm of the two:

- **Ad Astra's national study**, reported by the Hechinger Report, found colleges meet real student demand
  for required courses only **about 15% of the time**, and **57% of students** end up spending more time
  and money on their degree because a required course wasn't available when they needed it.
  ([hechingerreport.org](https://hechingerreport.org/students-cant-get-into-basic-college-courses-dragging-out-their-time-in-school/))
- **Robles, Gross & Fairlie** (NBER Working Paper 26376, published in the *Journal of Public Economics*,
  2021) provide actual causal evidence, not just correlation: using a regression-discontinuity design
  around community-college waitlist cutoffs, they find getting shut out of a course significantly
  increases the probability a student takes **zero courses that semester** or transfers to a
  lower-quality two-year college. ([nber.org/papers/w26376](https://www.nber.org/papers/w26376))
- **Mumford** (NBER Working Paper 33800, "College Course Shutouts," Purdue-based data, reported via
  Hechinger) found first-year students denied a required course were **35 percentage points less likely to
  ever take that course again**, and 25 points less likely to enroll in related courses in that
  subject — i.e., a single closed section can permanently redirect a student away from a subject area, not
  just delay them.
- The same Hechinger reporting cites a gendered effect: women who can't get into a needed course lose an
  average of **$800 in extra tuition/housing and $1,500 in forgone wages per missed course**, and are
  **7% less likely to graduate in four years** for every course they're shut out of — with the effect
  concentrated in STEM fields that are already male-dominated.

**Conclusion on Part 1:** the single biggest, most defensibly-evidenced problem isn't one bad conversation
with one advisor — it's the combination of (a) advisors who structurally don't have time to do
individualized routing/troubleshooting for each student's plan, and (b) a registration system where being
shut out of one required course has real, causally-measured downstream costs (time, money, wages, and — for
some students — permanently steering them away from a subject). A thin advising layer sitting on top of a
scarce, opaque registration system is a worse combination than either problem alone: the exact moment a
student most needs individualized help (a closed section that breaks their plan) is the exact moment a
296:1 advisor has the least capacity to give it.

---

## Part 2 — The strongest-evidence advising change

Among everything reviewed, one intervention has the best causal evidence: **proactive, structured contact
initiated by the advising side, rather than a passive/on-demand model that waits for the student to come
in with a question.**

- **Bettinger & Baker (2014, *Educational Evaluation and Policy Analysis*)** ran a genuine randomized
  controlled trial — 17 student cohorts across 8 institutions — testing InsideTrack's proactive coaching
  model (advisors who called and checked in on students rather than waiting for office-hours visits).
  Students randomly assigned a coach were **4, 5, and 7 percentage points more likely to persist** at 6,
  12, and 18 months respectively, and the largest tracked effect (6.6 points) was still statistically
  significant **24 months after coaching ended**.
  ([nber.org/papers/w16881](https://www.nber.org/system/files/working_papers/w16881/w16881.pdf)) Honest
  caveat: the study found **no statistically significant effect on actual degree completion** — persistence
  went up, but the RCT doesn't prove more people graduated.
- **Digital nudges show the same pattern at much lower cost.** Moorpark College's two-way texting system
  around registration: **25% of students who got a registration-related text enrolled within the week**,
  versus **15% of students who didn't get one** — a real, measured difference from a cheap intervention.
  ([insidehighered.com, Dec 2024](https://www.insidehighered.com/news/student-success/academic-life/2024/12/16/four-ways-improve-course-registration-current))

The common thread: **the advising intervention that has actual RCT-grade evidence behind it is proactive
outreach, not better answers to questions students remember to ask.** This matters directly for a chat-based
tool like Course Planner — a chatbot that answers well when asked is still a passive/reactive model in the
Bettinger & Baker sense. The evidence favors the system reaching out first (e.g., "your plan just broke
because CMPSC 465 is full" before the student notices) over waiting to be asked.

---

## Part 3 — Does taking a hard class WITH friends measurably help? (for the "take together" feature)

The evidence here is real but more nuanced than a flat yes. Several distinct bodies of research bear on
this, and they don't all point the same direction.

### Positive, causal evidence — but the mechanism matters
- **Sacerdote (Dartmouth, NBER Working Paper 7469)** — the classic study, using **randomly assigned
  freshman roommates** (so genuinely causal, not just correlation from students choosing similar friends):
  a one-point increase in a roommate's GPA is associated with a **0.12-point increase in the student's own
  GPA**. Modest, but real, and specifically *not* about friend-selection — it's what happens when you're
  just thrown together with someone. ([povertyactionlab.org PDF](https://www.povertyactionlab.org/sites/default/files/research-paper/988_Peer-Effects-With-Random-Asignment-Results-for-Dartmouth-roommates_2001.pdf))
  Caveat: Dartmouth, early-2000s, elite private institution — not a directly comparable population to PSU.
- **Mehta, R. Stinebrickner & T. Stinebrickner (NBER working paper, Berea College)** found the mechanism is
  behavioral, not ability-transmission: for every 10 additional weekly study-hours a student's *friends*
  logged in high school, the student's own daily study time rose ~25 minutes and GPA rose ~0.1 points —
  and the same pattern showed up, smaller, for randomly assigned roommates too. Their finding in plain
  terms: **"studious friends" mattered more than "smart friends."**
  ([hechingerreport.org summary](https://hechingerreport.org/studious-friends-and-roommates-might-lead-to-higher-grades-in-college/))
- A related and important nuance from the broader peer-effects literature: grades rise with the ability of
  **study partners**, but this effect is specifically **absent for friends who are not also study
  partners** — i.e., simply being enrolled in the same class as a friend, without actually studying
  together, doesn't show a measurable effect on its own. Co-enrollment is not the active ingredient;
  co-studying is.
- **Formalized cohort/co-registration programs have the strongest, best-designed evidence of all.** Aulck,
  Malters, Lee, Mancinelli, Sun & West's large-scale study of Freshman Interest Groups (FIGs) at the
  University of Washington found FIG participants had **1.6 percentage points higher first-to-second-year
  retention** and higher course-completion rates than non-participants, with the effect **more pronounced
  for underrepresented racial-minority students**.
  ([journals.sagepub.com/doi/full/10.1177/23328584211021857](https://journals.sagepub.com/doi/full/10.1177/23328584211021857))
  A separate 33-institution study of Living-Learning Communities found participants' GPAs averaged **0.11
  points higher** than matched non-participants after controlling for incoming characteristics. Critically,
  the research on *what makes these programs work* says the most effective models are **opt-in,
  interest-based, and framed around opportunity rather than deficit** — design guidance that transfers
  directly to a "take this class together" feature.
- A separate randomized trial of mandatory peer-cooperative learning in STEM gateway courses
  (Bridgewater State University's STREAMS program, reported via a paper on PMC that could not be rendered
  as text and is cited from the secondary summary it produced) reported large effects: gateway-course GPA
  rising from 2.20 to 2.58, pass rates from 50.2% to 59.3%, and two-year STEM retention from 48% to 59%.
  This is the single largest effect size found in this whole research pass — but it describes
  **instructor-assigned, mandatory** small-group learning attached to a course, not students choosing to
  co-enroll with an existing friend, so it's suggestive rather than directly on-point for a friend-matching
  feature.

### The real, documented risk
- **Feld & Zölitz** ("The Persistent Effects of Short-Term Peer Groups on Performance," *Management
  Science*), using random assignment of students to peer groups during a mandatory orientation week at a
  Dutch business school and tracking them for four years, found that **students assigned to
  high-peer-ability groups performed worse in their first year and were more likely to drop out early —
  and this effect was driven entirely by exposure of lower-ability students to higher-ability peers.** The
  effect persisted: years later, exposure to higher-ability peers during that single orientation week still
  measurably lowered final GPA and pushed students away from the college's most popular major.
  ([pubsonline.informs.org/doi/10.1287/mnsc.2021.3993](https://pubsonline.informs.org/doi/10.1287/mnsc.2021.3993))
  Caveat: non-US institution, and it's about *assigned* group exposure during orientation, not students
  choosing to co-enroll with a specific existing friend — but the underlying mechanism (a less-prepared
  student following/being grouped with a stronger peer into something that's a mismatch for them
  individually) is exactly the failure mode a "take this hard class with your friend" feature could
  reproduce if a student joins a friend in a course that's a poor fit for their own preparation.
- Separately, self-report survey research on peer pressure finds real, if softer, evidence that comparing
  academic standing to peers is a source of anxiety for a meaningful share of students (one cited survey
  found roughly two-thirds of respondents reported anxiety or feelings of inferiority from comparing
  themselves to peers). This is weaker evidence — self-report, not causal — but consistent enough across
  sources to flag as a real design consideration, not dismiss.
- One reassuring null result worth noting: a UC Santa Cruz randomized trial of ~3,900 students randomly
  assigned lab partners in intro chemistry found female students showed **no negative effect** — no grade
  penalty, no higher drop rate, no reduced STEM persistence — from being randomly paired with a male
  partner, contrary to some prior survey-based concern about gender dynamics in partnered coursework. Not
  directly about friend-matching, but relevant reassurance for any pairing/partnership mechanic.

**Bottom line on Part 3:** the evidence for "take this class with a friend" is real but conditional. The
strongest, best-designed evidence (FIGs/learning communities) is for *structured, opt-in, interest-based*
cohort participation, not bare co-enrollment visibility. The mechanism that actually produces the benefit
is shared study behavior, not mere friendship or shared class attendance. And there's a specific,
well-documented risk — a less-prepared student being drawn into a harder-than-appropriate course to stay
with a stronger friend can measurably hurt that student's own first-year outcomes and persist for years.

---

## Part 4 — Does professional-networking/alumni access during undergrad help? (for the LinkedIn feature)

### Strong general evidence that networking access matters, especially for the students who lack it
- **Rajkumar, Saint-Jacques, Bojinov, Brynjolfsson & Aral, "A causal test of the strength of weak ties"**
  (*Science*, September 2022) is the strongest single piece of evidence in this whole research pass on the
  networking side: a set of large-scale randomized experiments run directly on LinkedIn's "People You May
  Know" algorithm, varying the prevalence of weak ties in the networks of **over 20 million people** over
  five years, during which **2 billion new ties and 600,000 new jobs were created** in the data. Finding:
  weak ties cause more job mobility than strong ties, but the relationship is an inverted U — **moderate-
  strength ties** (people you share roughly 10 mutual connections with but rarely interact with directly)
  are the most effective for job mobility, more so than either close friends or complete strangers.
  ([science.org/doi/10.1126/science.abl4476](https://www.science.org/doi/10.1126/science.abl4476);
  coverage: [news.stanford.edu](https://news.stanford.edu/stories/2022/09/real-strength-weak-ties))
  This is directly relevant to a classmate-networking feature: classmates in the same major, who you don't
  necessarily know well but share a lot of context with, are close to the exact "moderate tie strength"
  profile the study found most valuable — a stronger match than "connect with everyone" or "connect only
  with close friends."
- Research on first-generation and low-income students specifically ties this to inequity: those students
  have **disproportionately less access to this kind of network**, and multiple sources report that a
  large share of job openings are filled through networking/referral rather than open application, with a
  referred candidate reported as several times more likely to be hired than a non-referred one.
  ([the74million.org](https://www.the74million.org/article/who-you-know-social-capital-is-key-for-first-gen-students-career-success/);
  [ccwt.wisc.edu report](https://ccwt.wisc.edu/wp-content/uploads/2022/04/ccwt_report_Enhancing-Social-Capital.pdf))
  Honest caveat: the specific "4x more likely to be hired" and "70–85% of jobs filled by networking"
  figures are widely repeated in secondary sources but trace back to industry/recruiting figures this pass
  could not independently verify at the primary-source level — treat them as directionally credible, not
  precisely confirmed.

### What has *not* been studied: this exact product concept
No source found evaluates an **in-app, opt-in, classmate-only networking directory embedded in a
degree-planning tool** — that specific product shape has no direct research behind it either way. Everything
above is evidence that professional-network access matters generally (via LinkedIn broadly, or via campus
mentoring programs), not evidence about this specific mechanism. That gap should be stated plainly rather
than papered over.

### A real, if imperfect, cautionary pattern
Campus-specific social apps have a rough adoption/sustainability track record. Yik Yak lost roughly 76% of
its user base after introducing phone-number verification and eventually shut down; its successor Fizz
faces the same boom-and-bust pattern and is now banned across the UNC system alongside other anonymous
apps, largely over toxic content. ([techcrunch.com](https://techcrunch.com/2024/03/07/anonymous-social-apps-face-another-reckoning-as-unc-system-to-ban-yik-yik-fizz-sidechat-whisper/))
This is an imperfect analogy — Yik Yak/Fizz's core problem was anonymity enabling harassment, which an
identity-attached, opt-in LinkedIn feature specifically avoids by design — but it's real evidence that
campus social features can flame out quickly regardless of good intentions, and that a feature bolted onto
a utility app needs a real non-social reason for people to open it, or opt-in rates stay low. Separately,
general research on app adoption finds privacy-conscious users disproportionately decline data-sharing
features, which is relevant to how the opt-in flow itself should be designed (clear, specific, revocable
consent, not a broad one-time toggle).

---

## What this pass did not find evidence for either way

- No PSU-specific advisor caseload/ratio number, and no substantiation of a specific "proportional
  advising model" by that name at Penn State — consistent with the original pass's finding that PSU
  publishes advising philosophy, not operational metrics.
- No direct study of an in-app opt-in classmate/LinkedIn networking feature (Part 4) — the evidence is
  about network value generally, not this specific mechanism.
- No study of a student-built (non-institutional) course waitlist visibility layer specifically — the
  causal evidence (Robles/Gross/Fairlie, Mumford) is about actually being shut out of a course at the
  registrar level, not about whether a third-party planning tool's own waitlist UI changes outcomes.
- Two sources (Sacerdote/Dartmouth, Feld & Zölitz/Dutch business school) are from institutions and eras
  different enough from PSU today that their effect sizes should be read as directionally suggestive, not
  as numbers that would replicate exactly here.

---

## Product implications

### 1. Seat-limited course-registration waitlist system
**Verdict: strongly supported as a problem to solve.** The harm this targets — students shut out of a
required course — is the most rigorously, causally documented problem found in this entire research pass
(regression-discontinuity evidence from Robles/Gross/Fairlie and Mumford, plus the Ad Astra 57%/15%
national figures and the gendered wage-loss data). This isn't a soft "advising could be better" finding;
it's closer to the strongest evidence in either research pass.
**What to get right:** the nudge evidence (Moorpark: 25% vs. 15% follow-through from a text) argues for
*pushing* students a notification the moment a seat opens rather than making them poll — passive visibility
alone doesn't capture the benefit proactive contact showed in the RCT evidence. The Purdue/Mumford finding
(a shutout makes a student 35 points less likely to *ever* take that course) argues the feature should
surface real alternative sections/terms alongside the wait, not just a queue position — the causal harm
comes from the student giving up on the course entirely, not just from waiting.
**Real risk:** this app doesn't control PSU's actual registrar seat inventory. A self-built waitlist not
tied to real, live seat counts risks becoming exactly the "signed off, then reversed" failure mode the
original research pass warned about (a queue position that feels like a guarantee but isn't one) — the
feature needs to be scrupulously honest about what it can and can't promise, the same discipline the rest
of this project already applies to real-vs-invented data.

### 2. "Take this class with your friends" coordination feature
**Verdict: directionally supported, with real evidence for a specific design shape — not a blank endorsement
of "let friends see each other's schedules."** The best-designed evidence (FIGs, learning communities) is
for opt-in, interest-framed cohort participation, and the mechanism research consistently points to is
shared study behavior, not mere co-enrollment or friendship itself (grades track study partners, not
friends who aren't study partners; "studious friends" outweigh "smart friends").
**What to get right:** frame it around opt-in coordination and actual joint study/accountability (matching
the FIG design principle of "opportunity-framed, interest-based") rather than a passive "your friend is in
this class too" notice with no next step.
**Real risk:** the Feld & Zölitz orientation-week RCT is a concrete, causal warning — a less-prepared
student drawn into a harder course to stay with a stronger friend showed measurably worse first-year
outcomes and higher dropout probability, with effects still visible four years later. A feature that makes
it easy to follow a friend into a course without also surfacing whether that course fits *your own*
preparation and plan could reproduce that exact harm. Secondary, softer risk: self-report research on
peer-comparison anxiety suggests a UI that emphasizes visible progress comparisons between friends (not
just co-enrollment) could add pressure for some students rather than help.

### 3. Opt-in LinkedIn-based classmate networking
**Verdict: the underlying premise is well-supported at the general level; the specific product mechanism is
genuinely untested.** Rajkumar et al.'s large-scale causal LinkedIn study is strong evidence that
moderate-strength ties — people you share context with but don't know well, which describes classmates
better than close friends or strangers — are disproportionately valuable for job mobility, and separate
research shows first-generation/low-income students specifically lack this kind of access today, which is
exactly the population a degree-planning tool already serves.
**What to get right:** per the weak-ties finding, design toward connecting people who share real context
(same major, same course sequence) rather than a generic "add all classmates" model — that's the tie
strength the causal evidence actually supports. Consider prioritizing visibility/promotion of the feature
for exactly the students the social-capital research says benefit most (first-gen, limited existing
network), since the gap being closed is real and documented for that group specifically.
**Real risk:** no research validates this exact mechanism (in-app opt-in classmate directory), so treat
adoption assumptions as a hypothesis to test, not a settled bet. The nearest real-world analogy — campus
social apps generally — has a poor sustainability track record (Yik Yak/Fizz), though for a different
reason (anonymity-driven toxicity, which an identity-attached opt-in feature avoids by construction). The
more relevant transferable risk is generic app-adoption research: privacy-conscious users disproportionately
decline data-sharing features, so the opt-in flow itself (what's shared, with whom, how revocable) will
likely matter more to actual adoption than the underlying value proposition.
