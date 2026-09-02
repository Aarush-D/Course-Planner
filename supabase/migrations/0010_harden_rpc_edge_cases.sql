-- Follow-up hardening pass, scoped to the narrower set of issues real for
-- this app's actual architecture (stateless Flask + Supabase RLS/RPCs;
-- see the architecture-review note this audit was run against). Three
-- independent fixes, each to a SECURITY DEFINER RPC or a grant left over
-- from an earlier migration -- no new tables, no new features.

-- ── FIX 1: respond_to_meeting_proposal had no status-transition guard ────
-- Found during a TOCTOU/concurrent-write audit. The function itself was
-- already atomic (a single UPDATE, not a separate SELECT-then-UPDATE), so
-- this was never a true race condition -- Postgres' row-level locking
-- serializes two concurrent calls against the same row correctly. But the
-- UPDATE's WHERE clause only checked `id = meeting_id and new_status in
-- ('accepted', 'declined')`, never the row's CURRENT status. That means:
--   - A proposal already declined could later be silently re-accepted (or
--     vice versa) by anyone still holding the link -- a stale browser tab,
--     a replayed request, or a double-click -- with no error, no signal
--     that a decision had already been recorded, and the advisor side
--     would only ever see whichever response happened to land last.
--   - Calling it with a bogus meeting_id or an already-final row was a
--     silent no-op (UPDATE matches zero rows, function still returns void
--     with no error), which the frontend's `if (error) throw error;` can't
--     detect -- it would report success to the student even though nothing
--     changed.
-- Fixed by requiring the row still be in 'proposed' state (a "first real
-- response wins" guard, same shape as claim_advisor_profile's `where
-- used_by is null` -- an atomic single-statement UPDATE, not a new race),
-- and by raising a clear exception on no-op instead of silently returning
-- success. A failed attempt is logged the same way claim_advisor_profile
-- already logs a failed invite-code claim -- repeated attempts to respond
-- to an already-settled meeting_id are exactly the kind of thing this
-- audit trail exists to catch.
create or replace function respond_to_meeting_proposal(meeting_id uuid, new_status text)
returns void
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
    raise exception 'That meeting proposal was already responded to, or no longer exists.';
  end if;

  insert into security_events (event_type, actor_id, detail)
  values ('meeting_responded', auth.uid(), jsonb_build_object('meeting_id', meeting_id, 'new_status', new_status));
end;
$$;

-- ── FIX 2: create_review_request's plan_state had no size bound ─────────
-- Found during the SECURITY DEFINER RPC audit. student_label already got a
-- length guard in 0006 (the exact pattern this fix mirrors), but plan_state
-- -- the actual jsonb payload -- never did. This RPC is called directly
-- from the browser via supabase-js (ReviewRequestService.createReviewRequest
-- -> client.rpc('create_review_request', ...)), never through Flask, so
-- none of Backend/app.py's MAX_CONTENT_LENGTH or rate limits apply to it --
-- the only backstop is whatever this function itself enforces. Anyone
-- holding the public anon key (shipped in the frontend bundle, so anyone)
-- could otherwise call this RPC directly and insert arbitrarily large jsonb
-- blobs, unbounded and unlimited, straight into the database. A real
-- plan_state (the frontend's PlannerState snapshot -- completed courses,
-- settings, a handful of majors/minors) is on the order of a few KB even
-- for an unusually large multi-major plan, so a generous 300KB ceiling
-- costs no legitimate caller anything while closing the storage-abuse
-- vector. Rejected attempts are logged for the same reason FIX 1's are --
-- repeated oversized submissions are a probing signal worth having a
-- record of.
create or replace function create_review_request(plan_state jsonb, student_label text default null)
returns uuid
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
    raise exception 'That plan is too large to submit for review.';
  end if;

  insert into review_requests (plan_state, student_label)
  values (plan_state, left(student_label, 200))
  returning id into new_id;

  insert into security_events (event_type, actor_id, detail)
  values ('review_request_created', auth.uid(), jsonb_build_object('review_request_id', new_id));

  return new_id;
end;
$$;

-- ── FIX 3: stale UPDATE grant on meeting_proposals for `authenticated` ──
-- Found during the RLS completeness audit. 0001 originally granted UPDATE
-- on meeting_proposals to both anon and authenticated, backing a plain RLS
-- policy that let either role update any row. 0003 replaced that policy
-- with respond_to_meeting_proposal (a SECURITY DEFINER RPC) and dropped the
-- permissive policy, but its `revoke update ... from anon` only ever
-- covered the anon half -- the table-level UPDATE grant to `authenticated`
-- was never revoked. In practice this grant has been inert since 0003: RLS
-- has been enabled on this table since 0001, and with the permissive
-- policy gone there has been no UPDATE policy left for `authenticated` to
-- satisfy, so Postgres denies every direct UPDATE regardless of the raw
-- grant (RLS defaults to deny when no policy matches, independent of
-- GRANT). Not an active vulnerability today, but a stale grant that no
-- longer matches the "every write goes through the vetted RPC" design this
-- table otherwise follows -- if a future migration ever added so much as a
-- permissive `using (true)` UPDATE policy for `authenticated` (an easy
-- mistake, and exactly the class of bug 0003 itself was written to fix),
-- this leftover grant would make it immediately exploitable instead of
-- needing its own new grant first. Revoked for the same reason 0002/0003
-- revoked their own now-unneeded grants rather than leaving them in place.
revoke update on meeting_proposals from authenticated;
