-- Curated, hand-picked sample section info for a small set of showcase
-- courses (chosen from commonly-recommended CS-major flowchart courses),
-- used to make the Weekly Schedule's "sample section" display feel like a
-- real course listing instead of an obviously-hashed placeholder. This is
-- explicitly NOT real registrar data -- PSU doesn't publish real
-- per-section instructor/room assignments this far from registration (see
-- dummy-schedule.util.ts's own header comment) -- these are illustrative
-- names picked by the app team, not real Penn State faculty.
--
-- Deliberately small and hand-authored rather than generated for every
-- course in the catalog: this only ever *overrides* the deterministic
-- per-course hash fallback in dummy-schedule.util.ts for the specific
-- courses seeded here. A course with no row here still gets a perfectly
-- fine hashed placeholder -- this table exists to make a handful of
-- showcase courses look polished, not to become the catalog's source of
-- truth for every major.
create table if not exists course_showcase_info (
  course_code text primary key,
  professor_name text not null,
  building text not null,
  modality text not null check (modality in ('in_person', 'online', 'hybrid')),
  updated_at timestamptz not null default now()
);

alter table course_showcase_info enable row level security;

-- Same trust shape as course_ratings: public, read-only reference data,
-- no per-user scoping, no writes from the client at all (only ever
-- seeded/updated by a migration or the team directly in Supabase).
create policy "anyone can read course showcase info"
  on course_showcase_info for select
  to anon, authenticated
  using (true);

grant select on course_showcase_info to anon, authenticated;

insert into course_showcase_info (course_code, professor_name, building, modality) values
  ('CMPSC 132', 'Dr. Rachel Ainsley',  'Westgate Building',   'in_person'),
  ('CMPSC 221', 'Dr. Marcus Feldt',    'IST Building',        'in_person'),
  ('CMPSC 222', 'Dr. Priya Nandakumar','IST Building',        'hybrid'),
  ('CMPSC 311', 'Dr. Owen Castellano', 'Westgate Building',   'in_person'),
  ('CMPSC 315', 'Dr. Helena Brooks',   'IST Building',        'in_person'),
  ('CMPSC 316', 'Dr. Tobias Reinholt', 'Thomas Building',     'online'),
  ('CMPSC 320', 'Dr. Alicia Munroe',   'Westgate Building',   'hybrid'),
  ('CMPSC 360', 'Dr. Grant Okafor',    'IST Building',        'in_person'),
  ('CMPSC 461', 'Dr. Simone Dupree',   'Westgate Building',   'in_person'),
  ('CMPSC 465', 'Dr. Elliot Vance',    'IST Building',        'in_person'),
  ('CMPSC 483W','Dr. Naomi Kessler',   'Business Building',   'hybrid'),
  ('CMPEN 270', 'Dr. Farid Al-Amin',   'EE West Building',    'in_person'),
  ('ENGL 202C', 'Dr. Lauren Whitfield','Burrowes Building',   'in_person'),
  ('STAT 318',  'Dr. Yusuf Karimi',    'Thomas Building',     'online')
on conflict (course_code) do update set
  professor_name = excluded.professor_name,
  building = excluded.building,
  modality = excluded.modality,
  updated_at = now();
