-- Anonymous, free course ratings/reviews. Fully unauthenticated, unlike
-- the advisor workspace: no login, no display name, not even an optional
-- nickname -- a rating carries nothing but a course code, a 1-5 score, and
-- an optional review body. This is a genuinely different trust shape from
-- review_requests: THIS table wants public listing (an average rating and
-- count is the whole point), so a plain RLS SELECT policy + grant works
-- fine here -- the SECURITY DEFINER RPC pattern used elsewhere in this
-- schema is only needed when an anonymous INSERT must echo back the new
-- row's id via PostgREST's return=representation; submitting a rating
-- doesn't need that (fire-and-forget from the client's perspective), so a
-- plain scoped INSERT policy + grant is enough.

create table if not exists course_ratings (
  id uuid primary key default gen_random_uuid(),
  course_code text not null,
  rating smallint not null check (rating between 1 and 5),
  review_body text,
  created_at timestamptz not null default now()
);

alter table course_ratings enable row level security;

create policy "anyone can read ratings"
  on course_ratings for select
  to anon, authenticated
  using (true);

-- Length checks are defense in depth, not the primary guard (there is no
-- primary guard here beyond RLS -- see the soft, client-side-only
-- localStorage "already rated" flag in course-rating.service.ts; this is a
-- deliberate, accepted trade-off matching the app's existing
-- link-is-the-key trust boundaries, not a gap).
create policy "anyone can submit a rating"
  on course_ratings for insert
  to anon, authenticated
  with check (
    rating between 1 and 5
    and length(course_code) between 2 and 20
    and (review_body is null or length(review_body) <= 2000)
  );

grant select, insert on course_ratings to anon, authenticated;

-- Read-side aggregate, computed on every query rather than maintained by a
-- trigger -- can never drift from the underlying rows, and this table is
-- never going to be large enough for that to matter performance-wise.
create or replace view course_rating_summary as
  select
    course_code,
    count(*)::int as rating_count,
    round(avg(rating)::numeric, 2) as average_rating
  from course_ratings
  group by course_code;

grant select on course_rating_summary to anon, authenticated;
