-- 0023: a full course can't be registered (no more waitlist), and seat
-- counts are published live.
--
-- Product decision, 2026-09-24: when every seat in a course is taken, a
-- student who tries to register is told so and refused -- they are NOT
-- quietly placed on a waitlist. Two things follow from that:
--
--   1. claim_course_seat now RAISES for a full course instead of
--      inserting a 'waitlisted' row. The refusal is still race-safe for
--      exactly the same reason the old enrolled-vs-waitlisted decision
--      was (see 0011 Part A): it hangs off the `update ... where
--      seats_taken < capacity` whose WHERE clause Postgres re-checks
--      against the committed row after any concurrent claimer commits.
--      Two students racing for the last seat can't both get it, and the
--      loser gets the same 'course_full' error as someone who clicked a
--      minute late. The message text is a stable, machine-readable token
--      the frontend maps to its own copy ("X is full -- its seats can't be
--      registered."); don't reword it here without changing
--      CourseEnrollmentService's matcher.
--
--   2. course_seat_pools is added to the supabase_realtime publication so
--      every open browser sees a course fill up or free up the moment it
--      happens, without reloading -- one student's claim updates the count
--      on everyone else's Weekly Schedule. The table is already publicly
--      readable (0011's select policy grants anon), which is exactly the
--      policy Realtime evaluates for a subscriber, so no new grant is
--      needed and nothing identifying is exposed: the rows are aggregate
--      counts only.
--
-- release_freed_course_seat (0011) is left as-is. Its promote-the-next-
-- waitlisted-student branch simply never finds a row now, and rewriting a
-- trigger that already does the right thing (decrement on delete, nothing
-- else) buys nothing. get_my_enrollment likewise keeps its signature; the
-- waitlist-position arithmetic in it is dead but harmless.

-- ── 1. Leftover waitlist rows ─────────────────────────────────────────
-- Anyone still 'waitlisted' from before this migration gets a seat if
-- their course has one now (oldest request first, the order the old
-- promotion trigger would have used), and is otherwise removed -- there
-- is no longer any state for them to be in. Deleting a 'waitlisted' row
-- doesn't touch seats_taken (the trigger returns early for non-enrolled
-- rows), so the counts stay exact either way.
do $$
declare
  r record;
begin
  for r in
    select id, course_code from course_enrollments
      where status = 'waitlisted'
      order by created_at asc
  loop
    update course_seat_pools
      set seats_taken = seats_taken + 1
      where course_code = r.course_code and seats_taken < capacity;
    if found then
      update course_enrollments set status = 'enrolled' where id = r.id;
    else
      delete from course_enrollments where id = r.id;
    end if;
  end loop;
end $$;

-- ── 2. The schema says it too ─────────────────────────────────────────
-- Not just "the function never writes it": the column itself can no
-- longer hold anything but 'enrolled', so a future RPC (or a stray SQL
-- editor session) can't reintroduce a waitlist by accident.
alter table course_enrollments drop constraint if exists course_enrollments_status_check;
alter table course_enrollments
  add constraint course_enrollments_status_check check (status = 'enrolled');

-- ── 3. claim_course_seat: full means refused ──────────────────────────
-- Same signature as before (status, seat_position) so nothing that
-- already calls it needs a redeploy to keep working; seat_position is
-- now always null. The locking story is unchanged from 0011 -- read its
-- comment for why the (course, student) advisory lock is there.
create or replace function claim_course_seat(p_course_code text)
returns table(status text, seat_position int)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_student uuid := auth.uid();
  v_claimed int;
begin
  if v_student is null then
    raise exception 'must be signed in to apply';
  end if;

  perform pg_advisory_xact_lock(hashtext(p_course_code), hashtext(v_student::text));

  insert into course_seat_pools (course_code) values (p_course_code)
    on conflict (course_code) do nothing;

  -- Idempotent re-apply: a second click from a student who already holds
  -- a seat just reports it, and must not fall through to the claim below.
  if exists (
    select 1 from course_enrollments ce
      where ce.course_code = p_course_code and ce.student_id = v_student
  ) then
    return query select 'enrolled'::text, null::int;
    return;
  end if;

  update course_seat_pools
    set seats_taken = seats_taken + 1
    where course_code = p_course_code and seats_taken < capacity
    returning seats_taken into v_claimed;

  if not found then
    -- Stable token, matched verbatim by the frontend. The hint carries
    -- the human wording for anyone reading raw API errors.
    raise exception 'course_full'
      using hint = format('%s is full; its seats cannot be registered.', p_course_code);
  end if;

  insert into course_enrollments (course_code, student_id, status)
    values (p_course_code, v_student, 'enrolled');
  return query select 'enrolled'::text, null::int;
end;
$$;

-- ── 4. Live counts ────────────────────────────────────────────────────
-- Idempotent: adding a table that's already in the publication is an
-- error, and this migration should be safe to re-run.
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
      where pubname = 'supabase_realtime'
        and schemaname = 'public'
        and tablename = 'course_seat_pools'
  ) then
    alter publication supabase_realtime add table public.course_seat_pools;
  end if;
end $$;
