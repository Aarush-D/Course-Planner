# Legal & Compliance Audit

**Date:** 2026-08-21
**Audited by:** Claude Sonnet 5, working through the codebase directly (grep, git history, dependency
scanners, live API testing) — not a lawyer, and this document is not legal advice. Anywhere a real legal
determination is required (jurisdiction, GDPR/CCPA/COPPA applicability, business formation), that's flagged
explicitly below and should go to an actual attorney before this project is treated as "compliant."

This works through the full checklist Aarush provided, in the same order, with `N/A`, `✅ Fixed`,
`✓ Verified clean`, or `⚠ Needs real legal review` on every item — grounded in what was actually found in
the code, not assumed.

---

## Status key
- **✅ Fixed** — a real issue was found and corrected in this pass.
- **✓ Verified clean** — checked directly (not assumed) and found to already be fine.
- **N/A** — the underlying feature doesn't exist in this app, with the reason stated.
- **⚠ Needs real legal review** — this document can describe the current state, but the actual
  determination requires an attorney, not an AI.
- **📋 Backlog** — applies once a specific future feature ships, not before.

---

## 1. Privacy Policy — ✅ Fixed

Added a real page at `/privacy` (Frontend/src/pages/privacy-page/), linked from the nav sidebar footer.
Grounded in an actual audit (see §3 below), not a generic template:
- States plainly what is and isn't collected (no accounts exist — no name/email/username/password/payment
  info/uploaded files ever collected).
- Discloses IP address logging via standard web server access logs.
- Discloses that chat text may be sent to Ollama's cloud API for AI phrasing, and explains what the
  deterministic engine does vs. what the AI does.
- States retention honestly: there's no database, so nothing persists server-side between requests.
- Includes contact email and effective date.
- Explicitly labeled "Draft — not yet reviewed by an attorney" — the content is accurate to the app's real
  behavior, but jurisdiction-specific legal sufficiency hasn't been confirmed by counsel.

## 2. Terms of Service — ✅ Fixed

Added a real page at `/terms`. Explicitly states:
- This is not an official Penn State product/service and isn't endorsed by Penn State.
- It's informational only — not a replacement for a real academic adviser or LionPATH.
- AI-phrased output can be wrong; users should always confirm with a real adviser.
- No warranty, limitation of liability, acceptable-use rules.
- Payments/subscriptions marked N/A (no such features exist).
- Governing law tentatively set to Pennsylvania, explicitly flagged as unconfirmed by an attorney.

## 3. Audit Everything the App Collects — ✅ Done

Actually checked, not assumed:

| Question | Finding |
|---|---|
| Database contents | **None.** No database exists in this project — verified by inspecting `Backend/requirements.txt` (no DB driver) and the engine code (all data comes from static JSON files loaded at request time, see `docs/course-planner-architecture` context). |
| Browser LocalStorage | **None used.** `grep -rn "localStorage\|sessionStorage" Frontend/src` returns zero matches. |
| Cookies | **None used.** `grep -rn "document\.cookie"` returns zero matches. |
| Server logs | Standard Flask/gunicorn access logs only — method, path, status, timestamp, client IP. No code anywhere calls `logging`/`print` on request bodies or chat prompt text (verified by grep). |
| Analytics tools | **None integrated.** No Google Analytics, Mixpanel, Segment, Sentry, PostHog, etc. anywhere in the codebase. |
| AI APIs | Chat prompt text + derived planning facts (course codes, credit counts) are sent to Ollama (local process or `ollama.com` cloud API depending on config) for reply phrasing only — never for the scheduling logic itself. |
| Auth providers | N/A — no authentication provider is wired in (no accounts exist). |
| Payment providers | N/A — no payment feature exists. |
| Backups | N/A — no database means nothing to back up server-side. |
| Unnecessarily retained data | None found — the architecture is already stateless-by-design (client resends full state each request), which happens to also be the most privacy-minimizing shape possible. |

## 4. API Keys and Secrets — ✅ Fixed (real issues found)

- **Grepped all tracked source files** for hardcoded secrets — clean.
- **Grepped full git history** (`git log --all -p`) for ever-committed secrets in source files — clean, nothing
  was ever committed and later removed.
- **Real issue found and fixed:** `.gitignore` listed `.venv/` but never excluded `.env` — meaning a real
  `.env` file (which `Backend/.env.example` explicitly invites creating) had no protection against being
  accidentally committed. Fixed.
