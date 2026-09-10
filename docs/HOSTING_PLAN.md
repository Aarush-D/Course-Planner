# Hosting Plan — Cheapest Path to a Live, Public App

Goal: get the real app (not just the landing page) live at a public URL, at **$0/month**, using what's
already built this session (gunicorn + Procfile, Ollama Cloud support). Verified against each platform's
actual current terms as of 2026-08-21, not assumed — free tiers change often and several popular options
have quietly stopped being free.

## What's already free and already built

| Piece | Where | Cost | Status |
|---|---|---|---|
| Landing page | GitHub Pages, `docs/index.html` | $0, forever | Built, committed, not yet enabled (Settings → Pages) |
| Backend WSGI server | `Backend/Procfile` (gunicorn) | — | Built, tested live |
| LLM phrasing | Ollama Cloud (`OLLAMA_API_KEY`) | $0 on Free plan | Wired up, tested live end-to-end |

## The two platforms that looked free but currently aren't

Checked directly before recommending anything, since this changes often:
- **Fly.io** — no free tier for new accounts since October 2024. New signups get a 2-VM-hour/7-day trial,
  then pay per resource. Ruled out.
- **Railway** — a one-time $5 trial credit, then $1/month minimum to keep anything running. Not a genuine
  ongoing free tier anymore. Ruled out.

## The recommended path: Render (backend) + GitHub Pages (frontend + landing page)

**Render's free tier**, confirmed current: 750 free instance-hours per workspace per month (more than a
month's worth of hours for one always-on-ish service), Python/Flask supported natively, connects directly to
a GitHub repo. The real tradeoff, and it matters for your specific use case: **a free instance spins down
after 15 minutes with no traffic, and the next request wakes it up with a 30-60 second cold start.**

**What that means concretely for a showcase:** if nobody's used the app in the last 15 minutes and a
recruiter is the first to click your link, they wait up to a minute before anything loads. Two ways to
manage this, in order of preference:
1. **Just know it and plan around it** — open the app yourself a few minutes before anyone's likely to look,
   so it's already warm. Free, zero setup.
2. A scheduled "ping" every 10-14 minutes to keep it awake defeats the purpose of a free tier's idle-sleep
   design and isn't something to rely on — skip this rather than fight the platform.

### Steps

1. **Push the `main` branch** (everything from this session is committed locally, not yet pushed — needs
   your explicit go-ahead).
2. **Render**: sign up, "New Web Service," connect the GitHub repo, set root directory to `Backend`. Render
   auto-detects the `Procfile` and `requirements.txt`.
3. **Set environment variables** in Render's dashboard (never in code): `OLLAMA_API_KEY`, `USE_OLLAMA=1`,
   `CORS_ORIGINS=<your GitHub Pages URL>`, `FLASK_DEBUG=0`. Rotate the Ollama key first (see
   `docs/COMPLIANCE_AUDIT.md` §4) and use the new one here.
4. **GitHub Pages**: Settings → Pages → Deploy from branch → `main` → `/docs`. This serves the static landing
   page at `aarush-d.github.io/Course-Planner` immediately.
5. **The live Angular app itself** needs its own static build deployed somewhere pointed at the Render
   backend's URL (update `Frontend/proxy.conf.json`'s dev-only proxy target isn't used in production — the
   built app needs an actual API base URL configured, and CORS on the Render side needs to allow that
   origin). This is a small, contained follow-up once you're ready — not done in this pass since it means
   picking exactly where the built Angular app itself will live (GitHub Pages can serve it too, alongside or
   instead of the landing page, or a separate free static host like Netlify/Vercel's free tiers).

## Total cost: $0/month

With the explicit tradeoffs stated above (cold starts, 1-concurrent-generation LLM limit on Ollama's free
plan). Both are real, known constraints — not hidden gotchas — and both have a clear, cheap upgrade path
(Render paid tier removes cold starts; Ollama Pro raises concurrency to 3) if this ever needs to feel more
production-grade without changing any code.

## Production readiness (2026-09)

The app is now actually deployed (Render backend, GitHub Pages frontend) and being asked to handle real,
active users and showcasing, still at $0/month. What changed and what's still an accepted limit, verified
against each platform's current 2026 terms rather than assumed:

**Cold starts are real and not fixed.** Still true, per the section above — nothing free eliminates a
15-minutes-idle spin-down on Render's free tier. What's new: the frontend now shows an explicit "Waking up
the server — this can take up to a minute on our free hosting tier" banner (see
`PlannerStateService.init()`/`wakingUp` and `AppComponent`'s template) if that very first request of a
session takes longer than ~3.5s, so a cold visit reads as intentional instead of broken. A scheduled
keep-alive ping was considered again and rejected again, for a sharper reason than before: Render's free
tier is 750 instance-hours **per workspace**, per month. A ping every few minutes, 24/7, burns roughly
730 of those 750 hours just idling — leaving almost no headroom for real traffic before the *entire
workspace* gets suspended for the rest of the month. Trading an intermittent ~30-60s cold start for a risk
of total outage is a worse deal, not a better one. Don't re-propose it.

**LLM concurrency: Groq replaces Ollama Cloud's 1-request ceiling.** Ollama Cloud's free tier caps out at 1
concurrent generation — fine for a solo demo, not for more than one person chatting at once. Groq's free
tier (`console.groq.com`, no credit card) has no such ceiling — roughly 30 requests/minute and up to 14,400
requests/day depending on model, OpenAI-compatible `/v1/chat/completions` endpoint. `Backend/app.py` now
has a `groq_chat()` function alongside `ollama_chat()`, dispatched through `llm_chat()`: Groq is used when
`GROQ_API_KEY` is set, otherwise behavior is unchanged (Ollama Cloud if `OLLAMA_API_KEY` is set, else local
Ollama). The deterministic planning engine is still the sole source of truth either way — the LLM is
phrasing-only, and a failed/slow/ungrounded LLM reply already falls back to the plain deterministic text
(see `_phrased_reply_stays_grounded`), so this swap doesn't touch that safety net.

**Rate limiting is accurate but not high-throughput.** Unchanged from the last sweep: the in-memory limiter
is correct for Render's single-worker Procfile, but doesn't hold up across multiple processes/instances.
The `RATE_LIMIT_STORAGE_URI`/Redis path exists and is ready to switch on the moment a Redis add-on is worth
paying for — not needed yet at current traffic.

**Error monitoring**: `Backend/app.py` now initializes Sentry (`sentry-sdk[flask]`, error capture only —
`traces_sample_rate=0`, `send_default_pii=False`) when `SENTRY_DSN` is set, so a production crash actually
surfaces somewhere instead of vanishing into Render's ephemeral log stream. A no-op with it unset.

**To turn these on**, set two environment variables in Render's dashboard (never in code, never committed):
- `GROQ_API_KEY` — free account at [console.groq.com](https://console.groq.com), generate an API key.
- `SENTRY_DSN` — free Developer-plan account at [sentry.io](https://sentry.io), create a Flask project, copy
  its DSN.

Both are additive and independently optional — the app runs exactly as it does today with either or both
unset.
