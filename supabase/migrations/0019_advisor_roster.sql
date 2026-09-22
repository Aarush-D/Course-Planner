-- Advisor roster: a standing advisor <-> student relationship, alongside
-- (not replacing) the existing one-off review_requests flow from
-- 0001_advisor_workspace.sql.
--
-- Run this once in the Supabase project's SQL Editor, same convention as
-- every migration before it (see 0001's own header comment).
--
-- Why a new pair of tables rather than widening review_requests/
-- plan_comments: those are deliberately anonymous-student-friendly (no
-- student_id column at all -- holding a request's id IS the
-- authorization, same trust model as this app's existing ?shared= link).
-- A roster relationship is the opposite shape: both sides always have a
-- real Supabase Auth account, and the relationship is durable (survives
-- across many plan edits and many advising conversations), not a single
-- snapshot. Trying to bolt that onto review_requests' shape would mean
-- widening a NOT NULL FK to nullable, adding a second nullable FK with a
-- "exactly one is set" check, and touching every existing RLS
-- policy/RPC on that table for a feature it was never shaped for -- a
-- new table is the smaller, clearer change.
--
-- The invite-code mechanism below deliberately mirrors
-- create_course_group/join_course_group (0011/0013/0015/0018) as closely
-- as possible: same entropy (16 hex chars via double gen_random_uuid()),
-- same "reusable code, no single-use guard -- entropy alone defends
-- against guessing" reasoning, same "return a rejection column instead
-- of raising on the audit-logged branch" shape (0018's fix to the exact
-- bug 0016 first found: a RAISE aborts the whole transaction, rolling
-- back the security_events INSERT that ran one statement earlier in the
-- same function body -- closed here from the start, not re-introduced).
--
-- Every new RLS policy below was checked against the exact shape that
-- caused 0015's "infinite recursion detected in policy" bug (a plain
-- declarative policy whose own USING clause queries the table it's
-- defined on) -- none of them do that; each either calls the
-- SECURITY DEFINER helper is_advisor() (which reads advisor_profiles,
-- a different table) or compares a bare column on the row being
-- evaluated. Every new function explicitly revokes EXECUTE from
-- public/anon after creation -- Postgres grants EXECUTE to PUBLIC by
-- default on every new function regardless of what it touches, which
-- 0014/0017/0018 each had to fix retroactively for an earlier function;
-- closed here proactively instead.

-- ═══════════════════════════════════════════════════════════════════════
-- advisor_profiles: one reusable, self-serve roster invite code
-- ═══════════════════════════════════════════════════════════════════════
-- One code per advisor (1:1) -- unlike course_groups.invite_code, which
-- needs its own table because a single student creates MANY groups (one
-- per course). An advisor has exactly one roster, so the code lives
-- directly on their existing profile row. Nullable until first requested
-- (get_or_create_roster_invite_code below); every existing advisor_profiles
-- row is untouched by this ALTER.
alter table advisor_profiles
  add column if not exists roster_invite_code text unique check (length(roster_invite_code) <= 32);

create or replace function get_or_create_roster_invite_code()
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_advisor uuid := (select auth.uid());
  v_code text;
begin
  if v_advisor is null or not is_advisor() then
    raise exception 'must be an advisor';
  end if;

  select roster_invite_code into v_code from advisor_profiles where id = v_advisor;
  if v_code is not null then
    return v_code;
  end if;

  -- Same entropy as create_course_group's own code: 16 hex chars (~64
  -- bits) from two concatenated gen_random_uuid() md5 sums. Reusable by
  -- design (every advisee who has it can join, not just the first), so
  -- entropy alone is the defense against a guessing/enumeration attempt.
  v_code := substr(md5(gen_random_uuid()::text) || md5(gen_random_uuid()::text), 1, 16);
  update advisor_profiles set roster_invite_code = v_code where id = v_advisor;
  return v_code;
end;
$$;

revoke execute on function get_or_create_roster_invite_code() from public, anon;
grant execute on function get_or_create_roster_invite_code() to authenticated;

