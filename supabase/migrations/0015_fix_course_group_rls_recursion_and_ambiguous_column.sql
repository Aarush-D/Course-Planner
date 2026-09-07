-- Two independent bugs in the course-group feature (migrations
-- 0011/0013), both confirmed by actually reproducing them against a
-- throwaway local Postgres 16 instance (not the live Supabase project --
-- this migration only touches that when the user applies it themselves):
--
--   FIX 1 -- RLS self-recursion on course_group_members. Reading that
--   table as any authenticated user fails outright with "infinite
--   recursion detected in policy for relation \"course_group_members\""
--   (Postgres error 42P17), which breaks CourseGroupService entirely --
--   findMyGroup(), the fellow-member list, everything that queries this
--   table.
--
--   FIX 2 -- join_course_group() fails on every call with "column
--   reference \"invite_code\" is ambiguous" (and, as it turns out, the
--   exact same ambiguity on group_id one statement later -- see FIX 2
--   below), so no student can actually join a group via invite code at
--   all right now.
--
-- ═══════════════════════════════════════════════════════════════════════
-- FIX 1: course_group_members' own SELECT policy queries itself
-- ═══════════════════════════════════════════════════════════════════════
-- The policy as shipped in 0011:
--
--   create policy "members can see fellow members of their own groups"
--     on course_group_members for select
--     to authenticated
--     using (
--       exists (
--         select 1 from course_group_members m2
--         where m2.group_id = course_group_members.group_id
--           and m2.student_id = auth.uid()
--       )
--     );
--
-- looks like the same "check membership via a self-join" idiom used all
-- over this codebase (get_group_status, get_classmate_linkedins, etc.),
-- but those are all inside SECURITY DEFINER functions, which run with the
-- function owner's privileges and so never re-trigger RLS on the table
-- they're reading. This one is a plain declarative policy, evaluated as
-- the querying role -- so its own `select 1 from course_group_members m2`
-- subquery re-enters course_group_members' RLS, which means re-evaluating
-- this exact same policy on m2, which subqueries course_group_members
-- again, forever. Postgres detects the cycle and refuses outright rather
-- than actually looping:
--
--   ERROR:  infinite recursion detected in policy for relation
--   "course_group_members"
--
-- Reproduced live (throwaway local Postgres, not the Supabase project):
-- created the table with this exact policy, `set role` to a non-superuser
-- role standing in for `authenticated` (a superuser connection bypasses
-- RLS entirely and would never have shown this), and `select * from
-- course_group_members` as a real member of a real group raised exactly
-- that error.
--
-- The fix used throughout the rest of this codebase for "a policy needs
-- to check something only visible by querying the very table RLS is
-- protecting" is a SECURITY DEFINER helper (is_advisor() in 0003 is the
-- clearest example: "SECURITY DEFINER so the lookup isn't itself subject
-- to the caller's own RLS view"). Same fix here: is_course_group_member()
-- runs as the function owner, so its internal query against
-- course_group_members never re-enters this policy at all -- the
-- recursion is gone because the policy itself no longer contains a query
-- against the RLS-protected table, just a function call.
create or replace function is_course_group_member(p_group_id uuid, p_user_id uuid)
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1 from course_group_members
    where group_id = p_group_id and student_id = p_user_id
  );
$$;

-- Like every SECURITY DEFINER function here, Postgres granted EXECUTE to
-- PUBLIC the moment this was created (0014's own comment covers this in
-- detail) -- revoked so the only path to it is the one policy below that
-- actually needs it. authenticated still needs an explicit grant, though:
-- unlike a SECURITY DEFINER function's own body (which runs as the
-- owner), the CALL to it from inside a policy's USING clause is made by
-- the querying role -- here, whatever role the policy is scoped `to`,
-- i.e. authenticated -- so that role needs its own EXECUTE grant or the
-- policy itself fails closed with "permission denied for function
-- is_course_group_member" the moment anyone tries to read this table.
revoke execute on function is_course_group_member(uuid, uuid) from public, anon;
grant execute on function is_course_group_member(uuid, uuid) to authenticated;

drop policy if exists "members can see fellow members of their own groups" on course_group_members;
create policy "members can see fellow members of their own groups"
  on course_group_members for select
  to authenticated
  using (
    is_course_group_member(course_group_members.group_id, auth.uid())
  );

-- Verified against the local reproduction above after applying this fix:
-- the same query that raised 42P17 now returns the caller's own group's
-- roster (no recursion), and a second student who belongs to a
-- *different* group still correctly sees zero rows -- so this isn't just
-- "stopped erroring," the isolation the original policy was trying to
-- express is actually preserved.
--
-- "members can read their own groups" on course_groups (also 0011) is
-- left untouched -- it queries course_group_members (a different table
-- from the one its own policy protects), which is not a cycle and never
-- raised this error. It was only ever a casualty of FIX 1's bug by
-- association, whenever it happened to trigger course_group_members' own
-- SELECT policy along the way; with that policy fixed, this one needs no
-- change of its own.

