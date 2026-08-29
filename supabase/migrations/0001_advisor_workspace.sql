-- Two-way advisor workspace: review requests, comments, meeting proposals.
--
-- Run this once in the Supabase project's SQL Editor (Dashboard -> SQL
-- Editor -> New query -> paste -> Run). Kept here as a real file (not just
-- "the thing that exists in the dashboard") so the schema is reproducible
-- and reviewable.
--
-- Trust model, spelled out once since every policy below follows from it:
--   - Students have no accounts (matches the rest of this app). Holding a
--     review request's id IS the authorization to read/act on it -- same
--     model as the app's existing `?shared=` read-only link, just backed
--     by a real row instead of a client-encoded token.
--   - Because Postgres RLS can't distinguish "fetch the one row you already
--     know the id of" from "list every row", anonymous reads of a single
--     request/its comments/its meeting proposals go through SECURITY
--     DEFINER functions that take an id and return only that id's data --
--     structurally impossible to use for enumeration, regardless of RLS.
--   - Advisors have real Supabase Auth accounts and get normal RLS-scoped
--     table access (e.g. "list every pending request") for their dashboard.

-- ── advisor_profiles ─────────────────────────────────────────────────────
-- One row per advisor, created right after signup. Real credentials
-- (password, etc.) live in Supabase's own auth.users table, never here.
create table if not exists advisor_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null,
  created_at timestamptz not null default now()
);

alter table advisor_profiles enable row level security;

create policy "advisors can insert their own profile"
  on advisor_profiles for insert
  to authenticated
  with check (auth.uid() = id);

create policy "advisors can read their own profile"
  on advisor_profiles for select
  to authenticated
  using (auth.uid() = id);

grant insert, select on advisor_profiles to authenticated;

-- ── review_requests ──────────────────────────────────────────────────────
-- plan_state mirrors the frontend's PlannerState shape exactly (see
-- Frontend/src/services/planner-state.service.ts) -- a snapshot at the
-- moment the student asked for review, not a live sync.
create table if not exists review_requests (
  id uuid primary key default gen_random_uuid(),
  plan_state jsonb not null,
  student_label text,
  status text not null default 'pending' check (status in ('pending', 'reviewed')),
  created_at timestamptz not null default now()
);

alter table review_requests enable row level security;

-- A student creates one without logging in -- same trust boundary as
-- generating the existing read-only share link.
create policy "anyone can create a review request"
  on review_requests for insert
  to anon, authenticated
  with check (true);

-- Advisor dashboard: list every pending request. Deliberately NOT open to
-- anon -- a student's own single-row read goes through get_review_request()
-- below instead, so the anon key can never list this table.
create policy "advisors can read all review requests"
  on review_requests for select
  to authenticated
  using (true);

create policy "advisors can update review requests"
  on review_requests for update
  to authenticated
  using (true)
  with check (true);

grant insert on review_requests to anon, authenticated;
grant select, update on review_requests to authenticated;

-- ── plan_comments ────────────────────────────────────────────────────────
-- author_role (not a nullable advisor_id) so a student's reply doesn't
-- need any account row to exist. author_name is denormalized at insert
-- time (the advisor's display name, or a plain "You" for student replies)
-- so an anonymous reader never needs to join into advisor_profiles, which
-- would otherwise need its own public-read policy just for this.
create table if not exists plan_comments (
  id uuid primary key default gen_random_uuid(),
  review_request_id uuid not null references review_requests(id) on delete cascade,
  author_role text not null check (author_role in ('advisor', 'student')),
  author_name text not null,
  body text not null,
  created_at timestamptz not null default now()
);

alter table plan_comments enable row level security;

-- A student can post as 'student' without logging in; only a real
-- authenticated advisor can post as 'advisor' -- stops an anonymous caller
-- from forging a comment that looks like it came from an advisor.
create policy "scoped comment inserts"
  on plan_comments for insert
  to anon, authenticated
  with check (
    author_role = 'student'
    or (author_role = 'advisor' and auth.role() = 'authenticated')
  );

-- Advisors list comments on any request via normal table access; an
-- anonymous student reads their own request's comments via
-- get_review_request_comments() below instead (same reasoning as
-- review_requests' own select policy).
create policy "advisors can read all comments"
  on plan_comments for select
  to authenticated
  using (true);

grant insert on plan_comments to anon, authenticated;
grant select on plan_comments to authenticated;

-- ── meeting_proposals ────────────────────────────────────────────────────
create table if not exists meeting_proposals (
  id uuid primary key default gen_random_uuid(),
  review_request_id uuid not null references review_requests(id) on delete cascade,
  advisor_id uuid not null references auth.users(id),
  proposed_at timestamptz not null,
  note text,
  status text not null default 'proposed' check (status in ('proposed', 'accepted', 'declined')),
  created_at timestamptz not null default now()
);

alter table meeting_proposals enable row level security;

create policy "advisors can propose meetings"
  on meeting_proposals for insert
  to authenticated
  with check (auth.role() = 'authenticated' and advisor_id = auth.uid());

create policy "advisors can read all meeting proposals"
  on meeting_proposals for select
  to authenticated
  using (true);

-- A student accepts/declines without logging in -- same link-is-the-key
-- trust boundary as everything else here. The row's own unguessable uuid
-- (reached only via a review request the student already holds) is what
-- stands in for authorization, same as the read paths below.
create policy "anyone can update a meeting proposal's status"
  on meeting_proposals for update
  to anon, authenticated
  using (true)
  with check (true);

grant insert, select on meeting_proposals to authenticated;
grant update on meeting_proposals to anon, authenticated;

-- ── RPCs for the anonymous single-request read path ─────────────────────
-- SECURITY DEFINER runs with the function owner's privileges (bypassing
-- RLS for its own query), but each function's WHERE clause is pinned to
-- the exact id argument -- there is no code path here that can return
-- more than one review request's worth of data, regardless of caller.

create or replace function get_review_request(request_id uuid)
returns review_requests
language sql
security definer
set search_path = public
as $$
  select * from review_requests where id = request_id;
$$;

create or replace function get_review_request_comments(request_id uuid)
returns setof plan_comments
language sql
security definer
set search_path = public
as $$
  select * from plan_comments where review_request_id = request_id order by created_at asc;
$$;

create or replace function get_review_request_meetings(request_id uuid)
returns setof meeting_proposals
language sql
security definer
set search_path = public
as $$
  select * from meeting_proposals where review_request_id = request_id order by created_at asc;
$$;

grant execute on function get_review_request(uuid) to anon, authenticated;
grant execute on function get_review_request_comments(uuid) to anon, authenticated;
grant execute on function get_review_request_meetings(uuid) to anon, authenticated;
