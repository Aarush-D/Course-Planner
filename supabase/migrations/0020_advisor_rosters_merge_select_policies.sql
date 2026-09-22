-- Follow-up to 0019_advisor_roster.sql, applied within minutes of it after
-- running the Supabase security/performance advisor on the freshly-applied
-- schema (per this repo's own convention -- see 0018's header for why a
-- throwaway-instance replay or a static read isn't enough on its own).
--
-- Finding: advisor_rosters had two permissive SELECT policies for the
-- `authenticated` role ("advisors can read their own roster",
-- "students can read their own roster assignments") -- Postgres has to
-- evaluate BOTH on every query against this table, even though only one
-- is ever relevant per caller. Same class of issue 0018 FIX 6 fixed for
-- 13 other policies across this schema.
--
-- Merging into one policy with an OR produces an identical result set --
-- Postgres already combines multiple permissive policies with OR, so this
-- is a pure query-plan simplification, not a behavior change. Confirmed
-- via the Supabase advisor tooling immediately after applying: the
-- "multiple_permissive_policies" WARN for advisor_rosters is gone post-fix,
-- with no new findings introduced.
drop policy if exists "advisors can read their own roster" on advisor_rosters;
drop policy if exists "students can read their own roster assignments" on advisor_rosters;

create policy "roster participants can read their own rows"
  on advisor_rosters for select
  to authenticated
  using (
    (is_advisor() and advisor_id = (select auth.uid()))
    or student_id = (select auth.uid())
  );