-- ── regenerate_roster_invite_code: advisor-initiated rotation ───────────
-- Not present in course_groups (a group's code is tied to that one group
-- forever, no reason to rotate it). Added here because a single
-- advisor-wide code is a bigger blast radius if it leaks -- every past
-- and future advisee, not one course's worth of classmates -- so the
-- advisor gets an explicit "get a new link" escape hatch. The old code
-- simply stops matching anything the moment this runs; already-rostered
-- students are completely unaffected (advisor_rosters rows never
-- reference the code itself, only the fact that it was once redeemed).
create or replace function regenerate_roster_invite_code()
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_advisor uuid := (select auth.uid());
  v_code text;
begin
  if v_advisor is null or not is_advisor() then
    raise exception 'must be an advisor';
  end if;

  v_code := substr(md5(gen_random_uuid()::text) || md5(gen_random_uuid()::text), 1, 16);
  update advisor_profiles set roster_invite_code = v_code where id = v_advisor;
  return v_code;
end;
$$;

revoke execute on function regenerate_roster_invite_code() from public, anon;
grant execute on function regenerate_roster_invite_code() to authenticated;

-- ═══════════════════════════════════════════════════════════════════════
-- advisor_rosters: the standing relationship itself
-- ═══════════════════════════════════════════════════════════════════════
-- advisor_id references advisor_profiles(id), not auth.users(id) --
-- makes "a roster row whose advisor isn't actually a vetted advisor"
-- structurally impossible, not just app-logic-impossible (every insert
-- path below only ever resolves the advisor via an advisor_profiles
-- lookup). Both sides cascade on delete -- a roster row has no meaning
-- once either party's account is gone (unlike meeting_proposals, a
-- historical record 0009 deliberately preserves via SET NULL).
create table if not exists advisor_rosters (
  advisor_id uuid not null references advisor_profiles(id) on delete cascade,
  student_id uuid not null references auth.users(id) on delete cascade,
  -- Free text, typed by the student at join time -- same convention as
  -- review_requests.student_label (0001): there is no queryable student
  -- display name anywhere in this app (first/last name lives only in
  -- Supabase Auth's own user_metadata, never exposed via any table/RPC).
  -- Left nullable rather than coalesced to a placeholder here, so the
  -- frontend falls back to "A student" the same way
  -- advisor-dashboard-page's existing template already does for
  -- review_requests.student_label.
  student_label text check (student_label is null or length(student_label) <= 200),
  joined_at timestamptz not null default now(),
  primary key (advisor_id, student_id)
);

-- Serves a student's "who's advising me" lookup (list_my_advisors below
-- filters on student_id alone -- the composite PK's leading column is
-- advisor_id, so it doesn't serve this) and the auth.users(id) ON DELETE
-- CASCADE scan when a student's account is deleted -- same reasoning as
-- 0018 FIX 7's five bare-FK indexes.
create index if not exists advisor_rosters_student_id_idx on advisor_rosters (student_id);

alter table advisor_rosters enable row level security;

-- Neither policy below queries advisor_rosters itself in its own USING
-- clause (is_advisor() reads advisor_profiles, a different table; the
-- other is a bare column comparison on the row being evaluated) -- so,
-- unlike course_group_members' original policy (0011, recursion fixed in
-- 0015), there is no self-reference here and no SECURITY DEFINER helper
-- is needed for these two.
create policy "advisors can read their own roster"
  on advisor_rosters for select
  to authenticated
  using (is_advisor() and advisor_id = (select auth.uid()));

create policy "students can read their own roster assignments"
  on advisor_rosters for select
  to authenticated
  using (student_id = (select auth.uid()));

grant select on advisor_rosters to authenticated;
-- No insert/update/delete grant at all -- every write goes through the
-- RPCs below, same "writes only through RPCs" shape as course_group_members.

-- ── join_advisor_roster: redeem a code, join that advisor's roster ──────
-- Mirrors join_course_group's CURRENT (post-0018) shape exactly: on an
-- invalid code, logs to security_events and RETURNS a rejection string
-- instead of raising, so that audit row actually commits.
create or replace function join_advisor_roster(p_invite_code text, p_student_label text default null)
returns table(advisor_id uuid, advisor_display_name text, rejection text)
language plpgsql
security definer
set search_path = public
as $$
#variable_conflict use_column
declare
  v_student uuid := (select auth.uid());
  v_advisor advisor_profiles%rowtype;
