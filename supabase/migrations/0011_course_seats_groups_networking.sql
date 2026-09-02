-- Three new, related features, one migration since they share tables
-- (course_enrollments underlies both the seat pool and the LinkedIn
-- visibility scope):
--   A. Real, race-safe course seat pools + waitlist
--   B. "Take this course with friends" invite-link coordination groups
--   C. Optional, opt-in LinkedIn sharing, visible only to actual classmates
--
-- ═══════════════════════════════════════════════════════════════════════
-- PART A: real, race-safe course seat pools + waitlist
-- ═══════════════════════════════════════════════════════════════════════
-- Until now "seats left" on the Weekly Schedule was dummySeatAvailabilityFor
-- -- a hash of the course code, computed client-side, never touching a
-- database, shared with nobody. There is no real, shared, capacity-limited
-- signup anywhere in this app. This adds one: a persisted seat pool per
-- course and an atomic claim-or-waitlist RPC, so that if 200 students hit
-- Apply for a 50-seat course at once, exactly 50 get "enrolled" and the
-- other 150 get "waitlisted" -- never more than capacity, regardless of
-- how many requests race each other.
--
-- Scope deliberately kept to one pool per course_code (not per
-- section/term -- this app has no concept of course sections/offerings
-- anywhere else, and inventing one is out of scope here). This represents
-- "the upcoming offering" of that course in the aggregate, which matches
-- how the Weekly Schedule already treats "next semester" recommendations.
--
-- Requires a real student account (student_plans already established this
-- as optional/additive) -- a global, persistent, contended seat count has
-- no meaningful way to track anonymous, no-account students across
-- sessions/devices, so Apply is gated behind being signed in on the
-- frontend, same as anything else in student_plans.

create table if not exists course_seat_pools (
  -- Bounded (not just "text primary key"): every RPC below accepts
  -- p_course_code straight from an authenticated caller with no backend in
  -- between (this table talks to the browser directly, same as every
  -- table in this app) -- an unbounded text primary key is an open
  -- storage-abuse vector for anyone holding the anon key, the exact class
  -- of bug 0010's own FIX 2 closed for review_requests.plan_state. A real
  -- course code (even a cross-listed one, e.g. "CMPSC/CMPEN 315") is
  -- nowhere near 40 characters.
  course_code text primary key check (length(course_code) <= 40),
  capacity int not null default 50 check (capacity > 0),
  seats_taken int not null default 0 check (seats_taken >= 0),
  created_at timestamptz not null default now()
);

alter table course_seat_pools enable row level security;

-- Capacity/seats_taken aren't sensitive on their own (no identity attached)
-- -- readable by anyone so the UI can show real numbers before a student
-- even applies. All writes go through claim_course_seat/drop_course_seat
-- below; no insert/update/delete policy is granted here at all.
create policy "seat pool counts are publicly readable"
  on course_seat_pools for select
  using (true);

grant select on course_seat_pools to anon, authenticated;

create table if not exists course_enrollments (
  id uuid primary key default gen_random_uuid(),
  course_code text not null check (length(course_code) <= 40),
  student_id uuid not null references auth.users(id) on delete cascade,
  status text not null check (status in ('enrolled', 'waitlisted')),
  created_at timestamptz not null default now(),
  unique (course_code, student_id)
);

create index if not exists course_enrollments_course_status_idx
  on course_enrollments (course_code, status, created_at);

alter table course_enrollments enable row level security;

