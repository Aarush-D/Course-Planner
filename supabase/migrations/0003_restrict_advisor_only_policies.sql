-- Two fixes surfaced by an extensive testing pass, both against the
-- advisor workspace schema from 0001/0002.
--
-- FIX 1 -- security gap: every "advisors can ..." policy below was written
-- as `to authenticated using (true)`, i.e. "is logged in", not "is actually
-- an advisor". That was safe only because the sole sign-up path in the app
-- was advisor-only. The moment a second, genuinely public sign-up path
-- exists (student accounts), any student's session would satisfy these
-- checks and could list every other student's review request, forge a
-- comment that looks like it came from an advisor, propose a bogus
-- meeting, or load the advisor dashboard routes directly. Confirmed live
-- against the real project (signed up a disposable account with no
-- advisor_profiles row, verified `to authenticated using (true)` doesn't
-- distinguish it from a real advisor). Every policy below is rewritten to
-- require a matching advisor_profiles row, not just a session.
--
-- FIX 2 -- confirmed live bug: the student-facing accept/decline on a
-- meeting proposal (ReviewRequestService.setMeetingStatus, a direct
-- `.update({status}).eq('id', ...)` as the anon role) fails with
-- "permission denied for table meeting_proposals" (Postgres error 42501).
-- meeting_proposals only ever granted `anon` UPDATE, never SELECT, and
-- PostgREST needs SELECT on any column referenced in an UPDATE's WHERE
-- clause. The obvious fix (`grant select ... to anon`) would let anyone
-- list every advisor-student meeting on the platform -- exactly the
-- enumeration risk review_requests' own SELECT policy was deliberately
-- built to avoid. Routed through a SECURITY DEFINER RPC instead, scoped to
-- one id, matching the same pattern 0002 already established for
-- create_review_request.

-- ── is_advisor() ─────────────────────────────────────────────────────────
-- SECURITY DEFINER so the lookup isn't itself subject to the caller's own
-- RLS view of advisor_profiles -- it only ever returns a boolean about the
-- calling user's own id, never any row data, so bypassing RLS here doesn't
-- widen what a caller can actually see.
create or replace function is_advisor()
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (select 1 from advisor_profiles where id = auth.uid());
$$;

grant execute on function is_advisor() to authenticated;

-- ── review_requests: real-advisor-only reads/updates ────────────────────
drop policy if exists "advisors can read all review requests" on review_requests;
create policy "advisors can read all review requests"
  on review_requests for select
  to authenticated
  using (is_advisor());

drop policy if exists "advisors can update review requests" on review_requests;
create policy "advisors can update review requests"
  on review_requests for update
  to authenticated
  using (is_advisor())
  with check (is_advisor());

-- ── plan_comments: real-advisor-only reads; can't forge an advisor post ──
drop policy if exists "advisors can read all comments" on plan_comments;
create policy "advisors can read all comments"
  on plan_comments for select
  to authenticated
  using (is_advisor());

drop policy if exists "scoped comment inserts" on plan_comments;
create policy "scoped comment inserts"
  on plan_comments for insert
  to anon, authenticated
  with check (
    author_role = 'student'
    or (author_role = 'advisor' and is_advisor())
  );

-- ── meeting_proposals: real-advisor-only reads/proposals ────────────────
drop policy if exists "advisors can propose meetings" on meeting_proposals;
create policy "advisors can propose meetings"
  on meeting_proposals for insert
  to authenticated
  with check (advisor_id = auth.uid() and is_advisor());

drop policy if exists "advisors can read all meeting proposals" on meeting_proposals;
create policy "advisors can read all meeting proposals"
  on meeting_proposals for select
  to authenticated
  using (is_advisor());

-- ── meeting_proposals: move the anonymous accept/decline off direct
-- table UPDATE onto a scoped RPC (fixes the confirmed 42501 above without
-- ever granting anon a listable SELECT on this table) ───────────────────
drop policy if exists "anyone can update a meeting proposal's status" on meeting_proposals;
revoke update on meeting_proposals from anon;

create or replace function respond_to_meeting_proposal(meeting_id uuid, new_status text)
returns void
language sql
security definer
set search_path = public
as $$
  update meeting_proposals
  set status = new_status
  where id = meeting_id and new_status in ('accepted', 'declined');
$$;

grant execute on function respond_to_meeting_proposal(uuid, text) to anon, authenticated;
