# Data Governance & Security Posture

Internal reference document — not the public-facing Privacy Policy
(`Frontend/src/pages/privacy-page`), which summarizes this for a student
audience. This is the detailed version: what's actually stored, where,
why, for how long, who can reach it, and what hasn't been done yet.
Written for our own use first, and as the starting point for any future
formal security/FERPA review — a reviewer's first question is always
"show me you already know your own data," and this is that answer.

Last reviewed: 2026-09-02 (added migrations 0010/0011 — RPC edge-case
hardening, plus real seat/waitlist enrollment, "take together" groups, and
opt-in classmate LinkedIn sharing). Re-review after any schema change (new
migration) or before presenting this project to any outside institution.

## 1. System overview

- **Frontend**: Angular SPA, static-hosted (GitHub Pages). No server-side
  rendering, no server-side session of its own.
- **Backend**: Flask API (Render). Stateless per request — no database of
  its own, no user accounts, no persistent storage. See §4 for exactly
  what passes through it.
- **Database/Auth**: Supabase (Postgres + Auth), accessed **directly from
  the browser** via the public `anon` key. Row Level Security (RLS) is
  the only access-control layer for this data — there is no backend proxy
  narrowing what the anon key can reach. Every table below is protected
  by RLS policies, not by network topology.
- **LLM**: Ollama (self-hosted or Ollama Cloud, backend-only). Never
  receives account information, because the backend never has any to
  send — see §4.

## 2. Data inventory (by table)

For each table: what it holds, whether it's education-record-adjacent
data, who can read/write it, retention, and how a row is removed.

