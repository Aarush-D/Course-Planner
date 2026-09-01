-- Optional student accounts, purely for persisting a plan across sessions.
-- Additive, not a replacement: the existing anonymous, ephemeral,
-- no-account flow keeps working exactly as before for students who don't
-- create one. This is deliberately NOT the same shape as advisor_profiles
-- -- there is no display name anywhere in the product surface for a
-- student, and course ratings (0004) stay anonymous regardless of whether
-- the submitting browser happens to be signed in here, so there is nothing
-- to store beyond the plan snapshot itself.

create table if not exists student_plans (
  user_id uuid primary key references auth.users(id) on delete cascade,
  plan_state jsonb not null,
  updated_at timestamptz not null default now()
);

alter table student_plans enable row level security;

create policy "students can read their own plan"
  on student_plans for select
  to authenticated
  using (auth.uid() = user_id);

create policy "students can insert their own plan"
  on student_plans for insert
  to authenticated
  with check (auth.uid() = user_id);

create policy "students can update their own plan"
  on student_plans for update
  to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

grant select, insert, update on student_plans to authenticated;
