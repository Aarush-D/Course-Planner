-- Real, self-serve account deletion -- until now, the Privacy Policy
-- promised "you can delete your account any time by contacting us," but
-- that promise was actually unfulfillable for an advisor account with any
-- history: meeting_proposals.advisor_id, security_events.actor_id, and
-- advisor_invite_codes.used_by all reference auth.users(id) with the
-- default foreign-key behavior (NO ACTION), which BLOCKS deleting the
-- referenced row entirely rather than cascading or nulling it out.
-- Concretely: any advisor who had ever proposed a meeting, or who (after
-- migration 0006) claimed an invite code to become an advisor in the
-- first place -- i.e. every advisor created since then -- could not
-- actually have their auth.users row deleted, by anyone, including a
-- team member manually running the SQL the privacy policy describes.
-- Found while auditing the app's actual FERPA-readiness, not from a
-- support request.
--
-- Fixed with ON DELETE SET NULL (not CASCADE) on all three -- deleting an
-- account should anonymize its trace in these tables, not erase them:
-- - meeting_proposals: the record (and the student's side of that
--   conversation) should survive the advisor deleting their account, the
--   same way plan_comments.author_name already persists as plain text
--   uninstead of a live foreign key.
-- - security_events: an audit trail that vanishes when the audited
--   account is deleted defeats its own purpose.
-- - advisor_invite_codes: keeps the "this code was used, and when" record
--   even if the account that used it is later deleted.

alter table meeting_proposals alter column advisor_id drop not null;
alter table meeting_proposals drop constraint if exists meeting_proposals_advisor_id_fkey;
alter table meeting_proposals
  add constraint meeting_proposals_advisor_id_fkey
  foreign key (advisor_id) references auth.users(id) on delete set null;

alter table security_events drop constraint if exists security_events_actor_id_fkey;
alter table security_events
  add constraint security_events_actor_id_fkey
  foreign key (actor_id) references auth.users(id) on delete set null;

alter table advisor_invite_codes drop constraint if exists advisor_invite_codes_used_by_fkey;
alter table advisor_invite_codes
  add constraint advisor_invite_codes_used_by_fkey
  foreign key (used_by) references auth.users(id) on delete set null;

-- ── delete_my_account: the actual self-serve mechanism ───────────────────
-- SECURITY DEFINER so it can delete from auth.users at all (authenticated
-- callers have no direct grant there, by design) -- but it only ever
-- targets auth.uid() itself, so this can't be used to delete anyone else's
-- account regardless of who calls it. advisor_profiles and student_plans
-- both already cascade on delete (see migrations 0001/0005), so this one
-- statement is genuinely sufficient to remove everything tied to the
-- account, now that the three blocking constraints above are fixed.
create or replace function delete_my_account()
returns void
language sql
security definer
set search_path = public
as $$
  delete from auth.users where id = auth.uid();
$$;

grant execute on function delete_my_account() to authenticated;