-- ═══════════════════════════════════════════════════════════════════════
-- FIX 2: join_course_group()'s OUT parameters shadow real column names
-- ═══════════════════════════════════════════════════════════════════════
-- 0013 widened this function's return type to
-- `returns table(group_id uuid, course_code text, invite_code text)`,
-- which in PL/pgSQL implicitly declares group_id/course_code/invite_code
-- as variables local to the function -- and all three happen to also be
-- real column names on course_groups / course_group_members, the exact
-- tables this function queries. Two lines in the function body are
-- genuinely ambiguous between "the local variable" and "the table
-- column," and Postgres's default (plpgsql.variable_conflict = error)
-- refuses to guess:
--
--   1. `select * into v_group from course_groups where invite_code =
--      p_invite_code;` -- confirmed live:
--        ERROR:  column reference "invite_code" is ambiguous
--        DETAIL:  It could refer to either a PL/pgSQL variable or a
--        table column.
--
--   2. `... on conflict (group_id, student_id) do nothing;` -- the same
--      ambiguity, on group_id this time, discovered only by actually
--      applying the WHERE-clause fix below and re-running this function
--      against the local reproduction -- the first error had been
--      masking this second one. This is *why* the fix below isn't simply
--      "qualify the WHERE clause with a table alias": an ON CONFLICT
--      conflict target is a bare column list by Postgres's own grammar
--      (`on conflict (cg.group_id, ...)` is not valid syntax -- there is
--      no table alias in scope to qualify it with), so table-alias
--      qualification cannot fix this second occurrence no matter how
--      it's written.
--
-- The one thing that resolves both is `#variable_conflict use_column`,
-- Postgres's own documented answer to exactly this shape of bug (RETURNS
-- TABLE / OUT parameter names colliding with real column names): it
-- tells the PL/pgSQL parser, for this function only, that an unqualified
-- name ambiguous between a variable and a column should resolve to the
-- column -- i.e. the OUT parameter no longer shadows it. That's a
-- behavioral no-op here: this function never actually reads the bare
-- group_id/course_code/invite_code variables by name anywhere -- every
-- value it returns comes from the explicit `v_group.id` /
-- `v_group.course_code` / `v_group.invite_code` in the final
-- `return query select`, never an implicit fallthrough of the OUT params
-- -- so there is no case where the *variable* reading was the one that
-- mattered. Renaming the OUT parameters instead was ruled out: the
-- frontend (CourseGroupService.joinGroup, Frontend/src/services/
-- course-group.service.ts) destructures the RPC response by exactly these
-- names (`row.group_id`, `row.course_code`, `row.invite_code`), and
-- CREATE OR REPLACE can't change a function's OUT column names without
-- dropping it first (0013's own comment already hit this same
-- restriction) -- so a rename would mean a breaking signature change and
-- a frontend edit for a bug that doesn't otherwise call for either.
--
-- Function body is otherwise byte-for-byte the same as 0013's -- same
-- signature, so no drop-first needed the way 0013 needed one against
-- 0011's narrower return type.
create or replace function join_course_group(p_invite_code text)
returns table(group_id uuid, course_code text, invite_code text)
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
    -- audit trail this app has.
    insert into security_events (event_type, actor_id, detail)
      values ('course_group_join_rejected', v_student, jsonb_build_object('reason', 'invalid_invite_code'));
    raise exception 'invalid invite code';
  end if;

  -- Idempotent if already a member of THIS group (on conflict does
  -- nothing); a friendly error if they're already in a DIFFERENT group for
  -- the same course_code (the unique(student_id, course_code) constraint).
  begin
    insert into course_group_members (group_id, student_id, course_code)
      values (v_group.id, v_student, v_group.course_code)
      on conflict (group_id, student_id) do nothing;
  exception when unique_violation then
    raise exception 'You''re already in a group for this course.';
  end;

  return query select v_group.id, v_group.course_code, v_group.invite_code;
end;
$$;

grant execute on function join_course_group(text) to authenticated;

-- Verified against the local reproduction above after applying this fix:
-- a real invite code now returns (group_id, course_code, invite_code)
-- correctly on both a first join and an idempotent repeat call, and an
-- unknown code still raises the intended "invalid invite code" (not an
-- ambiguity error) -- all three branches of the function actually
-- exercised, not just the happy path.
--
-- Separately noticed but NOT fixed here (out of scope for this migration
-- -- only the two bugs above were asked for): the security_events insert
-- on the invalid-code branch is immediately followed, in the same
-- transaction, by an unhandled `raise exception` -- which rolls back that
-- insert along with everything else, so the audit trail this comment
-- above says it's for likely never actually persists a row today. Worth
-- its own follow-up migration (e.g. wrapping the insert in its own
-- exception-handling block, or a savepoint) if the audit trail matters in
-- practice; left alone here since it's a third, separate bug this task
-- didn't ask about.
