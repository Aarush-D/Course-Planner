import { Injectable, inject } from '@angular/core';
import type { PlannerState } from './planner-state.service';
import { AdviseeCommentRow, AdvisorRosterRow, MeetingRequestRow, SupabaseService } from './supabase.service';

/** One of a rostered student's saved plans, as returned by the
 * get_advisee_plans RPC (setof student_plans) -- includes plan_state,
 * unlike StudentPlanService.listPlans()'s own SavedPlanMeta, since an
 * advisor typically opens the plan right away rather than picking from a
 * bare list first. */
export interface AdviseePlanRow {
  id: string;
  user_id: string;
  name: string;
  plan_state: PlannerState;
  created_at: string;
  updated_at: string;
}

/** All Supabase access for the standing advisor <-> student roster lives
 * here, kept separate from ReviewRequestService (a different subsystem:
 * a one-off snapshot a student submits, not a durable relationship where
 * an advisor can open a student's LIVE plan at any time). See
 * supabase/migrations/0019_advisor_roster.sql for the schema/RLS this
 * relies on -- in particular, why the invite code is reusable and
 * advisor-wide (not per-student, not single-use) and why the comment
 * thread is a new table rather than a repurposed plan_comments. */
@Injectable({ providedIn: 'root' })
export class AdvisorRosterService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  // ── Advisor side ──────────────────────────────────────────────────────

  /** Lazily generates the advisor's roster invite code on first call,
   * idempotent after that (same code every time until regenerated). */
  async getMyInviteCode(): Promise<string> {
    const { data, error } = await this.client.rpc('get_or_create_roster_invite_code');
    if (error) throw error;
    return data as string;
  }

  /** Old code stops matching anything the instant this returns --
   * already-rostered students are unaffected (advisor_rosters rows don't
   * reference the code itself). */
  async regenerateInviteCode(): Promise<string> {
    const { data, error } = await this.client.rpc('regenerate_roster_invite_code');
    if (error) throw error;
    return data as string;
  }

  async listMyRoster(): Promise<AdvisorRosterRow[]> {
    const { data, error } = await this.client
      .from('advisor_rosters')
      .select('*')
      .order('joined_at', { ascending: false });
    if (error) throw error;
    return (data as AdvisorRosterRow[]) ?? [];
  }

  async removeAdvisee(studentId: string): Promise<void> {
    const { error } = await this.client.rpc('remove_advisee_from_roster', { p_student_id: studentId });
    if (error) throw error;
  }

  /** Returns zero rows (not an error) if the caller isn't actually an
   * advisor, or this student isn't on their roster -- see the RPC's own
   * comment for why that ambiguity is intentional. */
  async getAdviseePlans(studentId: string): Promise<AdviseePlanRow[]> {
    const { data, error } = await this.client.rpc('get_advisee_plans', { p_student_id: studentId });
    if (error) throw error;
    return (data as AdviseePlanRow[]) ?? [];
  }

  // ── Student side ──────────────────────────────────────────────────────

  /** Idempotent -- redeeming a code you already redeemed just lands back
   * on that advisor's roster, not an error. As with
   * CourseGroupService.joinGroup, an invalid code comes back as a
   * `rejection` string (the other columns null) rather than a thrown
   * PostgREST error, so the audit-logged rejection branch's
   * security_events row actually commits -- see the RPC's own comment. */
  async joinRoster(inviteCode: string, studentLabel?: string): Promise<{ advisorId: string; advisorDisplayName: string }> {
    const { data, error } = await this.client
      .rpc('join_advisor_roster', { p_invite_code: inviteCode, p_student_label: studentLabel || null })
      .single();
    if (error) throw error;
    const row = data as { advisor_id: string | null; advisor_display_name: string | null; rejection: string | null };
    if (row.rejection) throw new Error(row.rejection);
    return { advisorId: row.advisor_id as string, advisorDisplayName: row.advisor_display_name as string };
  }

  async listMyAdvisors(): Promise<{ advisorId: string; displayName: string; joinedAt: string }[]> {
    const { data, error } = await this.client.rpc('list_my_advisors');
    if (error) throw error;
    return ((data as { advisor_id: string; display_name: string; joined_at: string }[]) ?? []).map((row) => ({
      advisorId: row.advisor_id,
      displayName: row.display_name,
      joinedAt: row.joined_at,
    }));
  }

  async leaveRoster(advisorId: string): Promise<void> {
    const { error } = await this.client.rpc('leave_advisor_roster', { p_advisor_id: advisorId });
    if (error) throw error;
  }

  // ── Shared comment thread (plain RLS-scoped table access -- both sides
  //    always have a real session here, no anonymous-read RPC wrapper
  //    needed the way review_requests' comments need one) ──────────────

  async getAdviseeComments(advisorId: string, studentId: string): Promise<AdviseeCommentRow[]> {
    const { data, error } = await this.client
      .from('advisee_comments')
      .select('*')
      .eq('advisor_id', advisorId)
      .eq('student_id', studentId)
      .order('created_at', { ascending: true });
    if (error) throw error;
    return (data as AdviseeCommentRow[]) ?? [];
  }

  async postAdviseeComment(
    advisorId: string,
    studentId: string,
    authorRole: 'advisor' | 'student',
    authorName: string,
    body: string,
  ): Promise<AdviseeCommentRow> {
    const { data, error } = await this.client
      .from('advisee_comments')
      .insert({ advisor_id: advisorId, student_id: studentId, author_role: authorRole, author_name: authorName, body })
      .select()
      .single();
    if (error) throw error;
    return data as AdviseeCommentRow;
  }

  // ── Meeting requests (see supabase/migrations/0022) ──────────────────

  /** Rows visible to the caller under RLS -- a student's own requests to
   * this advisor, or (for an advisor) that student's requests to them. */
  async listMeetingRequests(advisorId: string, studentId: string): Promise<MeetingRequestRow[]> {
    const { data, error } = await this.client
      .from('advisor_meeting_requests')
      .select('*')
      .eq('advisor_id', advisorId)
      .eq('student_id', studentId)
      .order('created_at', { ascending: false });
    if (error) throw error;
    return (data as MeetingRequestRow[]) ?? [];
  }

  /** Every still-open request addressed to the signed-in advisor, across
   * all their advisees -- backs the dashboard's "needs a reply" card. */
  async listOpenMeetingRequestsForAdvisor(): Promise<MeetingRequestRow[]> {
    const { data, error } = await this.client
      .from('advisor_meeting_requests')
      .select('*')
      .eq('status', 'requested')
      .order('created_at', { ascending: true });
    if (error) throw error;
    return (data as MeetingRequestRow[]) ?? [];
  }

  async requestMeeting(
    advisorId: string,
    studentId: string,
    topic: string,
    note?: string,
    preferredTimes?: string,
  ): Promise<MeetingRequestRow> {
    const { data, error } = await this.client
      .from('advisor_meeting_requests')
      .insert({
        advisor_id: advisorId,
        student_id: studentId,
        topic: topic.trim(),
        note: note?.trim() || null,
        preferred_times: preferredTimes?.trim() || null,
      })
      .select()
      .single();
    if (error) {
      // 23505 = the one-open-request-per-pair unique index (see 0022).
      if ((error as { code?: string }).code === '23505') {
        throw new Error('You already have a pending request with this advisor. Cancel it first to send a new one.');
      }
      throw error;
    }
    return data as MeetingRequestRow;
  }

  async respondToMeetingRequest(
    requestId: string,
    status: 'confirmed' | 'declined',
    confirmedAt?: string,
    response?: string,
  ): Promise<MeetingRequestRow> {
    const { data, error } = await this.client
      .rpc('respond_to_meeting_request', {
        p_request_id: requestId,
        p_status: status,
        p_confirmed_at: confirmedAt ?? null,
        p_response: response ?? null,
      })
      .single();
    if (error) throw error;
    return data as MeetingRequestRow;
  }

  async cancelMeetingRequest(requestId: string): Promise<MeetingRequestRow> {
    const { data, error } = await this.client.rpc('cancel_meeting_request', { p_request_id: requestId }).single();
    if (error) throw error;
    return data as MeetingRequestRow;
  }
}
