-- Database-layer follow-ups from a security/performance advisor pass plus
-- three bugs the 0015/0016 comments explicitly flagged for "a follow-up
-- migration." Eight independent fixes, grouped by what they touch; every
-- one was replayed against a throwaway local Postgres 16 first (migrations
-- 0001..0017 applied verbatim on top of a minimal auth.uid()/auth.role()/
-- auth.users stand-in and non-superuser anon/authenticated roles -- same
-- method as 0015's header; a superuser connection bypasses RLS and would
-- make the policy checks below meaningless). Each pre-fix bug was
-- reproduced on that instance before the fix was written, and each fix was
-- re-run against it afterward. The live Supabase project was only ever
-- READ during this work (pg_get_functiondef / pg_policies / pg_indexes /
-- pg_event_trigger / a SELECT calling get_review_request) -- nothing here
-- has been applied to it.
--
--   FIX 1 -- Audit rows still rolled back in three more RPCs (the exact bug
--            0016 fixed for claim_advisor_profile): respond_to_meeting_
--            proposal, create_review_request, join_course_group.
--   FIX 2 -- get_review_request returns an all-null row, not zero rows, for
--            an unknown id -- so the frontend's "no longer exists" branch
--            can never run.
--   FIX 3 -- rls_auto_enable() (a hand-created event-trigger function)
--            exists live but in no migration file, and is executable via
--            PostgREST by anon.
--   FIX 4 -- course_rating_summary view runs as its definer.
--   FIX 5 -- is_course_group_member(uuid, uuid) is a membership oracle for
--            any signed-in user.
--   FIX 6 -- 13 RLS policies re-evaluate auth.uid() per row.
--   FIX 7 -- No index on plan_comments/meeting_proposals(review_request_id)
--            or on the other auth.users foreign keys.
--
-- Deploy-ordering note for the frontend half of FIX 1/FIX 2 (the return-
-- type changes): the matching caller changes in Frontend/src/services/
-- review-request.service.ts and course-group.service.ts read the new row
-- shapes. Apply this migration BEFORE (or in the same deploy as) that
-- frontend -- the old frontend against the new schema throws
-- "Cannot read properties of null" on the nulled columns of a rejection
-- row, and the new frontend against the old schema gets a PostgREST 406 on
-- create_review_request (`.single()` on a scalar). get_review_request is the
-- one exception: its new caller is compatible with either function shape.

-- ═══════════════════════════════════════════════════════════════════════
-- FIX 1: three more RPCs whose rejection-path audit INSERT never commits
-- ═══════════════════════════════════════════════════════════════════════
-- 0016's header already explains the mechanism in full: each of these
-- functions does `insert into security_events (...)` and then, one
-- statement later in the same function body, `raise exception ...`. The
-- RPC is one top-level statement from PostgREST, so one transaction -- the
-- unhandled RAISE aborts it, and the audit INSERT that ran a statement
-- earlier is rolled back with everything else. The rejection branch is the
-- one branch each of these audit rows exists to record (brute-forcing
-- invite codes, replaying a settled meeting_id, probing the plan_state size
-- limit), and it is exactly the branch that never lands a row.
--
-- Reproduced on the local replay before touching anything: called each RPC
-- on its rejection path as the role the browser would use (anon for the
-- two review-request RPCs, authenticated for join_course_group), got the
-- expected error, then `select count(*) from security_events where
-- event_type = '<the rejected type>'` -- zero rows, all three.
--
-- Same fix as 0016, for the same reason 0016 gives (no autonomous-
-- transaction extension is installed; a plpgsql function cannot partially
-- commit): the function stops raising on the audit-logged rejection path
-- and instead returns a user-safe description of what happened, so its own
-- transaction -- audit INSERT included -- always commits. The caller in
-- the browser, entirely outside that transaction, reads the returned text
-- and throws there, after the row is durable. The "must be signed in" and
-- unique_violation raises stay as real errors: neither of those branches
-- writes an audit row, so there is nothing for a rollback to lose, and
-- both are genuinely exceptional rather than "expected rejection."
--
-- Each of these is a return-type change, which CREATE OR REPLACE refuses
-- -- drop first, then re-grant, since DROP FUNCTION discards the grants
-- along with the function (0016/0017 hit this same pair of gotchas; 0017
-- is specifically the "forgot that PUBLIC gets EXECUTE on a re-created
-- function" cleanup, so each re-grant below revokes PUBLIC explicitly
-- rather than leaving that for another follow-up).

