-- Follow-up to 0016: that migration fixed claim_advisor_profile's
-- "insert audit row, then raise in the same transaction" bug (see its own
-- comment for the full write-up) and explicitly flagged three more RPCs
-- with the identical shape, out of scope at the time:
--
--   - respond_to_meeting_proposal (0010) -- rejected response (meeting
--     already settled): insert into security_events, then raise.
--   - create_review_request (0010) -- oversized plan_state: insert into
--     security_events, then raise.
--   - join_course_group (0011, most recently touched by 0015): invalid
--     invite code: insert into security_events, then raise. (0015's own
--     trailing comment flags this exact bug too, independently.)
--
-- Same root cause as 0016 in all three: each is called directly from the
-- browser via supabase-js (never through Flask), so the whole RPC call is
-- one top-level statement/transaction. An unhandled RAISE EXCEPTION rolls
-- back everything done earlier in that same function body, including the
-- security_events insert one statement before it -- so the exact
-- rejection attempts this audit trail exists to catch are the ones that
-- never actually land a row.
--
-- Same fix as 0016, applied to each: this project has no autonomous-
-- transaction extension installed (dblink/pg_background -- re-checked via
-- `grep -rniE "extension|dblink|pg_background" supabase/migrations/`, no
-- hits), so there's no way for the callee to partially commit on its own.
-- Instead, each function's rejection branch stops raising, inserts the
-- audit row, and returns a description of the rejection instead -- so its
-- own transaction (audit insert included) always commits. The actual
-- throw moves to the frontend caller, which runs after that transaction
-- has already committed, and throws the exact same message every existing
-- UI caller already expects. Only the branches that previously did
-- "insert audit row, then raise" are touched here -- other raises in
-- join_course_group (not signed in; already in a different group for this
-- course) have no audit insert ahead of them, nothing of value for them to
-- roll back, and are left as plain raises.
--
-- CREATE OR REPLACE FUNCTION can't change a return type/OUT columns, so
-- each function below is dropped and recreated (same restriction 0013,
-- 0015, and 0016 each already hit).

-- ── FIX 1: respond_to_meeting_proposal (void -> text) ────────────────────
-- Single rejection branch, same shape as claim_advisor_profile in 0016:
-- null return means accepted, text means the user-safe rejection reason.
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

grant execute on function respond_to_meeting_proposal(uuid, text) to anon, authenticated;

-- ── FIX 2: create_review_request (uuid -> table(review_request_id, rejection_reason)) ──
-- Unlike claim_advisor_profile/respond_to_meeting_proposal, the success
-- path here has a real payload to hand back (the new row's id), not just
-- "it worked" -- so a single nullable-text return can't carry both. Widened
-- to a two-column table instead: review_request_id is set (and
-- rejection_reason is null) on success, and vice versa on rejection.
drop function if exists create_review_request(jsonb, text);

create function create_review_request(plan_state jsonb, student_label text default null)
returns table(review_request_id uuid, rejection_reason text)
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
    return query select null::uuid, 'That plan is too large to submit for review.';
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

grant execute on function create_review_request(jsonb, text) to anon, authenticated;

-- ── FIX 3: join_course_group (adds a rejection_reason out column) ───────
-- Same "widen the table, add a rejection_reason column" approach as FIX 2
-- above -- the success path already returns a row (group_id, course_code,
-- invite_code), not just a status. Only the invalid-invite-code branch
-- (the one with the audit-insert-then-raise bug) changes to insert+return;
-- the "must be signed in" and "already in a group for this course" raises
-- are untouched -- neither is preceded by a security_events insert, so
-- neither has anything to lose by staying a plain raise. Function body is
-- otherwise byte-for-byte the same as 0015's (including the
-- #variable_conflict use_column pragma that fixed the group_id/
-- course_code/invite_code OUT-parameter ambiguity -- still needed here,
-- and the new rejection_reason OUT param doesn't collide with any column
-- on course_groups/course_group_members so it needs no special handling
-- of its own).
drop function if exists join_course_group(text);

create function join_course_group(p_invite_code text)
returns table(group_id uuid, course_code text, invite_code text, rejection_reason text)
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
    -- audit trail this app has. As of this migration the insert actually
    -- survives the rejection instead of being rolled back with it (see
    -- this file's header comment).
    insert into security_events (event_type, actor_id, detail)
      values ('course_group_join_rejected', v_student, jsonb_build_object('reason', 'invalid_invite_code'));
    return query select null::uuid, null::text, null::text, 'invalid invite code';
    return;
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

  return query select v_group.id, v_group.course_code, v_group.invite_code, null::text;
end;
$$;

grant execute on function join_course_group(text) to authenticated;