- **Real issue found and fixed:** `.venv/` (7,295 files — the entire local Python virtual environment) was
  actually tracked in git despite being gitignored, because it was committed before the gitignore rule
  existed. Audited every tracked file inside it first — no real secrets found (just library source, a public
  CA certificate bundle, and compiled bytecode) — then removed it from tracking. See the note under
  "Follow-up" below about git history.
- **`openai` and `pinecone` Python packages** were installed locally but are in neither `requirements.txt`
  nor imported by any real code — dead weight from earlier experimentation, not hidden functionality. Not a
  security issue, just noted.
- The new `OLLAMA_API_KEY` is handled correctly: read from an environment variable only, never hardcoded,
  never sent to the frontend (the Ollama call happens entirely server-side in `Backend/app.py`), and `.env`
  is now actually gitignored.
- **Rotate the key Aarush pasted into this chat** — see the note at the top of this session's work. A secret
  transmitted in plaintext anywhere should be treated as exposed, even if this specific chat transcript
  never becomes public.

**Follow-up not done in this pass:** removing `.venv/` from *current* tracking doesn't purge it from git
*history* — those old file blobs still exist in past commits and on the GitHub remote until history is
rewritten (`git filter-repo` or BFG Repo-Cleaner). Since no real secret was found inside it, this is a repo
hygiene/size issue, not an urgent security one — but it's a real, invasive operation (rewrites every commit
hash, breaks any existing clones/forks) that needs Aarush's explicit go-ahead, not something to do
unilaterally.

## 5. Database Security — N/A

No database exists. Nothing to secure, no RLS to configure, no per-user record isolation to test, because
there's no concept of "a user's own records" anywhere in the current architecture. **Revisit when Supabase
gets added** — this is already tracked as a backlog item (see `docs/COMPLIANCE_BACKLOG.md`).

## 6. Authentication — N/A

No real authentication exists — this was an explicit, deliberate design decision earlier this session (the
"Try a demo student" page is fake, local-only sample data by design, not a shortcut that was supposed to be
real auth). Nothing here to audit yet: no passwords, no sessions, no password reset flow, no admin routes.
**Revisit when real accounts get built.**

## 7. HTTPS / Encryption — ⚠ Depends on hosting choice, not yet deployed

The app isn't deployed publicly yet. Every hosting option discussed (Render, Fly.io, GitHub Pages) provides
automatic HTTPS by default at no extra cost — this needs verification at deploy time, not a code change now.
No sensitive credentials are stored in browser storage (none is used at all, see §3). Server logs don't
contain passwords, tokens, or payment info (verified by grep — no such logging exists).

## 8. Account and Data Deletion — N/A

No accounts exist, so there's nothing to delete. The Privacy Policy states this plainly rather than claiming
a deletion flow that doesn't need to exist yet.

## 9. GDPR — ⚠ Needs real legal review if this ever targets EU users at scale

Current state: no accounts, no persistent storage, no behavioral tracking/monitoring of any kind — the
factors that most directly trigger GDPR's extraterritorial reach (offering goods/services to EU residents,
or *monitoring* their behavior) are largely absent today. This is **not a substitute for a real legal
determination** if the project ever grows into something with real EU user accounts or tracking — flagged
here as a backlog trigger, not resolved.

## 10. California Privacy Rules (CCPA/CPRA) — ⚠ Needs real legal review if scale/revenue thresholds are ever met

CCPA has business-size/revenue thresholds that this project, as a non-commercial student project with no
accounts and no revenue, does not currently approach. Not a substitute for real legal advice if that changes.

## 11. Cookies and Tracking — N/A, verified

Zero cookies, zero tracking technology of any kind, verified by grep (see §3). Nothing to disclose because
nothing exists to disclose.

## 12. Copyright / Assets — ✓ Verified clean

- **No image assets at all** in the live app (`find src -iname "*.png" -o -iname "*.jpg" -o -iname "*.svg"`
  returns nothing) — no logo file, no stock photos, no illustrations to license.