-- ── 1a. respond_to_meeting_proposal: void -> text ────────────────────────
-- Null on success; a user-safe rejection message otherwise. The message
-- text is byte-for-byte what 0010's RAISE produced, so the student-facing
-- accept/decline flow (ReviewRequestService.setMeetingStatus, which now
-- throws `new Error(data)` on a non-null return) shows exactly what it
-- showed before -- only the audit row's durability changes.
drop function if exists respond_to_meeting_proposal(uuid, text);

create function respond_to_meeting_proposal(meeting_id uuid, new_status text)
returns text -- null on success; a user-safe rejection message otherwise
language plpgsql
security definer
set search_path = public
as $$
begin
  update meeting_proposals
  set status = new_status
  where id = meeting_id and status = 'proposed' and new_status in ('accepted', 'declined');

  if not found then
    insert into security_events (event_type, actor_id, detail)
    values (
      'meeting_response_rejected', auth.uid(),
      jsonb_build_object('meeting_id', meeting_id, 'attempted_status', new_status)
    );
    return 'That meeting proposal was already responded to, or no longer exists.';
  end if;

  insert into security_events (event_type, actor_id, detail)
  values ('meeting_responded', auth.uid(), jsonb_build_object('meeting_id', meeting_id, 'new_status', new_status));

  return null;
end;
$$;

