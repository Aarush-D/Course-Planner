-- Lets a signed-in student save more than one named plan (e.g. "CMPSC
-- track" vs. "what if I switched to Nursing") instead of exactly one.
-- ALTER, not drop/recreate -- a real student may already have a row under
-- the old user_id-as-primary-key shape, and this must not lose it.
--
-- RLS is unchanged below (still auth.uid() = user_id on every policy) --
-- that check was already correct for "a student can only touch their own
-- rows" regardless of whether user_id is unique, so multiple rows per
-- user just falls out of it for free. Only a genuinely new capability
-- (delete) needs a new policy.

alter table student_plans add column if not exists id uuid not null default gen_random_uuid();
alter table student_plans add column if not exists name text not null default 'My Plan';
alter table student_plans add column if not exists created_at timestamptz not null default now();

-- Swap the primary key from user_id (which forced exactly one row per
-- student) to id -- user_id stays a plain, indexed foreign key.
alter table student_plans drop constraint if exists student_plans_pkey;
alter table student_plans add constraint student_plans_pkey primary key (id);
create index if not exists student_plans_user_id_idx on student_plans (user_id);

-- New capability: deleting one of several plans (the single-plan model
-- never needed this -- there was nothing to delete down to).
create policy "students can delete their own plan"
  on student_plans for delete
  to authenticated
  using (auth.uid() = user_id);

grant delete on student_plans to authenticated;
