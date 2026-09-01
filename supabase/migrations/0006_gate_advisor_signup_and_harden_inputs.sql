-- Security-audit fixes: one Critical (open advisor self-promotion) and a
-- few input-hardening gaps (comment impersonation, unbounded free text),
-- all found by auditing this app against a general "vibe-coded app"
-- security checklist.

-- ── advisor_invite_codes: gates who can become an advisor ────────────────
-- FIX (Critical): advisor_profiles' only INSERT policy was
-- `to authenticated with check (auth.uid() = id)` -- i.e. "is logged in",
-- with no check on WHO. Students and advisors share the same Supabase Auth
-- user pool (same signUp/signIn calls, no separate identity system), so
-- literally any visitor could get real advisor access two ways: signing up
-- at /advisor/login directly, or even just signing IN there with an
-- existing STUDENT account -- SupabaseService.signInAdvisor also called
-- _ensureAdvisorProfile(), which auto-created the row on first sign-in
-- too, no signup step required. Either path got is_advisor()-gated access
-- to every student's review_requests (including their full plan_state),
-- the ability to post comments tagged as a real advisor, and to propose
-- meetings. Confirmed live: no invite/vetting step existed anywhere in the
-- schema.
--
-- Fixed with a single-use invite-code table + a SECURITY DEFINER RPC,
-- matching this file's own established pattern (create_review_request,
-- respond_to_meeting_proposal) instead of a hardcoded secret in source
-- (which this same audit flags as its own antipattern). Mint a code for a
-- real advisor via the SQL editor, e.g.:
--   insert into advisor_invite_codes (code) values ('some-hard-to-guess-string');
-- RLS is enabled with NO policies granted to anon or authenticated on this
-- table, so a code can only ever be consumed through claim_advisor_profile
-- below -- never listed or enumerated via the API.
create table if not exists advisor_invite_codes (
  code text primary key,
  used_by uuid references auth.users(id),
  used_at timestamptz
);

alter table advisor_invite_codes enable row level security;

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
    raise exception 'That invite code is invalid or already used.';
  end if;

  insert into advisor_profiles (id, display_name) values (auth.uid(), trim(display_name))
  on conflict (id) do nothing;
end;
$$;

grant execute on function claim_advisor_profile(text, text) to authenticated;

-- Direct inserts are no longer how an advisor_profiles row gets created --
-- everything now goes through the vetted RPC above. Existing advisor rows
-- (created before this migration) are untouched and keep working.
drop policy if exists "advisors can insert their own profile" on advisor_profiles;
revoke insert on advisor_profiles from authenticated;

-- ── plan_comments: can no longer forge author_name, and body is bounded ──
-- FIX (High): the insert policy checked author_role but never author_name
-- -- an authenticated (or even anonymous) caller could post
-- {author_role:'student', author_name:'Financial Aid Office', ...} and it
-- would render verbatim in the thread next to real advisor replies
-- (impersonation/phishing vector). Also FIX (Medium): body had no length
-- limit at all, unlike course_ratings.review_body's existing 2000-char cap
-- (0004), so an anonymous caller could insert an arbitrarily large row.
drop policy if exists "scoped comment inserts" on plan_comments;
create policy "scoped comment inserts"
  on plan_comments for insert
  to anon, authenticated
  with check (
    length(body) between 1 and 4000
    and (
      (author_role = 'student' and author_name = 'You')
      or (
        author_role = 'advisor'
        and is_advisor()
        and author_name = (select display_name from advisor_profiles where id = auth.uid())
      )
    )
  );

-- ── meeting_proposals: note is now bounded ────────────────────────────────
-- FIX (Medium): note had no length limit; only a real, is_advisor()-vetted
-- account can insert here at all, so this is a lower-severity version of
-- the same unbounded-input gap as plan_comments.body above.
drop policy if exists "advisors can propose meetings" on meeting_proposals;
create policy "advisors can propose meetings"
  on meeting_proposals for insert
  to authenticated
  with check (
    advisor_id = auth.uid()
    and is_advisor()
    and (note is null or length(note) <= 2000)
  );

-- ── review_requests: student_label is now bounded ────────────────────────
-- FIX (Medium): create_review_request (0002) accepted student_label with
-- no length cap. Clamped rather than rejected -- this field is optional,
-- free-text, and low-stakes, so silently truncating an overlong value is
-- friendlier than failing the whole request creation over it.
create or replace function create_review_request(plan_state jsonb, student_label text default null)
returns uuid
language sql
security definer
set search_path = public
as $$
  insert into review_requests (plan_state, student_label)
  values (plan_state, left(student_label, 200))
  returning id;
$$;