begin
  if v_student is null then
    raise exception 'must be signed in';
  end if;

  select * into v_advisor from advisor_profiles where roster_invite_code = p_invite_code;
  if not found then
    insert into security_events (event_type, actor_id, detail)
      values ('advisor_roster_join_rejected', v_student, jsonb_build_object('reason', 'invalid_invite_code'));
    return query select null::uuid, null::text, 'invalid invite code'::text;
    return;
  end if;

  -- Idempotent re-join (on conflict do nothing, same as
  -- join_course_group) -- a student re-pasting a link they already used
  -- just lands back on the roster, not an error. Does NOT update
  -- student_label on a re-join if one is already stored (matches
  -- join_course_group's own all-or-nothing insert).
  insert into advisor_rosters (advisor_id, student_id, student_label)
    values (v_advisor.id, v_student, nullif(trim(left(coalesce(p_student_label, ''), 200)), ''))
    on conflict (advisor_id, student_id) do nothing;

  return query select v_advisor.id, v_advisor.display_name, null::text;
end;
$$;

revoke execute on function join_advisor_roster(text, text) from public, anon;
grant execute on function join_advisor_roster(text, text) to authenticated;

-- ── leave_advisor_roster: student-initiated ──────────────────────────────
-- Mirrors leave_course_group exactly: a plain scoped delete, no error on
-- a no-op (leaving a roster you're not on is harmless).
create or replace function leave_advisor_roster(p_advisor_id uuid)
returns void
language sql
security definer
set search_path = public
as $$
  delete from advisor_rosters where advisor_id = p_advisor_id and student_id = (select auth.uid());
$$;

revoke execute on function leave_advisor_roster(uuid) from public, anon;
grant execute on function leave_advisor_roster(uuid) to authenticated;

-- ── remove_advisee_from_roster: advisor-initiated equivalent ────────────
create or replace function remove_advisee_from_roster(p_student_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not is_advisor() then
    raise exception 'must be an advisor';
  end if;
  delete from advisor_rosters where advisor_id = (select auth.uid()) and student_id = p_student_id;
end;
$$;

revoke execute on function remove_advisee_from_roster(uuid) from public, anon;
grant execute on function remove_advisee_from_roster(uuid) to authenticated;

-- ── list_my_advisors: student-side cross-role read ───────────────────────
-- advisor_profiles' own SELECT policy is (and must stay) "auth.uid() =
-- id" only -- widening it so any student could read any advisor's row
-- would undo real hardening (0006) for the sake of one screen. Narrow
-- SECURITY DEFINER RPC instead, same idiom as get_classmate_linkedins/
-- get_review_request: returns only (advisor_id, display_name, joined_at)
-- for the CALLER's own roster rows, nothing else from advisor_profiles.
create or replace function list_my_advisors()
returns table(advisor_id uuid, display_name text, joined_at timestamptz)
language sql
stable
security definer
set search_path = public
as $$
  select ap.id, ap.display_name, ar.joined_at
  from advisor_rosters ar
  join advisor_profiles ap on ap.id = ar.advisor_id
  where ar.student_id = (select auth.uid())
  order by ar.joined_at desc;
$$;

revoke execute on function list_my_advisors() from public, anon;
grant execute on function list_my_advisors() to authenticated;

-- ── get_advisee_plans: advisor reads a rostered student's LIVE plans ────
-- student_plans' own RLS ("auth.uid() = user_id", 0005/0008) is never
-- touched -- this RPC is the narrow, audited exception, gated by BOTH
-- is_advisor() and a real advisor_rosters row for the caller, same shape
-- every cross-role read in this schema already uses (get_review_request,
-- get_classmate_linkedins, etc.). Returns zero rows (not an error) for a
-- non-advisor caller or a student not on the caller's roster -- there is
-- no way to distinguish "no such student" from "not your advisee" from
-- the response, which is the point (this must not become a student-
-- existence oracle for a non-advisor or an unrelated advisor).
create or replace function get_advisee_plans(p_student_id uuid)
returns setof student_plans
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if not is_advisor() then
    return;
  end if;

  if not exists (
    select 1 from advisor_rosters
    where advisor_id = (select auth.uid()) and student_id = p_student_id
  ) then
    return;
  end if;

  return query select * from student_plans where user_id = p_student_id order by updated_at desc;
end;
$$;

revoke execute on function get_advisee_plans(uuid) from public, anon;
grant execute on function get_advisee_plans(uuid) to authenticated;

-- ═══════════════════════════════════════════════════════════════════════
-- advisee_comments: an ongoing thread per (advisor, student) pair
-- ═══════════════════════════════════════════════════════════════════════
-- Deliberately NOT a repurposed plan_comments -- see this file's own
-- header comment for why. Both sides here always have a real Supabase
-- Auth session (unlike review_requests' anonymous student), which is
-- exactly what lets this table use plain RLS instead of the
-- get_review_request_comments-style anonymous-read RPC wrapper.
create table if not exists advisee_comments (
  id uuid primary key default gen_random_uuid(),
  advisor_id uuid not null references advisor_profiles(id) on delete cascade,
  student_id uuid not null references auth.users(id) on delete cascade,
  -- This composite FK into advisor_rosters' own primary key is what
  -- enforces "a comment can only exist between two parties with a live
  -- roster relationship" at the database level, on every insert, for
  -- free -- and cascades the whole thread away automatically the moment
  -- either side ends the relationship (leave_advisor_roster /
  -- remove_advisee_from_roster / either account being deleted).
  foreign key (advisor_id, student_id) references advisor_rosters (advisor_id, student_id) on delete cascade,
  author_role text not null check (author_role in ('advisor', 'student')),
  -- Same convention as plan_comments (0001/0006): 'You' for a student
  -- post (no student display-name concept anywhere in this app), the
  -- advisor's real display_name for an advisor post -- enforced in the
  -- INSERT policy below exactly like plan_comments' own "scoped comment
  -- inserts" policy, so forging either is impossible.
  author_name text not null,
  body text not null check (length(body) between 1 and 4000),
  created_at timestamptz not null default now()
);

-- Serves the thread-read query (WHERE advisor_id = X AND student_id = Y
-- ORDER BY created_at), same shape as 0018 FIX 7's plan_comments index.
create index if not exists advisee_comments_thread_idx
  on advisee_comments (advisor_id, student_id, created_at);
-- Separate single-column index for the cascade-delete scan when a
-- student's auth.users row is deleted directly (student_id is not the
-- leading column of the composite index above, so it doesn't serve
-- that) -- same reasoning as 0018 FIX 7's five bare-FK indexes.
create index if not exists advisee_comments_student_id_idx on advisee_comments (student_id);

alter table advisee_comments enable row level security;

-- No same-table self-reference in either policy below (checked against
-- 0015's exact recursion shape) -- SELECT compares bare columns / calls
-- is_advisor(); INSERT's WITH CHECK does the same plus an
-- advisor_profiles lookup for the real display_name, never
-- advisee_comments itself.
create policy "roster participants can read their comment thread"
  on advisee_comments for select
  to authenticated
  using (
    (is_advisor() and advisor_id = (select auth.uid()))
    or student_id = (select auth.uid())
  );

-- The composite FK on the table already guarantees a row can't exist for
-- a pair that isn't actually rostered (a foreign-key violation rejects
-- the insert outright before this policy is even the deciding factor) --
-- this policy's own job is just the "who can post as which name" check,
-- same shape plan_comments' "scoped comment inserts" policy (0006) uses.
create policy "roster participants can post to their comment thread"
  on advisee_comments for insert
  to authenticated
  with check (
    length(body) between 1 and 4000
    and (
      (author_role = 'student' and student_id = (select auth.uid()) and author_name = 'You')
      or (
        author_role = 'advisor'
        and is_advisor()
        and advisor_id = (select auth.uid())
        and author_name = (select display_name from advisor_profiles where id = (select auth.uid()))
      )
    )
  );

grant select, insert on advisee_comments to authenticated;
