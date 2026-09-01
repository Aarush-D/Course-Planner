-- A real, queryable audit trail for the app's privileged/destructive
-- actions. Before this, "logging" meant Render's ephemeral app-log stream
-- (Backend/app.py's access log) -- fine for the Flask API, but every
-- privileged action in this schema (claiming an advisor account,
-- responding to a meeting proposal, creating a review request) happens
-- via SECURITY DEFINER RPCs called directly from the browser, which never
-- touch Flask or Render's logs at all. Those events had no record
-- anywhere once they happened.
--
-- Scoped to what's actually capturable from inside a SQL function -- this
-- app has no login of its own to hook (Supabase Auth's own failed-login
-- attempts aren't something a plain SQL migration can observe; that needs
-- Auth Hooks, a separate Dashboard-configured feature) and no admin role
-- to gate a SELECT policy behind, so this is written-only via the RPCs
-- below and read via the Supabase SQL editor, same as how this project
-- already manages its own schema.
create table if not exists security_events (
  id uuid primary key default gen_random_uuid(),
  event_type text not null,
  -- auth.uid() at the time of the event -- null for every one of these
  -- RPCs' anonymous-caller cases (a student never has a session), not a
  -- data-quality gap.
  actor_id uuid references auth.users(id),
  detail jsonb,
  created_at timestamptz not null default now()
);

alter table security_events enable row level security;
-- No policies granted to anon or authenticated -- this table is never
-- meant to be listed via the API (nothing about "who claimed advisor
-- access and when" should be visible to every anon-key holder). Every
-- insert below runs as the SECURITY DEFINER function owner, which
-- bypasses RLS for its own writes regardless of grants.

-- ── claim_advisor_profile: record both outcomes ──────────────────────────
-- A failed attempt (wrong/reused code) is arguably the MORE interesting
-- event here -- it's what someone brute-forcing invite codes looks like.
create or replace function claim_advisor_profile(invite_code text, display_name text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if display_name is null or length(trim(display_name)) = 0 or length(display_name) > 100 then
    raise exception 'Enter a name between 1 and 100 characters.';
  end if;

  update advisor_invite_codes
  set used_by = auth.uid(), used_at = now()
  where code = invite_code and used_by is null;

  if not found then
    insert into security_events (event_type, actor_id, detail)
    values ('advisor_claim_failed', auth.uid(), jsonb_build_object('invite_code', invite_code));
    raise exception 'That invite code is invalid or already used.';
  end if;

  insert into advisor_profiles (id, display_name) values (auth.uid(), trim(display_name))
  on conflict (id) do nothing;

  insert into security_events (event_type, actor_id, detail)
  values ('advisor_claimed', auth.uid(), jsonb_build_object('display_name', trim(display_name)));
end;
$$;

-- ── respond_to_meeting_proposal: record a real status change ────────────
create or replace function respond_to_meeting_proposal(meeting_id uuid, new_status text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update meeting_proposals
  set status = new_status
  where id = meeting_id and new_status in ('accepted', 'declined');

  if found then
    insert into security_events (event_type, actor_id, detail)
    values ('meeting_responded', auth.uid(), jsonb_build_object('meeting_id', meeting_id, 'new_status', new_status));
  end if;
end;
$$;

-- ── create_review_request: record creation ───────────────────────────────
create or replace function create_review_request(plan_state jsonb, student_label text default null)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_id uuid;
begin
  insert into review_requests (plan_state, student_label)
  values (plan_state, left(student_label, 200))
  returning id into new_id;

  insert into security_events (event_type, actor_id, detail)
  values ('review_request_created', auth.uid(), jsonb_build_object('review_request_id', new_id));

  return new_id;
end;
$$;
