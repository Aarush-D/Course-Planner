-- A direct feedback/bug-report channel for students and advisors: "what's
-- broken, what's missing, what's confusing." Deliberately NOT another
-- admin dashboard -- Aarush's own call when asked ("no dashboard -- I
-- query it directly when asked") is that nothing in the app itself should
-- ever read this back. That makes the trust shape a hybrid of two
-- existing precedents rather than a new one:
--   - like course_ratings (0004): anonymous-friendly, plain scoped INSERT
--     policy + grant, no RPC needed (fire-and-forget, no id echoed back).
--   - like security_events (0007): no SELECT/UPDATE/DELETE grant to any
--     app role at all -- reads only happen by hand, via the Supabase SQL
--     Editor or an MCP-connected session with the project's own
--     credentials, same as that table's "written by the app, read by a
--     human" split.
--
-- Triage status/notes columns exist so a review pass can mark rows done
-- without deleting them (no app role has DELETE either, matching
-- security_events) -- updated directly via SQL during a review, never
-- through the app.
create table if not exists user_feedback (
  id uuid primary key default gen_random_uuid(),
  category text not null check (category in ('bug', 'request', 'other')),
  body text not null check (length(body) between 1 and 4000),
  -- Free text, not an auth-linked email -- this form works with no
  -- session, same as course_ratings' fully-anonymous shape. Purely so a
  -- reply is possible if the submitter wants one; never required.
  contact text check (contact is null or length(contact) <= 200),
  -- Where they were when they hit the issue (e.g. the route path) --
  -- optional context for triage, set by the frontend, never trusted for
  -- anything security-relevant.
  page_context text check (page_context is null or length(page_context) <= 200),
  status text not null default 'new' check (status in ('new', 'reviewed', 'planned', 'done', 'wontfix')),
  triage_notes text,
  created_at timestamptz not null default now()
);

alter table user_feedback enable row level security;

create policy "anyone can submit feedback"
  on user_feedback for insert
  to anon, authenticated
  with check (
    category in ('bug', 'request', 'other')
    and length(body) between 1 and 4000
    and (contact is null or length(contact) <= 200)
    and (page_context is null or length(page_context) <= 200)
  );

-- No select/update/delete grant to anon or authenticated, and no read
-- policy at all -- intentional, per the design note above. This table is
-- write-only from the app's perspective.
grant insert on user_feedback to anon, authenticated;
