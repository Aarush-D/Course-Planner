-- join_course_group() previously returned only (group_id, course_code),
-- forcing the frontend to immediately re-query course_groups by
-- course_code just to learn the invite_code it already has in hand from
-- the row it just selected internally (see join_course_group's own body
-- below -- v_group is a full course_groups%rowtype, invite_code included,
-- it just wasn't part of the old return list). CourseGroupService.joinGroup
-- now returns it directly instead of the frontend re-deriving it via
-- findMyGroup(), matching what create_course_group already did.
--
-- Postgres won't let CREATE OR REPLACE change a function's return columns,
-- so the old signature has to be dropped first.
drop function if exists join_course_group(text);

create or replace function join_course_group(p_invite_code text)
returns table(group_id uuid, course_code text, invite_code text)
language plpgsql
security definer
set search_path = public
as $$
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
    -- audit trail this app has.
    insert into security_events (event_type, actor_id, detail)
      values ('course_group_join_rejected', v_student, jsonb_build_object('reason', 'invalid_invite_code'));
    raise exception 'invalid invite code';
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

  return query select v_group.id, v_group.course_code, v_group.invite_code;
end;
$$;

grant execute on function join_course_group(text) to authenticated;