- **No custom fonts loaded** by the live app (`index.html` has no font `<link>` tags) — it uses the system
  font stack. (The separate marketing materials built this session — the showcase page, the poster — do use
  Google Fonts, which are openly licensed for this exact use, loaded via Google's own CDN as intended.)
- **Icons are hand-coded inline SVG**, not external files — the path data matches the well-known Heroicons
  set (MIT-licensed, no attribution required, safe for any use including commercial).
- No music, video, or third-party branding anywhere in the app.

## 13. AI-Generated Code / Open-Source Code — ✓ Verified clean

Ran a real license audit, not a guess:
- **Python** (`pip-licenses` against `Backend/requirements.txt`'s actual dependency tree): every license is
  MIT, BSD, PSF-2.0, or similarly permissive. **Zero GPL/AGPL/LGPL** dependencies.
- **npm** (`license-checker` against the Angular frontend): 414 MIT, 104 ISC, 27 Apache-2.0, plus smaller
  permissive buckets (BSD, BlueOak, 0BSD, Unlicense, CC0). **Zero GPL/AGPL/LGPL** dependencies. The one
  "UNLICENSED" entry is the project's own unpublished `package.json`, not a third-party package.
- No copyleft attribution/notice obligations apply. A `LICENSES`/third-party-notices file isn't currently
  required given the license mix, but could be added as good practice if this is ever distributed more
  formally.

## 14. User-Generated Content — N/A

No feature lets users post, upload, or share content with other users or publicly. Nothing to moderate.

## 15. Age Requirements / Children — ✓ Addressed in the Privacy Policy

No accounts exist, so no age is ever collected. The Privacy Policy states the app isn't directed at children
under 13 and doesn't knowingly collect their information — standard COPPA-safe language for an app with no
account creation at all.

## 16. Accessibility — ✓ Partial pass done, not exhaustive

Checked directly:
- **Every `<input>` in the app has either an associated `<label>` or an `aria-label`** — verified by grep
  across every component, zero unlabeled inputs found.
- Icon-only buttons (chat close button, flowchart controls) have `aria-label`s.
- Standard Tailwind slate-on-white color choices throughout, which read as reasonable contrast on visual
  inspection.

**Not done:** a full automated WCAG audit (axe-core/Lighthouse), keyboard-navigation walkthrough of every
page, or a screen-reader pass. This is a real gap worth a dedicated session, tracked in the backlog doc —
claiming full accessibility compliance without that would be dishonest.

## 17–19. Payments, Subscriptions, Email Marketing — N/A

No payment processing, no subscriptions, no email sending of any kind exists in this app.

## 20. AI Features — ✓ Addressed

- Users are told (in both the app's own architecture and the Privacy/Terms pages) when they're interacting
  with an AI-phrased reply vs. the deterministic engine's own facts.
- Nowhere does the app claim to be a licensed adviser — the Terms explicitly say the opposite: not a
  replacement for a real academic adviser.
- The Terms and Privacy Policy both state plainly that AI output can be wrong.
- Whether conversations are stored: no — there's no database, see §3.
- Whether data is sent to an outside AI provider: yes, disclosed explicitly in the Privacy Policy (Ollama,
  local or cloud depending on config).
- No user data is used to train any model — nothing is retained past the single request/response cycle.

## 21. High-Risk Apps — N/A

This app doesn't touch medical/health data, financial accounts, credit decisions, government IDs, biometrics,
or any of the other high-risk categories listed. It's academic course planning.

## 22. Third-Party Services — ✓ Documented

| Service | Data sent | Why | Retention | Notes |
|---|---|---|---|---|
| Ollama (local or ollama.com cloud) | Chat prompt text + computed planning facts | Phrase the chat reply in natural language | Not retained by this app; Ollama's own retention policy applies for cloud mode | See Privacy Policy |
| GitHub Pages (planned) | None (static file hosting only) | Host the public landing page | N/A | No user data flows through it |
| Render/Fly.io (planned, if deployed) | Standard web server access logs | Run the backend | Platform-dependent | To be confirmed at deploy time |

No analytics, no advertising, no payment processor, no authentication provider is integrated today.

## 23. Dark Patterns — ✓ Verified clean

Reviewed the actual UI: no hidden cancel buttons (nothing to cancel), no preselected paid upgrades (no
payments), no fake urgency/scarcity, no confusing consent flows (nothing requires consent yet). The demo
login page is explicitly and visibly labeled "DEMO — NOT A REAL LOGIN," which is the opposite of a dark
pattern.

## 24–25. App Permissions, App Store Info — N/A

This is a web app, not a native mobile app, and isn't published through Apple/Google's app stores.

## 26. Security Testing Before Launch — ✓ Partial, real testing done

Actually tested, not assumed:
- Confirmed `FLASK_DEBUG` defaults to off, so stack traces don't leak to users in error responses
  (`message = str(e) if FLASK_DEBUG else "Internal server error."`).
- Grepped for SQL injection surface — none exists, because there's no SQL database at all.
- Grepped for command injection risk (`os.system`, `subprocess`, `eval`, `exec`) in request-handling code —
  none found.
- CORS is already restricted via an explicit allow-list (`CORS_ORIGINS` env var), not left wide open.
- Tested the live Ollama Cloud integration end-to-end with real requests (see the Ollama testing notes in
  git history) rather than assuming it "should work."

**Not done:** a full penetration-test pass (XSS injection attempts into the chat textbox, spam/rate-limit
abuse testing, dependency vulnerability scanning with a tool like `pip-audit`/`npm audit`). Worth doing
before any real public launch — tracked in the backlog.

## 27. Incident / Data Breach Plan — ✓ Minimal plan, appropriate to current scope

Given there's no database and no accounts, the realistic incident surface today is narrow: a leaked
`OLLAMA_API_KEY`. That's handled by revoking/rotating it at `ollama.com/settings/keys` and issuing a new one
via the hosting platform's environment variable settings — no user data is at risk in that scenario because
none is stored. A fuller incident-response plan (audit logs, access review, breach-notification obligations)
becomes relevant once real accounts/a database exist — tracked in the backlog.

## 28. Business Information — N/A for now

This is a student project, not an incorporated business. No LLC/business registration, tax ID, or sales-tax
question applies today. This becomes a real decision only if the project moves toward being a commercial
product — that's Aarush's (and the team's) call to make with real advisors, not something to resolve here.

---

## Minimum Pre-Launch Checklist — where this stands

| Item | Status |
|---|---|
| Privacy Policy | ✅ Done (draft, needs attorney review before treating as final) |
| Terms of Service | ✅ Done (draft, needs attorney review before treating as final) |
| Correct privacy disclosures | ✅ Grounded in a real audit, not boilerplate |
| No exposed API keys | ✅ Verified clean, plus two real gaps fixed (`.gitignore`, tracked `.venv`) |
| Secure authentication | N/A — none exists yet |
| Secure database permissions | N/A — no database exists yet |
| HTTPS | ⚠ Depends on hosting choice at deploy time (free options all provide it) |
| Account/data deletion procedure | N/A — no accounts exist yet |
| Properly licensed code/images/fonts/assets | ✅ Verified clean |
| Third-party dependency audit | ✅ Done, zero copyleft licenses found |
| Basic accessibility | ✓ Partial — labels/aria verified, full WCAG audit still pending |
| Age policy | ✅ Stated in Privacy Policy |
| Payment/subscription disclosures | N/A |
| Cookie/tracking review | N/A — none exist |
| AI-data disclosure | ✅ Done |
| User-content rules | N/A — no user-posted content |
| Security testing | ✓ Partial — core checks done, full pen-test pass still pending |
| Incident-response plan | ✓ Minimal plan appropriate to current scope |

## Biggest Red Flags — checked against this app specifically

| Red flag | Status |
|---|---|
| API keys inside frontend code | ✓ Verified absent — Ollama call is entirely server-side |
| Database set to public read/write | N/A — no database |
| No Row Level Security | N/A — no database |
| Admin access checked only by frontend | N/A — no admin surface exists |
| Plain-text passwords | N/A — no passwords exist |
| Generic AI-generated Privacy Policy that doesn't match the app | ✅ Fixed — the new one is grounded in a real audit |
| No Terms of Service | ✅ Fixed |
| No account deletion | N/A — no accounts |
| User data sent to AI without disclosure | ✅ Fixed — now disclosed |
| Analytics/tracking added without realizing it | ✓ Verified none exists |
| Unknown-license packages | ✓ Verified clean, zero copyleft |
| Copyrighted images copied from Google | ✓ Verified — no images used at all |
| User IDs that can be changed to view another account | N/A — no accounts, no per-user data at all |
| Payment success determined by frontend | N/A — no payments |
| Sensitive info in logs | ✓ Verified — only standard access logs, no request bodies logged |
| Production using test/debug credentials | ✓ Verified — `FLASK_DEBUG` defaults off |

---

*See `docs/COMPLIANCE_BACKLOG.md` for every item deferred here as a future trigger, and for every other
open item raised earlier in this project (data sources, feature scope, infrastructure decisions) that isn't
part of this specific audit but is still worth tracking.*