-- anon is intentional here (0001's trust model: a student has no account;
-- holding the meeting's uuid is the authorization) -- this is one of the
-- few RPCs where the advisor's "executable by anon" finding is by design.
revoke execute on function respond_to_meeting_proposal(uuid, text) from public;
grant execute on function respond_to_meeting_proposal(uuid, text) to anon, authenticated;

-- ── 1b. create_review_request: uuid -> table(review_request_id, rejection)
-- This one can't become `returns text` -- its success value is the new
-- row's id, which the caller needs. So it takes the same shape 1c gives
-- join_course_group: a single row where exactly one of
-- (review_request_id, rejection) is non-null. ReviewRequestService.
-- createReviewRequest reads it with `.single()` and throws on `rejection`.
-- Rejection message unchanged from 0010.
drop function if exists create_review_request(jsonb, text);

create function create_review_request(plan_state jsonb, student_label text default null)
returns table(review_request_id uuid, rejection text)
language plpgsql
security definer
set search_path = public
as $$
declare
  new_id uuid;
begin
  if pg_column_size(plan_state) > 300000 then
    insert into security_events (event_type, actor_id, detail)
    values ('review_request_rejected', auth.uid(), jsonb_build_object('reason', 'plan_state_too_large'));
    return query select null::uuid, 'That plan is too large to submit for review.'::text;
    return;
  end if;

  insert into review_requests (plan_state, student_label)
  values (plan_state, left(student_label, 200))
  returning id into new_id;

  insert into security_events (event_type, actor_id, detail)
  values ('review_request_created', auth.uid(), jsonb_build_object('review_request_id', new_id));

  return query select new_id, null::text;
end;
$$;

-- anon intentional, same reasoning as 1a (0002 is where this RPC was made
-- anon-callable in the first place, and why).
revoke execute on function create_review_request(jsonb, text) from public;
grant execute on function create_review_request(jsonb, text) to anon, authenticated;

-- ── 1c. join_course_group: adds a `rejection text` OUT column ────────────
-- The three existing OUT columns keep their names (the frontend
-- destructures by exactly these names -- see 0015's comment on why
-- renaming them was ruled out), and `#variable_conflict use_column` stays
-- for the reason 0015 spells out: group_id/course_code/invite_code shadow
-- real column names in this function's own queries, and the ON CONFLICT
-- target can't be alias-qualified. `rejection` collides with nothing.
-- Message text unchanged from 0011/0013/0015 ('invalid invite code').
drop function if exists join_course_group(text);

create function join_course_group(p_invite_code text)
returns table(group_id uuid, course_code text, invite_code text, rejection text)
language plpgsql
security definer
set search_path = public
as $$
#variable_conflict use_column
declare
  v_student uuid := auth.uid();
  v_group course_groups%rowtype;
begin
  if v_student is null then
    raise exception 'must be signed in';
  end if;

  select * into v_group from course_groups where invite_code = p_invite_code;
  if not found then
    -- Logged for the same reason claim_advisor_profile/
    -- respond_to_meeting_proposal log their own failed-attempt branches
    -- (migrations 0006/0010) -- a brute-force guessing campaign against
    -- this invite-code space would otherwise be invisible to the one
    -- audit trail this app has. Returned (not raised) so the row survives.
    insert into security_events (event_type, actor_id, detail)
      values ('course_group_join_rejected', v_student, jsonb_build_object('reason', 'invalid_invite_code'));
    return query select null::uuid, null::text, null::text, 'invalid invite code'::text;
    return;
  end if;

  -- Idempotent if already a member of THIS group (on conflict does
  -- nothing); a friendly error if they're already in a DIFFERENT group for
  -- the same course_code (the unique(student_id, course_code) constraint).
  -- Still a real RAISE: no audit row precedes it, so nothing is lost.
  begin
    insert into course_group_members (group_id, student_id, course_code)
      values (v_group.id, v_student, v_group.course_code)
      on conflict (group_id, student_id) do nothing;
  exception when unique_violation then
    raise exception 'You''re already in a group for this course.';
  end;

  return query select v_group.id, v_group.course_code, v_group.invite_code, null::text;
end;
$$;

-- authenticated only -- the body raises 'must be signed in' for anon
-- anyway, and 0011/0013/0015 only ever granted authenticated; the PUBLIC
-- grant Postgres adds on creation was the only reason anon could reach it
-- (the advisor's anon_security_definer_function_executable finding for
-- this function).
revoke execute on function join_course_group(text) from public, anon;
grant execute on function join_course_group(text) to authenticated;

-- Verified on the local replay after the three rewrites: every rejection
-- path now returns its message (no error) AND leaves exactly one
-- security_events row of the matching event_type behind; every success
-- path returns null in the rejection slot and the same values it did
-- before; join_course_group's 'must be signed in' (anon) and 'You're
-- already in a group for this course.' (second group, same course) still
-- raise.

-- ═══════════════════════════════════════════════════════════════════════
-- FIX 2: get_review_request returns one all-null row for an unknown id
-- ═══════════════════════════════════════════════════════════════════════
-- 0001 declared it `returns review_requests` -- a single composite, not
-- `setof`. A non-SETOF SQL function whose final query returns zero rows
-- doesn't return zero rows to its caller; it returns one NULL of the
-- declared type, and PostgREST serializes a null composite as an object
-- with every column null: {"id":null,"plan_state":null,...}. Confirmed
-- live (read-only) with `select * from get_review_request('<random
-- uuid>')` against the real project: one row, every column null. On the
-- frontend that object is truthy, so both callers' `if (!request)` ->
-- "This review request no longer exists." branch is dead code; a deleted
-- or mistyped review id instead falls through to `request.plan_state` being
-- null, the backend /plan call throwing, and the generic "Couldn't load
-- this review request" message.
--
-- `setof` is what the sibling functions (get_review_request_comments,
-- get_review_request_meetings) already use, and it makes "no such row"
-- come back as a genuinely empty result. Return-type change, so drop +
-- re-create + re-grant, as above. ReviewRequestService.getReviewRequest
-- now calls it with `.maybeSingle()` and treats a missing/null `id` as
-- "not found" -- which is also correct against the OLD function shape
-- (an all-null object has a null id), so this pair has no deploy-order
-- constraint.
drop function if exists get_review_request(uuid);

create function get_review_request(request_id uuid)
returns setof review_requests
language sql
security definer
set search_path = public
as $$
  select * from review_requests where id = request_id;
$$;

-- anon intentional (0001: the anonymous single-request read path).
revoke execute on function get_review_request(uuid) from public;
grant execute on function get_review_request(uuid) to anon, authenticated;

-- ═══════════════════════════════════════════════════════════════════════
-- FIX 3: rls_auto_enable() exists live but in no migration
-- ═══════════════════════════════════════════════════════════════════════
-- The security advisor flags `public.rls_auto_enable()` as a SECURITY
-- DEFINER function executable by anon (and authenticated) via
-- /rest/v1/rpc/rls_auto_enable. It appears in none of 0001..0017.
-- Inspected read-only on the live project:
--
--   - pg_get_functiondef: the body reproduced verbatim below -- a
--     ddl_command_end event-trigger function that walks
--     pg_event_trigger_ddl_commands() and runs `alter table ... enable row
--     level security` on every new table in the public schema. i.e. the
--     common "never ship a table without RLS again" safety net.
--   - pg_event_trigger: bound as event trigger `ensure_rls` (tags CREATE
--     TABLE / CREATE TABLE AS / SELECT INTO), owner `postgres`. Every
--     other event trigger on the project (pgrst_ddl_watch, pg_net/pg_cron/
--     pg_graphql access grants) is owned by supabase_admin -- those are
--     platform-managed. `postgres` is the SQL-editor/dashboard role, so
--     this one was created by hand and never written down.
--   - proconfig: search_path=pg_catalog already set; proacl NULL, i.e.
--     Postgres's default of EXECUTE to PUBLIC -- hence the finding.
--
-- Not exploitable as such: calling an event-trigger function outside an
-- event fails with "trigger functions can only be called as triggers" --
-- the identical situation 0014 handled for the release_freed_course_seat
-- row trigger, and the same reasoning applies: a dead-on-arrival public
-- endpoint on a SECURITY DEFINER function is noise in the one list where
-- noise hides real findings. Revoked for the same reason.
--
-- Captured here so the schema file set is actually reproducible (0001's
-- own header: "kept here as a real file ... so the schema is reproducible
-- and reviewable"). Body is verbatim from pg_get_functiondef -- including
-- its own `set search_path = pg_catalog`, which is kept rather than
-- changed to '' because (a) pg_catalog is implicitly searched first
-- regardless, so the two are equivalent for a body that references only
-- pg_catalog objects, and (b) a verbatim body makes this CREATE OR REPLACE
-- a provable no-op on the live project. The CREATE EVENT TRIGGER is
-- guarded (it exists live; CREATE EVENT TRIGGER has no IF NOT EXISTS) and
-- tolerates insufficient_privilege so a fresh environment whose migration
-- role can't create event triggers still gets the function and the revoke.
create or replace function public.rls_auto_enable()
returns event_trigger
language plpgsql
security definer
set search_path = pg_catalog
as $$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$$;

revoke execute on function public.rls_auto_enable() from public, anon, authenticated;

do $$
begin
  if not exists (select 1 from pg_event_trigger where evtname = 'ensure_rls') then
    create event trigger ensure_rls
      on ddl_command_end
      when tag in ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      execute function public.rls_auto_enable();
  end if;
exception when insufficient_privilege then
  raise notice 'ensure_rls event trigger not created (insufficient privilege): %', sqlerrm;
end
$$;

-- Verified on the local replay: with the trigger created and EXECUTE
-- revoked as above, `create table` run as a plain non-superuser role still
-- comes out with relrowsecurity = true (event-trigger functions, like row-
-- trigger functions, aren't subject to the caller's EXECUTE grant), and a
-- direct `select rls_auto_enable()` as authenticated fails on the revoke
-- ("permission denied for function") before it could even reach the
-- "can only be called as triggers" error.

-- ═══════════════════════════════════════════════════════════════════════
-- FIX 4: course_rating_summary runs with the view owner's privileges
-- ═══════════════════════════════════════════════════════════════════════
-- The advisor's security_definer_view finding (level ERROR). A plain
-- `create view` in Postgres reads the underlying tables as the view's
-- owner -- here `postgres`, which bypasses RLS -- not as whoever queries
-- it. For this specific view that's harmless today (course_ratings is
-- deliberately public-readable with `using (true)`, 0004), but it means
-- the view would silently keep exposing everything if course_ratings'
-- policy were ever tightened -- exactly the kind of latent gap 0010 FIX 3
-- closed for a stale grant. security_invoker (Postgres 15+) makes the
-- view evaluate as the querying role, so the underlying table's own
-- grant + RLS are what decide. No behavior change for anon/authenticated,
-- who already hold SELECT on course_ratings.
alter view course_rating_summary set (security_invoker = true);

-- ═══════════════════════════════════════════════════════════════════════
-- FIX 5: is_course_group_member(uuid, uuid) is a membership oracle
-- ═══════════════════════════════════════════════════════════════════════
-- 0015's helper takes the user id as a parameter (`p_user_id`) and is
-- granted to authenticated -- necessarily, since the policy's USING clause
-- calls it as the querying role. But that same grant means any signed-in
-- student can call /rest/v1/rpc/is_course_group_member directly with ANY
-- (group_id, user_id) pair and get a true/false answer about whether some
-- other student is in some group -- a "does user X belong to group Y"
-- oracle, which is precisely the cross-student membership disclosure
-- course_group_members' RLS exists to prevent (0011: "membership is
-- visible only to fellow members of that same group, never globally").
-- Group ids are unguessable uuids, so this needs the attacker to already
-- hold one, which limits it -- but a member of a group can trivially
-- obtain their own group's id and then probe arbitrary user ids against
-- it, and user ids do leak (e.g. any advisor's id via meeting_proposals.
-- advisor_id on a review link the student holds).
--
-- Replaced with a single-argument version that resolves the user from
-- auth.uid() internally: a caller can now only ever ask "am *I* in group
-- Y," which is information they already have. `(select auth.uid())`
-- rather than a bare call for the same per-statement-vs-per-row reason
-- FIX 6 gives. STABLE is accurate (reads, never writes) and lets the
-- planner treat repeated calls within one statement as such.
--
-- Order matters: both policies that reference the old signature have to
-- be dropped before the function can be (pg_depend tracks policy
-- expressions -> functions; DROP FUNCTION otherwise fails with "cannot
-- drop ... because other objects depend on it"). They are re-created
-- against the new signature at the end of this block. course_groups'
-- "members can read their own groups" (0011:401) was still the inline
-- `exists (select 1 from course_group_members ...)` form -- correct (not
-- a self-reference, see 0015's note) but one of the 13 initplan findings
-- and now needlessly different from its sibling; both use the helper.
drop policy if exists "members can see fellow members of their own groups" on course_group_members;
drop policy if exists "members can read their own groups" on course_groups;
drop function if exists is_course_group_member(uuid, uuid);

create function is_course_group_member(p_group_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from course_group_members
    where group_id = p_group_id and student_id = (select auth.uid())
  );
$$;

-- Same grant shape 0015 explained: PUBLIC/anon revoked; authenticated
-- needs EXECUTE because the policy's USING clause calls this as the
-- querying role.
revoke execute on function is_course_group_member(uuid) from public, anon;
grant execute on function is_course_group_member(uuid) to authenticated;

create policy "members can see fellow members of their own groups"
  on course_group_members for select
  to authenticated
  using (is_course_group_member(course_group_members.group_id));

create policy "members can read their own groups"
  on course_groups for select
  to authenticated
  using (is_course_group_member(course_groups.id));

-- Verified on the local replay: as a member, both tables return exactly
-- the caller's own group / its roster; as a non-member of that group,
-- zero rows from both (isolation preserved, no recursion); and the old
-- two-argument signature no longer exists to be called.

-- ═══════════════════════════════════════════════════════════════════════
-- FIX 6: auth.uid() -> (select auth.uid()) in the remaining 11 policies
-- ═══════════════════════════════════════════════════════════════════════
-- The performance advisor's auth_rls_initplan finding, 13 policies. A bare
-- `auth.uid()` in a policy expression is re-evaluated for every candidate
-- row (it's a STABLE function call, so the planner treats it as a per-row
-- expression); wrapping it as `(select auth.uid())` turns it into an
-- InitPlan the executor evaluates once per statement and then compares
-- as a constant. Same logic, same result set, one call instead of N.
-- Two of the 13 (course_groups / course_group_members) are handled by
-- FIX 5 above -- their policies now contain no auth call at all. The
-- other 11 are rewritten here with DROP POLICY IF EXISTS + CREATE POLICY,
-- each expression otherwise identical to the version currently live
-- (which is 0006's for the two hardened policies, not 0001/0003's).

-- 0001:37 -- advisor_profiles
drop policy if exists "advisors can read their own profile" on advisor_profiles;
create policy "advisors can read their own profile"
  on advisor_profiles for select
  to authenticated
  using ((select auth.uid()) = id);

-- 0005:18/23/28 + 0008:24 -- student_plans
drop policy if exists "students can read their own plan" on student_plans;
create policy "students can read their own plan"
  on student_plans for select
  to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "students can insert their own plan" on student_plans;
create policy "students can insert their own plan"
  on student_plans for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

drop policy if exists "students can update their own plan" on student_plans;
create policy "students can update their own plan"
  on student_plans for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "students can delete their own plan" on student_plans;
create policy "students can delete their own plan"
  on student_plans for delete
  to authenticated
  using ((select auth.uid()) = user_id);

-- 0006:79 -- plan_comments (the auth.uid() is inside the display_name
-- subselect; only that call changes)
drop policy if exists "scoped comment inserts" on plan_comments;
create policy "scoped comment inserts"
  on plan_comments for insert
  to anon, authenticated
  with check (
    length(body) between 1 and 4000
    and (
      (author_role = 'student' and author_name = 'You')
      or (
        author_role = 'advisor'
        and is_advisor()
        and author_name = (select display_name from advisor_profiles where id = (select auth.uid()))
      )
    )
  );

-- 0006:99 -- meeting_proposals
drop policy if exists "advisors can propose meetings" on meeting_proposals;
create policy "advisors can propose meetings"
  on meeting_proposals for insert
  to authenticated
  with check (
    advisor_id = (select auth.uid())
    and is_advisor()
    and (note is null or length(note) <= 2000)
  );

-- 0011:79 -- course_enrollments
drop policy if exists "students can read their own enrollment status" on course_enrollments;
create policy "students can read their own enrollment status"
  on course_enrollments for select
  to authenticated
  using ((select auth.uid()) = student_id);

-- 0011:596/601/606 -- student_profiles
drop policy if exists "students can read their own profile" on student_profiles;
create policy "students can read their own profile"
  on student_profiles for select
  to authenticated
  using ((select auth.uid()) = id);

drop policy if exists "students can upsert their own profile" on student_profiles;
create policy "students can upsert their own profile"
  on student_profiles for insert
  to authenticated
  with check ((select auth.uid()) = id);

drop policy if exists "students can update their own profile" on student_profiles;
create policy "students can update their own profile"
  on student_profiles for update
  to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

-- Verified on the local replay: pg_policies shows all 11 with the
-- (select auth.uid()) form and unchanged roles/commands, and a spot check
-- as authenticated (own student_plans row visible, another user's not;
-- own advisor_profiles row visible; a forged-author_name advisor comment
-- still rejected) behaves as before.

-- ═══════════════════════════════════════════════════════════════════════
-- FIX 7: covering indexes for the review-thread reads and the FK columns
-- ═══════════════════════════════════════════════════════════════════════
-- get_review_request_comments / get_review_request_meetings (0001) both
-- filter by review_request_id and order by created_at, and both the
-- student page and the advisor page call them on every load; neither
-- table had anything beyond its primary key, so each call was a full scan
-- + sort. A composite (review_request_id, created_at) index serves the
-- filter and the ORDER BY in one pass. These same two indexes also cover
-- two of the performance advisor's seven unindexed_foreign_keys findings.
create index if not exists plan_comments_review_request_id_created_at_idx
  on plan_comments (review_request_id, created_at);

create index if not exists meeting_proposals_review_request_id_created_at_idx
  on meeting_proposals (review_request_id, created_at);

-- The remaining five findings are all foreign keys into auth.users. None
-- of them is filtered on by any app query today, but every one of them is
-- scanned by the delete side of the FK the moment delete_my_account()
-- (0009) removes an auth.users row -- Postgres has to find the referencing
-- rows to cascade (course_enrollments) or set null (the other four), and
-- without an index that's a sequential scan of each table per deleted
-- account, holding the delete open for the duration. Cheap now, and it's
-- the account-deletion path the Privacy Policy promises, so worth not
-- letting it degrade with table size. student_plans.user_id already has
-- one (0008).
create index if not exists advisor_invite_codes_used_by_idx on advisor_invite_codes (used_by);
create index if not exists course_enrollments_student_id_idx on course_enrollments (student_id);
create index if not exists course_groups_created_by_idx on course_groups (created_by);
create index if not exists meeting_proposals_advisor_id_idx on meeting_proposals (advisor_id);
create index if not exists security_events_actor_id_idx on security_events (actor_id);

-- Left alone, deliberately:
--   - The advisor's unused_index note on student_plans_user_id_idx (and
--     the same note it will raise for the five FK indexes above until an
--     account is deleted) -- "unused" here means "no account deletion has
--     happened yet," not "unneeded."
--   - The live supabase_migrations history begins at 0010; 0001..0009 were
--     applied by hand through the SQL editor before migrations were
--     tracked. Nothing in this file depends on that, but it's why
--     `supabase migration list` won't show them.
