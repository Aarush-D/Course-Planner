import { Injectable, inject } from '@angular/core';
import { SupabaseService } from './supabase.service';

export interface CourseGroupSummary {
  groupId: string;
  inviteCode: string;
  memberCount: number;
  enrolledCount: number;
  waitlistedCount: number;
}

/** "Take this course with friends" -- an invite-link-based coordination
 * group per course, deliberately NOT a search/directory feature (this app
 * has no student search/browse surface anywhere, and building one just for
 * this would be a far bigger privacy surface than what was asked for).
 * Membership within a group is visible only to fellow members, shown as an
 * anonymous count + aggregate enrollment status -- no student identity is
 * exposed by this feature (see course_group_members' RLS policy and the
 * migration's own comment for why). */
@Injectable({ providedIn: 'root' })
export class CourseGroupService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  /** Creates a new group for this course and auto-joins the caller as its
   * first member. If the caller wants to reuse an existing group they
   * already made for this course, look it up via findMyGroup() first. */
  async createGroup(courseCode: string): Promise<{ groupId: string; inviteCode: string }> {
    const { data, error } = await this.client
      .rpc('create_course_group', { p_course_code: courseCode })
      .single();
    if (error) throw error;
    const row = data as { group_id: string; invite_code: string };
    return { groupId: row.group_id, inviteCode: row.invite_code };
  }

  /** Idempotent -- joining a group you're already in is a no-op, not an
   * error, so a friend re-clicking a link they already used just lands
   * back in the group normally. */
  async joinGroup(inviteCode: string): Promise<{ groupId: string; courseCode: string }> {
    const { data, error } = await this.client
      .rpc('join_course_group', { p_invite_code: inviteCode })
      .single();
    if (error) throw error;
    const row = data as { group_id: string; course_code: string };
    return { groupId: row.group_id, courseCode: row.course_code };
  }

  async leaveGroup(groupId: string): Promise<void> {
    const { error } = await this.client.rpc('leave_course_group', { p_group_id: groupId });
    if (error) throw error;
  }

  /** The group this course_code has that the signed-in student already
   * belongs to (RLS limits course_groups reads to the caller's own
   * memberships) -- used to show "you + N friends" instead of an
   * Apply-to-join prompt on a course they've already grouped up for.
   * Returns null if they belong to none for this course. The member/
   * enrolled/waitlisted counts come from get_group_status, not a direct
   * course_enrollments query -- that table's RLS only exposes a student's
   * own row, even to fellow group members, by design (see Part A of the
   * seat-pool migration), so the aggregate has to go through that
   * narrowly-scoped RPC instead. */
  async findMyGroup(courseCode: string): Promise<CourseGroupSummary | null> {
    const { data: group, error: groupError } = await this.client
      .from('course_groups')
      .select('id, invite_code')
      .eq('course_code', courseCode)
      .maybeSingle();
    if (groupError) throw groupError;
    if (!group) return null;

    const { data: status, error: statusError } = await this.client
      .rpc('get_group_status', { p_group_id: group.id })
      .single();
    if (statusError) throw statusError;
    const row = status as { member_count: number; enrolled_count: number; waitlisted_count: number };

    return {
      groupId: group.id,
      inviteCode: group.invite_code,
      memberCount: row.member_count,
      enrolledCount: row.enrolled_count,
      waitlistedCount: row.waitlisted_count,
    };
  }
}
