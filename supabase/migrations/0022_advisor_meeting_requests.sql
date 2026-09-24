-- Student-initiated meeting requests on top of the standing advisor roster
-- (0019/0020). The existing meeting_proposals table (0001) goes the other
-- way -- an advisor proposes a time against a one-off review request --
-- and is untouched. This table is the missing direction: a rostered
-- student asks their advisor for a meeting, the advisor confirms with a
-- real time or declines, and the student can cancel.
--
-- Same trust shape as advisee_comments (0019): both sides always have a
-- real Supabase Auth session, so plain RLS is enough for reads/inserts,
-- and the composite FK into advisor_rosters guarantees a request can only
-- exist between two parties with a live roster relationship (and
-- cascades away when either side ends it).
--
-- Status transitions go through SECURITY DEFINER RPCs, not a table-level
-- UPDATE grant: a plain UPDATE policy can't stop a student from editing
-- their own row's status/confirmed_at to "confirmed" (RLS has no
-- per-column rules), so no app role gets UPDATE at all.
create table if not exists advisor_meeting_requests (
  id uuid primary key default gen_random_uuid(),
  advisor_id uuid not null references advisor_profiles(id) on delete cascade,
  student_id uuid not null references auth.users(id) on delete cascade,
  foreign key (advisor_id, student_id) references advisor_rosters (advisor_id, student_id) on delete cascade,
  topic text not null check (length(topic) between 1 and 200),
  note text check (note is null or length(note) <= 2000),
  -- Free text ("Tuesdays after 2pm") -- deliberately not structured; the
  -- advisor picks the real time when confirming.
  preferred_times text check (preferred_times is null or length(preferred_times) <= 500),
  status text not null default 'requested'
    check (status in ('requested', 'confirmed', 'declined', 'cancelled')),
  confirmed_at timestamptz,
  advisor_response text check (advisor_response is null or length(advisor_response) <= 1000),
  created_at timestamptz not null default now()
);

-- One open request per pair: stops a student from flooding an advisor's
-- queue, and makes "you already have a pending request" a clean,
-- constraint-backed answer instead of app logic.
create unique index if not exists advisor_meeting_requests_one_open_idx
  on advisor_meeting_requests (advisor_id, student_id) where status = 'requested';
create index if not exists advisor_meeting_requests_thread_idx
  on advisor_meeting_requests (advisor_id, student_id, created_at);
-- Cascade-delete scan when a student's auth.users row is deleted (0018
-- FIX 7's reasoning: student_id isn't the leading column above).
create index if not exists advisor_meeting_requests_student_id_idx
  on advisor_meeting_requests (student_id);

alter table advisor_meeting_requests enable row level security;

create policy "roster participants can read their meeting requests"
  on advisor_meeting_requests for select
  to authenticated
  using (
    (is_advisor() and advisor_id = (select auth.uid()))
    or student_id = (select auth.uid())
  );

-- Students create requests only, and only in the initial state -- they
-- can't self-confirm by inserting a pre-confirmed row.
create policy "students can request a meeting"
  on advisor_meeting_requests for insert
  to authenticated
  with check (
    student_id = (select auth.uid())
    and status = 'requested'
    and confirmed_at is null
    and advisor_response is null
  );

grant select, insert on advisor_meeting_requests to authenticated;

-- ── advisor confirms or declines ─────────────────────────────────────────
create or replace function respond_to_meeting_request(
  p_request_id uuid,
  p_status text,
  p_confirmed_at timestamptz default null,
  p_response text default null
)
returns advisor_meeting_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  result advisor_meeting_requests;
begin
  if p_status not in ('confirmed', 'declined') then
    raise exception 'Status must be confirmed or declined.';
  end if;
  if p_status = 'confirmed' and p_confirmed_at is null then
    raise exception 'Pick a meeting time to confirm.';
  end if;
  if p_response is not null and length(p_response) > 1000 then
    raise exception 'That message is too long.';
  end if;

  update advisor_meeting_requests
  set status = p_status,
      confirmed_at = case when p_status = 'confirmed' then p_confirmed_at else null end,
      advisor_response = nullif(trim(p_response), '')
  where id = p_request_id
    and advisor_id = auth.uid()
    and is_advisor()
    and status = 'requested'
  returning * into result;

  if not found then
    raise exception 'That request is no longer open, or isn''t yours to answer.';
  end if;
  return result;
end;
$$;

-- ── student cancels their own request ────────────────────────────────────
create or replace function cancel_meeting_request(p_request_id uuid)
returns advisor_meeting_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  result advisor_meeting_requests;
begin
  update advisor_meeting_requests
  set status = 'cancelled'
  where id = p_request_id
    and student_id = auth.uid()
    and status in ('requested', 'confirmed')
  returning * into result;

  if not found then
    raise exception 'That request can''t be cancelled.';
  end if;
  return result;
end;
$$;

-- Postgres grants EXECUTE to PUBLIC by default -- the exact gap 0014/
-- 0017/0018 each had to close retroactively. Closed here from the start.
revoke execute on function respond_to_meeting_request(uuid, text, timestamptz, text) from public, anon;
revoke execute on function cancel_meeting_request(uuid) from public, anon;
grant execute on function respond_to_meeting_request(uuid, text, timestamptz, text) to authenticated;
grant execute on function cancel_meeting_request(uuid) to authenticated;
