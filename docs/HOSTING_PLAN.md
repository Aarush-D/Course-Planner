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