| Table | Holds | Sensitivity | Who can read | Who can write | Retention | Deletion path |
|---|---|---|---|---|---|---|
| `auth.users` (Supabase-managed) | email, hashed password, session tokens | **PII** | Supabase platform only; app never reads this table directly | Supabase Auth SDK only | Until account deleted | Self-serve: `delete_my_account()` RPC (migration 0009). Cascades to `advisor_profiles`/`student_plans`; nulls `meeting_proposals.advisor_id`, `security_events.actor_id`, `advisor_invite_codes.used_by` |
| `advisor_profiles` | display name, linked to `auth.users.id` | **PII** (name) | Row owner (self); any `is_advisor()`-verified advisor can see other advisors' display names via joins in `plan_comments` checks, not a direct listing | Only via `claim_advisor_profile()` RPC (invite-code gated, migration 0006) | Until account deleted | Cascades on `auth.users` deletion |
| `advisor_invite_codes` | a code string, which account claimed it, when | Low — no student data | Nobody via the API (no SELECT grant to anon/authenticated at all) | Only via `claim_advisor_profile()` RPC | Indefinite (small table, no student data) | `used_by` nulled on account deletion; code row itself persists (see migration 0006 comment on why direct listing is blocked) |
| `review_requests` | a full `plan_state` snapshot (major, minors, completed courses, timeline) + an optional self-chosen label | **Education record data** (this is the core "student academic plan" the whole app is about) | Anyone holding the request's own id (link-is-the-key model, see migration 0001); any `is_advisor()`-verified advisor can list all pending requests | Anyone, via `create_review_request()` RPC (no account needed) | Until the student or advisor asks us to delete it (currently: contact us — no self-serve delete on this table yet, see §5 gaps) | Manual, via Supabase SQL editor |
| `plan_comments` | comment text, `author_role`, `author_name` (denormalized, not a live FK) | Contains student-authored + advisor-authored text about a specific plan | Same access model as `review_requests` (its parent) | Scoped INSERT policy (migration 0006: students always allowed as `'You'`; advisors only as their own verified display name) | Tied to parent `review_requests` row; deleted via `on delete cascade` when the request is | Cascades with `review_requests` |
| `meeting_proposals` | a proposed meeting time, optional note, status | Low-moderate — scheduling metadata, tied to a review request | Same access model as `review_requests` | Advisors only (`is_advisor()`-gated INSERT); status changes via `respond_to_meeting_proposal()` RPC | Tied to parent `review_requests`; `advisor_id` nulled (not cascaded) if the advisor later deletes their account (migration 0009) | Cascades with `review_requests`; `advisor_id` independently nulled on advisor deletion |
| `student_plans` | one or more named `plan_state` snapshots per student, `user_id`-scoped | **Education record data**, same class as `review_requests` but tied to a real account instead of a bare link | Row owner only (`auth.uid() = user_id`, RLS) | Row owner only | Until the student deletes the plan or the account | Self-serve: per-plan via "My plans" delete (Your Plan page); whole account via `delete_my_account()` |
| `course_ratings` | course code, 1-5 rating, optional review text | **Not personal** — no account link, no name, ever (by design) | Public (anon key), by design — this is meant to be crowd-sourced and readable | Public INSERT, bounded (rating 1-5, review ≤2000 chars, migration 0004) | Indefinite | No delete grant to anon/authenticated (matches "nothing here identifies a submitter" design — see maintenance script for the one-off exception process) |
| `security_events` | event type, `actor_id` (nullable), a `detail` jsonb blob | Audit/log data, not itself student academic data | Nobody via the API (no SELECT grant at all) — SQL editor only | Only via the RPCs that log to it (migration 0007) | Indefinite (this is the audit trail; it's supposed to persist) | `actor_id` nulled on account deletion (migration 0009); event rows are not themselves deletable via the API |
| `course_seat_pools` | course code, capacity, seats taken — an aggregate count only | **Not personal** — no student identity attached at all | Public (anon key), by design — same reasoning as `course_ratings` | Only via `claim_course_seat()`/`drop_course_seat()` RPCs (migration 0011) | Indefinite | No delete path (nothing personal to delete) |
| `course_enrollments` | which student applied for which course, enrolled-vs-waitlisted | **Education record data** — this is a real, persisted "is this student taking this course" fact | Row owner only (`auth.uid() = student_id`, RLS) — never a classmate, never a group member (see migration 0011's own comment for why `get_group_status`/`get_classmate_linkedins` exist as narrow RPC exceptions rather than loosening this) | Only via `claim_course_seat()`/`drop_course_seat()` RPCs | Until the student drops or the account is deleted | Self-serve: "Drop this seat"/"Leave the waitlist" in the Weekly Schedule; cascades on account deletion (a trigger also re-releases the seat/promotes the next waitlisted student in that case, not just a plain cascade — see `release_freed_course_seat()`) |
| `course_groups` / `course_group_members` | an invite-code-gated "taking this together" group per course, and its membership | Low-moderate — no name/identity beyond an opaque `auth.users.id`; membership visible only to fellow members, and only as an anonymous count (see migration 0011) | Members of that specific group only (RLS: `course_group_members` is self-referential — visible only to a fellow member of the same `group_id`) | Only via `create_course_group()`/`join_course_group()`/`leave_course_group()` RPCs | Until members leave; the group row itself persists (orphaned, unreachable once empty) even after its creator's account is deleted (`created_by` nulled, not cascaded — matches `meeting_proposals.advisor_id`'s reasoning: other members' membership shouldn't vanish because the creator left) | Self-serve: "Leave group" in the Weekly Schedule; `course_group_members` rows cascade on account deletion |
| `student_profiles` | an optional LinkedIn URL + an opt-in "visible to classmates" flag | **PII, opt-in only** — the one piece of student identity this app exposes to other students at all, and only when the student explicitly turns it on (default off) | Row owner always; another student only if (a) the owner opted in AND (b) they share a real enrolled course — enforced by `get_classmate_linkedins()`, a narrow RPC exception to the same "no cross-student RLS" rule `course_enrollments` follows, never a direct table policy (see migration 0011's comment on why) | Row owner only | Until the student changes/clears it or the account is deleted | Self-serve: clear the URL or the visibility checkbox in the account menu; cascades on account deletion |

## 3. What the Flask backend actually sees (and doesn't persist)

- `/api/plan`, `/api/explore-majors`: the request body (prompt text, a
  `completed` course-code list, campus/major/minor selections, planning
  preferences). Used to compute a response and **never written to any
  database** — Flask has no database connection at all. Access-logged
  (method/path/status/duration only, see `Backend/app.py`'s
  `after_request` hook) — never the request body itself.
- `/api/parse-transcript`: an uploaded PDF, parsed **in memory**
  (`BytesIO`), never written to disk, discarded at the end of the
  request.
- Ollama (LLM phrasing step): receives the student's typed message and
  the deterministically-computed facts for that one request. No account
  information is ever included, because Flask never has any to include.

## 4. Known gaps (honest, not glossed over)

This is the section a real reviewer will actually care about most —
listing what's *not* done yet is what makes the rest of this document
credible.

- **No formal third-party security review or penetration test.** Nothing
  in this document substitutes for one.
- **No self-serve deletion for `review_requests`/`plan_comments`.** These
  are reachable by anyone holding the link (by design — no account is
  required to create one), which also means there's no "owner" account to
  authorize a self-serve delete against. Deletion today is manual, via
  the Supabase SQL editor, on request.
- **No automated data-retention expiry.** An abandoned `review_requests`
  row (a student who created one and never returned) is kept indefinitely
  today, not auto-expired after N months of inactivity.
- **No incident response plan beyond "the team fixes it."** No formal
  breach-notification procedure, no defined severity levels, no named
  on-call.
- **No accessibility audit** (WCAG 2.1 AA / Section 508) has been run
  against this app yet.
- **Hosting is not institution-grade.** Render's free/hobby tier and a
  single-project Supabase instance — no uptime SLA, no dedicated
  monitoring/alerting beyond what those platforms provide by default.
- **No signed data processing agreement exists with any institution.**
  This document is preparation for that conversation, not a substitute
  for it.

## 5. Deletion procedure (for the team, when a manual request comes in)

For anything covered by self-serve deletion (§2), point the requester at
the in-app control — it's both faster and leaves less room for the team
to make a mistake by hand. For `review_requests`/`plan_comments` (no
self-serve path yet):

```sql
-- Given the review request's id (from its share link or the requester's email):
delete from review_requests where id = '<uuid>';
-- plan_comments and meeting_proposals for it cascade automatically.
```

See `supabase/maintenance/cleanup_qa_test_data.sql` for the established
pattern this project already uses for one-off, human-run deletions.
