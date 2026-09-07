-- Fix: a rejected/failed advisor-claim attempt left NO audit row, even
-- though 0006 added security_events specifically to catch this.
--
-- claim_advisor_profile's rejection branch (0006) does this, in order,
-- inside one function body:
--
--   insert into security_events (...) values ('advisor_claim_failed', ...);
--   raise exception 'That invite code is invalid or already used.';
--
-- This RPC is called directly from the browser via supabase-js
-- (SupabaseService.claimAdvisorProfile -> client.rpc('claim_advisor_profile',
-- ...)), so the whole call is one top-level statement, i.e. one
-- transaction. An unhandled RAISE EXCEPTION aborts that transaction --
-- Postgres rolls back everything done inside it, which includes the
-- security_events INSERT that ran earlier in the very same function, one
-- statement before the RAISE. So the one case this audit trail most wants
-- to catch (someone guessing or replaying invite codes) is exactly the one
-- case that never actually lands a row. The success path never hit this,
-- since it has no RAISE after its own audit insert -- confirmed by reading
-- 0006/0007 side by side, this was never tested against a rejected claim.
--
-- (respond_to_meeting_proposal/create_review_request in 0010 and
-- join_course_group in 0011 log-then-raise the exact same way and likely
-- have the identical bug -- out of scope for this migration, which is
-- advisor-claim only; flagged separately for a follow-up.)
--
-- The standard Postgres fix for "commit this insert regardless of what the
-- rest of the transaction does" is an autonomous transaction -- dblink or
-- pg_background, run the audit insert on its own connection so it commits
-- independently before the caller's transaction ever aborts. Checked this
-- project's migrations for an existing autonomous-transaction extension
-- (`grep -rniE "extension|dblink|pg_background" supabase/migrations/`) --
-- none is installed, and this codebase's own style (see 0011/0013/0015's
-- "Logged for the same reason claim_advisor_profile..." comments) already
-- treats "insert an audit row, then reject" as its established pattern
-- elsewhere, so reaching for a new extension (and the security review that
-- installing dblink specifically deserves -- it can open outbound
-- connections) is a bigger footprint than this bug calls for.
--
-- A plain plpgsql function has no way to partially commit on its own --
-- there is no statement that commits just the statements-so-far and keeps
-- running. So instead of trying to make the callee commit early, this
-- moves the actual "reject the claim" step to where a real commit boundary
-- already exists: the caller. claim_advisor_profile itself no longer
-- raises on the rejection path -- it inserts the audit row and returns a
-- plain description of what happened (null = accepted, text = why it was
-- rejected) so its own transaction always commits cleanly, audit insert
-- included. Frontend/src/services/supabase.service.ts's
-- claimAdvisorProfile -- the actual caller of this RPC, running in the
-- browser, entirely outside this function's transaction -- reads that
-- return value and throws there instead, *after* the audit row has already
-- committed. From every existing caller's point of view (the advisor
-- login/signup page, via SupabaseService.claimAdvisorProfile) a rejected
-- claim still throws the same message it always did; only the audit
-- trail's durability changes.
--
-- Changing the return type (void -> text) is not something CREATE OR
-- REPLACE FUNCTION can do -- Postgres requires the old function to be
-- dropped first.
drop function if exists claim_advisor_profile(text, text);

create function claim_advisor_profile(invite_code text, display_name text)
returns text -- null on success; a user-safe rejection message otherwise
language plpgsql
security definer
set search_path = public
as $$
begin
  if display_name is null or length(trim(display_name)) = 0 or length(display_name) > 100 then
    -- Client-side input-validation miss, not a security-relevant event --
    -- no audit row for this branch, matching 0006's original behavior.
    return 'Enter a name between 1 and 100 characters.';
  end if;

  update advisor_invite_codes
  set used_by = auth.uid(), used_at = now()
  where code = invite_code and used_by is null;

  if not found then
    insert into security_events (event_type, actor_id, detail)
    values ('advisor_claim_failed', auth.uid(), jsonb_build_object('invite_code', invite_code));
    return 'That invite code is invalid or already used.';
  end if;

  insert into advisor_profiles (id, display_name) values (auth.uid(), trim(display_name))
  on conflict (id) do nothing;

  insert into security_events (event_type, actor_id, detail)
  values ('advisor_claimed', auth.uid(), jsonb_build_object('display_name', trim(display_name)));

  return null;
end;
$$;

-- DROP FUNCTION wipes any grants along with the old function -- re-grant,
-- same as 0006 originally did.
grant execute on function claim_advisor_profile(text, text) to authenticated;
