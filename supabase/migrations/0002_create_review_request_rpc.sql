-- Fix: an anonymous student couldn't create a review request via a direct
-- INSERT + .select().single() (PostgREST's return=representation needs the
-- base SELECT grant to return the new row's id, regardless of RLS -- and
-- no SELECT policy exists for anon, intentionally, so no one can list
-- every request). Route creation through a SECURITY DEFINER RPC instead,
-- matching the same narrowly-scoped pattern already used for anonymous
-- reads (get_review_request etc.) rather than widening a table-level grant.

drop policy if exists "anyone can create a review request" on review_requests;
revoke insert on review_requests from anon, authenticated;

create or replace function create_review_request(plan_state jsonb, student_label text default null)
returns uuid
language sql
security definer
set search_path = public
as $$
  insert into review_requests (plan_state, student_label) values (plan_state, student_label) returning id;
$$;

grant execute on function create_review_request(jsonb, text) to anon, authenticated;