-- A student can only ever see their OWN enrollment/waitlist row -- never
-- who else is enrolled in or waitlisted for a course. Matches the app's
-- existing default-anonymous posture (see student_plans's own comment:
-- "there is no display name anywhere in the product surface for a
-- student"). Aggregate counts (course_seat_pools) are public; identity
-- behind them is not.
create policy "students can read their own enrollment status"
  on course_enrollments for select
  to authenticated
  using (auth.uid() = student_id);

grant select on course_enrollments to authenticated;
-- No insert/update/delete grant -- all writes go through the
-- SECURITY DEFINER RPCs below, which is what actually makes the
-- claim-or-waitlist decision atomic instead of a client-side
-- read-then-write race.

-- claim_course_seat: the atomic first-come-first-served decision. The
-- `update ... where seats_taken < capacity returning` is what actually
-- prevents oversubscription -- Postgres only locks a row an UPDATE's WHERE
-- clause matches against THIS transaction's own snapshot, so two
-- concurrent callers who both still see a free seat correctly serialize
-- (the second blocks until the first commits, then re-checks the now-
-- current row and correctly loses). That part needs no advisory lock.
--
-- What DOES need one: the idempotent-reapply check just below is a plain,
-- unlocked SELECT. Two calls from the SAME student for the SAME course,
-- close enough together (a second tab, a client-side retry, literally
-- calling this RPC twice in a row) can both read "no existing row" before
-- either commits, and both then try to INSERT one -- the second hits this
-- table's own (course_code, student_id) unique constraint and raises an
-- unhandled error straight to the caller, exactly what the idempotent
-- branch below exists to avoid. pg_advisory_xact_lock, keyed to this
-- (course, student) pair, serializes only calls that could actually
-- conflict with each other -- it's a no-op for every other course/student
-- combination, and releases itself automatically at commit/rollback (xact-
-- scoped), so there's no separate unlock to forget.
create or replace function claim_course_seat(p_course_code text)
returns table(status text, seat_position int)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_student uuid := auth.uid();
  v_existing record;
  v_claimed int;
  v_position int;
begin
  if v_student is null then
    raise exception 'must be signed in to apply';
  end if;

  perform pg_advisory_xact_lock(hashtext(p_course_code), hashtext(v_student::text));

  insert into course_seat_pools (course_code) values (p_course_code)
    on conflict (course_code) do nothing;

  -- Idempotent re-apply: same student hitting Apply twice (double-click,
  -- retry after a dropped response) must not consume a second seat or
  -- throw on the unique constraint -- just return their existing status.
  -- Safe from the race described above now that the advisory lock forces
  -- a second concurrent call to wait until the first has committed its
  -- own insert (if any) before even reaching this SELECT.
  select ce.status into v_existing
    from course_enrollments ce
    where ce.course_code = p_course_code and ce.student_id = v_student;

  if found then
    if v_existing.status = 'enrolled' then
      return query select 'enrolled'::text, null::int;
    else
      select count(*) + 1 into v_position
        from course_enrollments ce
        where ce.course_code = p_course_code
          and ce.status = 'waitlisted'
          and ce.created_at < (
            select created_at from course_enrollments
            where course_code = p_course_code and student_id = v_student
          );
      return query select 'waitlisted'::text, v_position;
    end if;
    -- Without this, execution would fall through into the claim logic
    -- below on every repeat call -- double-clicking Apply while already
    -- enrolled would re-increment seats_taken and then hit this table's
    -- own (course_code, student_id) unique constraint on the second
    -- insert, throwing instead of the no-op this branch exists to be.
    return;
  end if;

  update course_seat_pools
    set seats_taken = seats_taken + 1
    where course_code = p_course_code and seats_taken < capacity
    returning seats_taken into v_claimed;

  if found then
    insert into course_enrollments (course_code, student_id, status)
      values (p_course_code, v_student, 'enrolled');
    return query select 'enrolled'::text, null::int;
  else
    -- The failed UPDATE above never actually locked this row (its WHERE
    -- clause didn't match against this transaction's own snapshot, so
    -- Postgres never attempted to take the write lock at all) -- so two
    -- different students discovering the course is full at the same
    -- moment aren't serialized by it. Take an explicit row lock before
    -- counting the waitlist so their position numbers can't be computed
    -- from the same stale count and collide.
    perform 1 from course_seat_pools where course_code = p_course_code for update;
    insert into course_enrollments (course_code, student_id, status)
      values (p_course_code, v_student, 'waitlisted');
    select count(*) into v_position
      from course_enrollments ce
      where ce.course_code = p_course_code and ce.status = 'waitlisted';
    return query select 'waitlisted'::text, v_position;
  end if;
end;
$$;

grant execute on function claim_course_seat(text) to authenticated;

-- get_my_enrollment: a read-only counterpart to claim_course_seat, for the
-- frontend to check "where do I stand" on later visits without applying
-- again. SECURITY DEFINER for the same reason as the position math inside
-- claim_course_seat itself -- computing a waitlist rank means counting
-- OTHER students' rows, which course_enrollments' own RLS (select-own-row-
-- only) would silently reduce to zero if done as a plain client-side
-- query. Returns no row at all if the student never applied.
create or replace function get_my_enrollment(p_course_code text)
returns table(status text, seat_position int)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_student uuid := auth.uid();
  v_status text;
  v_position int;
begin
  if v_student is null then
    raise exception 'must be signed in';
  end if;

  select ce.status into v_status
    from course_enrollments ce
    where ce.course_code = p_course_code and ce.student_id = v_student;

  if not found then
    return;
  end if;

  if v_status = 'enrolled' then
    return query select 'enrolled'::text, null::int;
    return;
  end if;

  select count(*) into v_position
    from course_enrollments ce
    where ce.course_code = p_course_code
      and ce.status = 'waitlisted'
      and ce.created_at < (
        select created_at from course_enrollments
        where course_code = p_course_code and student_id = v_student
      );
  return query select 'waitlisted'::text, v_position + 1;
end;
$$;

grant execute on function get_my_enrollment(text) to authenticated;

-- release_freed_course_seat: a trigger, not a plain function called only
-- from drop_course_seat -- deliberately, because course_enrollments rows
-- can disappear a second way that never goes through drop_course_seat at
-- all: `student_id ... on delete cascade` (this table's own FK) fires the
-- moment delete_my_account() (migration 0009) removes an enrolled
-- student's auth.users row. That cascade is a plain DELETE at the
-- database level -- if the seat-release/promotion logic only lived inside
-- drop_course_seat, an account deletion would silently leave
-- seats_taken permanently too high (the seat is gone, but nothing ever
-- decremented the count or promoted the next waitlisted student), forever
-- undercounting that course's real availability by one seat per deleted
-- account. An AFTER DELETE trigger fires for every row removal
-- regardless of source -- drop_course_seat's own explicit DELETE, or the
-- FK cascade -- so this is the one place the logic needs to live.
create or replace function release_freed_course_seat()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_promoted uuid;
begin
  if old.status <> 'enrolled' then
    return old;
  end if;

  update course_seat_pools
    set seats_taken = greatest(seats_taken - 1, 0)
    where course_code = old.course_code;

  select id into v_promoted from course_enrollments
    where course_code = old.course_code and status = 'waitlisted'
    order by created_at asc
    limit 1
    for update;

  if found then
    update course_enrollments set status = 'enrolled'
      where id = v_promoted;
    update course_seat_pools
      set seats_taken = seats_taken + 1
      where course_code = old.course_code;
  end if;

  return old;
end;
$$;

create trigger course_enrollment_released
  after delete on course_enrollments
  for each row execute function release_freed_course_seat();

-- drop_course_seat: the only thing left to do here is the delete itself --
-- release_freed_course_seat (above) picks up from there atomically, in
-- the same transaction, the same way it does for a cascade-deleted
-- account. Because both that trigger's seats_taken updates and
-- claim_course_seat's increment target the exact same course_seat_pools
-- row, Postgres's row lock serializes them: a concurrent new applicant
-- calling claim_course_seat while this transaction is mid-promotion
-- simply blocks until this transaction commits, then correctly sees the
-- promoted student already occupying the freed seat -- so a brand-new
-- applicant can never jump ahead of someone who was already waitlisted.
create or replace function drop_course_seat(p_course_code text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_student uuid := auth.uid();
begin
  if v_student is null then
    raise exception 'must be signed in';
  end if;

  -- Same (course, student)-scoped advisory lock claim_course_seat takes --
  -- closes the same student calling Drop and Apply for the same course at
  -- nearly the same moment, not just Apply-vs-Apply.
  perform pg_advisory_xact_lock(hashtext(p_course_code), hashtext(v_student::text));

  delete from course_enrollments
    where course_code = p_course_code and student_id = v_student;
end;
$$;

grant execute on function drop_course_seat(text) to authenticated;

-- ═══════════════════════════════════════════════════════════════════════
-- PART B: "take this course with friends" coordination groups
-- ═══════════════════════════════════════════════════════════════════════
-- Invite-link based, not directory/search based -- deliberately: this app
-- has no student search/browse surface anywhere (see Part A's comment on
-- course_enrollments' RLS), and adding one just to let friends "find" each
-- other would be a much bigger, riskier privacy surface than what was
-- actually asked for. A student creates a group for a course, gets a
-- short invite code, and shares it themselves outside the app (text,
-- email, whatever) -- only someone who already has that code can join.
-- Membership is visible only to fellow members of that same group, never
-- globally, and members are shown as an anonymous count + aggregate
-- enrollment status only (no student name/identity is exposed by this --
-- this app has no student display-name concept anywhere, and this feature
-- doesn't introduce one).

create table if not exists course_groups (
  id uuid primary key default gen_random_uuid(),
  course_code text not null check (length(course_code) <= 40),
  invite_code text not null unique,
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);

alter table course_groups enable row level security;
grant select on course_groups to authenticated;
-- The "members can read their own groups" policy has to come AFTER
-- course_group_members exists below -- its USING clause references that
-- table, and Postgres validates a policy's expression at creation time,
-- not lazily at first use. Defining it here (before that table existed)
-- was a real bug: `create policy ... using (exists (select 1 from
-- course_group_members ...))` failed outright with `relation
-- "course_group_members" does not exist` the first time this migration
-- was actually run -- caught only by applying it for real, not by static
-- review. See the policy itself, right after that table's own RLS setup.

create table if not exists course_group_members (
  group_id uuid not null references course_groups(id) on delete cascade,
  student_id uuid not null references auth.users(id) on delete cascade,
  -- Denormalized from course_groups.course_code (populated by
  -- create_course_group/join_course_group below, never set directly --
  -- there's no insert/update grant on this table at all outside those
  -- RPCs). Exists purely to carry the unique constraint just below:
  -- nothing else stops a student from joining two DIFFERENT groups for
  -- the SAME course_code (there's no reason group_id alone would catch
  -- that), which breaks the frontend's findMyGroup() -- it queries
  -- course_groups by course_code expecting at most one match, and
  -- Supabase's .maybeSingle() throws if RLS legitimately returns two.
  course_code text not null check (length(course_code) <= 40),
  joined_at timestamptz not null default now(),
  primary key (group_id, student_id),
  unique (student_id, course_code)
);

alter table course_group_members enable row level security;

create policy "members can see fellow members of their own groups"
  on course_group_members for select
  to authenticated
  using (
    exists (
      select 1 from course_group_members m2
      where m2.group_id = course_group_members.group_id
        and m2.student_id = auth.uid()
    )
  );

grant select on course_group_members to authenticated;

-- Deferred from course_groups' own setup above -- this expression needs
-- course_group_members to already exist.
create policy "members can read their own groups"
  on course_groups for select
  to authenticated
  using (
    exists (
      select 1 from course_group_members m
      where m.group_id = course_groups.id and m.student_id = auth.uid()
    )
  );

-- Writes (create/join/leave) go through the RPCs below only.

-- create_course_group / join_course_group both insert into
-- course_group_members, which is where a student joining a SECOND group
-- for a course they're already grouped up for would hit the new
-- unique(student_id, course_code) constraint -- caught explicitly here so
-- that shows up as a clear, friendly exception message instead of a raw
-- Postgres constraint-violation error reaching the frontend toast.
create or replace function create_course_group(p_course_code text)
returns table(group_id uuid, invite_code text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_student uuid := auth.uid();
  v_group_id uuid;
  -- 16 hex chars (~64 bits) -- up from an earlier 8-char (~32-bit) draft.
  -- This code is meant to be reusable (every friend who has it should be
  -- able to join, not just the first), so unlike advisor_invite_codes
  -- there's no single-use "claimed" guard to also rely on -- entropy is
  -- the only defense against a guessing/enumeration attempt, so it needs
  -- to be strong on its own.
  v_code text := substr(md5(gen_random_uuid()::text) || md5(gen_random_uuid()::text), 1, 16);
begin
  if v_student is null then
    raise exception 'must be signed in';
  end if;

  insert into course_groups (course_code, invite_code, created_by)
    values (p_course_code, v_code, v_student)
    returning id into v_group_id;

  begin
    insert into course_group_members (group_id, student_id, course_code)
      values (v_group_id, v_student, p_course_code);
  exception when unique_violation then
    raise exception 'You''re already in a group for this course.';
  end;

  return query select v_group_id, v_code;
end;
$$;

grant execute on function create_course_group(text) to authenticated;

create or replace function join_course_group(p_invite_code text)
returns table(group_id uuid, course_code text)
language plpgsql
security definer
set search_path = public
as $$
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

  return query select v_group.id, v_group.course_code;
end;
$$;

grant execute on function join_course_group(text) to authenticated;

create or replace function leave_course_group(p_group_id uuid)
returns void
language sql
security definer
set search_path = public
as $$
  delete from course_group_members
    where group_id = p_group_id and student_id = auth.uid();
$$;

grant execute on function leave_course_group(uuid) to authenticated;

-- get_group_status: course_group_members' own RLS policy already lets a
-- member see who else is in their group, but course_enrollments' policy
-- deliberately does NOT extend that same trust -- a student can only ever
-- read their OWN enrollment row (see Part A), full stop, no exceptions for
-- groupmates. That's intentional: the frontend showing "2 of 3 friends
-- have a seat" must not require weakening the one RLS boundary this whole
-- feature was designed around (course_enrollments = never visible to
-- anyone but its own owner). This RPC is the narrow, audited exception --
-- SECURITY DEFINER specifically so it CAN read every member's row
-- internally, but it only ever returns aggregate counts, never which
-- member has which status.
create or replace function get_group_status(p_group_id uuid)
returns table(member_count int, enrolled_count int, waitlisted_count int)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_student uuid := auth.uid();
  v_course_code text;
begin
  if v_student is null then
    raise exception 'must be signed in';
  end if;

  if not exists (
    select 1 from course_group_members
    where group_id = p_group_id and student_id = v_student
  ) then
    raise exception 'not a member of this group';
  end if;

  select course_code into v_course_code from course_groups where id = p_group_id;

  return query
    select
      count(*)::int as member_count,
      count(*) filter (where ce.status = 'enrolled')::int as enrolled_count,
      count(*) filter (where ce.status = 'waitlisted')::int as waitlisted_count
    from course_group_members cgm
    left join course_enrollments ce
      on ce.student_id = cgm.student_id and ce.course_code = v_course_code
    where cgm.group_id = p_group_id;
end;
$$;

grant execute on function get_group_status(uuid) to authenticated;

-- ═══════════════════════════════════════════════════════════════════════
-- PART C: optional, opt-in LinkedIn visible only to actual classmates
-- ═══════════════════════════════════════════════════════════════════════
-- OFF by default (is_linkedin_public starts false) -- a student must
-- explicitly turn this on. Even then, it is never a public directory:
-- another student can only see it if they (a) opted in themselves is NOT
-- required to view, but (b) they share an actual enrolled course with this
-- student, per course_enrollments from Part A. This mirrors the real ask
-- ("other students in their classes can also connect with them"), not a
-- site-wide public profile list, and it costs no new identity disclosure
-- beyond a URL the student chose to publish themselves.
--
-- Deliberately NOT exposed via a plain RLS "using" policy: a policy on
-- student_profiles that peeks into course_enrollments for ANOTHER
-- student's row would itself be blocked by course_enrollments' own RLS
-- (select-own-row-only, no exception) -- a plain declarative policy's
-- subquery runs as the querying role, same as any other query against
-- that table, so it can't see rows RLS wouldn't otherwise let it see. This
-- is exactly why create_review_request/is_advisor/claim_advisor_profile
-- are RPCs rather than table grants (see their own comments). Same fix
-- here: get_classmate_linkedins below, SECURITY DEFINER, so it alone can
-- cross that boundary internally -- and even then it only ever returns
-- opted-in URLs, never which student_id any of them belongs to.
create table if not exists student_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  linkedin_url text
    check (
      linkedin_url is null
      or (length(linkedin_url) <= 300 and linkedin_url ~* '^https://([a-z]{2,3}\.)?linkedin\.com/.+')
    ),
  is_linkedin_public boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table student_profiles enable row level security;

create policy "students can read their own profile"
  on student_profiles for select
  to authenticated
  using (auth.uid() = id);

create policy "students can upsert their own profile"
  on student_profiles for insert
  to authenticated
  with check (auth.uid() = id);

create policy "students can update their own profile"
  on student_profiles for update
  to authenticated
  using (auth.uid() = id)
  with check (auth.uid() = id);

grant select, insert, update on student_profiles to authenticated;

-- get_classmate_linkedins: the caller must themselves be 'enrolled' (not
-- merely waitlisted) in the course to see anything at all -- enforced
-- inside the function body since RLS can't do it here (see comment
-- above). Returns bare URLs with no student_id attached, so even this
-- function can't be used to map a URL back to a specific classmate beyond
-- what the URL itself reveals.
create or replace function get_classmate_linkedins(p_course_code text)
returns table(linkedin_url text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_student uuid := auth.uid();
begin
  if v_student is null then
    raise exception 'must be signed in';
  end if;

  if not exists (
    select 1 from course_enrollments
    where course_code = p_course_code and student_id = v_student and status = 'enrolled'
  ) then
    return;
  end if;

  return query
    select sp.linkedin_url
    from student_profiles sp
    join course_enrollments ce
      on ce.student_id = sp.id
    where ce.course_code = p_course_code
      and ce.status = 'enrolled'
      and ce.student_id <> v_student
      and sp.is_linkedin_public = true
      and sp.linkedin_url is not null;
end;
$$;

grant execute on function get_classmate_linkedins(text) to authenticated;
