import { Injectable, inject } from '@angular/core';
import type { PlannerState } from './planner-state.service';
import {
  MeetingProposalRow,
  PlanCommentRow,
  ReviewRequestRow,
  SupabaseService,
} from './supabase.service';

/**
 * All Supabase access for the two-way advisor workspace lives here, kept
 * separate from PlannerStateService -- that service owns the live,
 * client-side plan; this owns the persisted, server-side review-request
 * subsystem. Anonymous (student) reads of a single request go through the
 * SECURITY DEFINER RPCs (see the migration) rather than direct table
 * selects, since a plain RLS policy can't tell "fetch the one row I know
 * the id of" from "list every row".
 */
@Injectable({ providedIn: 'root' })
export class ReviewRequestService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  /** Student side: create a request, no login needed -- via RPC (see
   * supabase/migrations/0002_create_review_request_rpc.sql for why a direct
   * table insert + .select() doesn't work for an anonymous caller here).
   *
   * As of migration 0018 the RPC returns one row of
   * (review_request_id, rejection) instead of a bare uuid, and no longer
   * raises on its audit-logged rejection path (an oversized plan_state):
   * a RAISE would roll back the security_events row it had just inserted.
   * The throw happens here instead, once that row has committed -- same
   * caller shape as SupabaseService.claimAdvisorProfile (0016). */
  async createReviewRequest(planState: PlannerState, studentLabel?: string): Promise<string> {
    const { data, error } = await this.client
      .rpc('create_review_request', {
        plan_state: planState,
        student_label: studentLabel || null,
      })
      .single();
    if (error) throw error;
    const row = data as { review_request_id: string | null; rejection: string | null };
    if (row.rejection) throw new Error(row.rejection);
    return row.review_request_id as string;
  }

  /** Student side: fetch one request by id -- via RPC, never a table list.
   * Resolves to null when no such row exists. `.maybeSingle()` + the id
   * check (rather than trusting a truthy `data`) because until migration
   * 0018 this RPC was declared `returns review_requests` (non-SETOF), which
   * yields ONE all-null row for an unknown id -- a truthy object with a
   * null id -- so `data ?? null` never actually produced null. 0018 makes
   * it `setof` (zero rows -> maybeSingle gives null); the id check keeps
   * this correct against either shape. */
  async getReviewRequest(id: string): Promise<ReviewRequestRow | null> {
    const { data, error } = await this.client.rpc('get_review_request', { request_id: id }).maybeSingle();
    if (error) throw error;
    const row = data as ReviewRequestRow | null;
    return row?.id ? row : null;
  }

  async getComments(reviewRequestId: string): Promise<PlanCommentRow[]> {
    const { data, error } = await this.client.rpc('get_review_request_comments', {
      request_id: reviewRequestId,
    });
    if (error) throw error;
    return (data as PlanCommentRow[]) ?? [];
  }

  async getMeetingProposals(reviewRequestId: string): Promise<MeetingProposalRow[]> {
    const { data, error } = await this.client.rpc('get_review_request_meetings', {
      request_id: reviewRequestId,
    });
    if (error) throw error;
    return (data as MeetingProposalRow[]) ?? [];
  }

  /** Used by both the student reply box (author_role 'student') and the
   * advisor comment box (author_role 'advisor', only actually accepted by
   * RLS when the caller is really authenticated). Deliberately does not
   * .select() its own inserted row back: plan_comments only grants SELECT
   * to `authenticated` (see 0001_advisor_workspace.sql), so that would
   * come back empty for the anonymous student caller. The advisor path has
   * that grant and uses postAdvisorComment() below instead, which can. */
  async postComment(reviewRequestId: string, authorRole: 'advisor' | 'student', authorName: string, body: string) {
    const { error } = await this.client
      .from('plan_comments')
      .insert({ review_request_id: reviewRequestId, author_role: authorRole, author_name: authorName, body });
    if (error) throw error;
  }

  /** Advisor-only counterpart to postComment() above: returns the inserted
   * row directly so the caller can append it locally instead of re-fetching
   * the whole thread via get_review_request_comments(). Safe here (and not
   * in postComment) specifically because this is only ever called by an
   * authenticated advisor, who already has SELECT on plan_comments via the
   * "advisors can read all comments" policy. */
  async postAdvisorComment(reviewRequestId: string, authorName: string, body: string): Promise<PlanCommentRow> {
    const { data, error } = await this.client
      .from('plan_comments')
      .insert({ review_request_id: reviewRequestId, author_role: 'advisor', author_name: authorName, body })
      .select()
      .single();
    if (error) throw error;
    return data as PlanCommentRow;
  }

  async updateStatus(reviewRequestId: string, status: 'pending' | 'reviewed') {
    const { error } = await this.client.from('review_requests').update({ status }).eq('id', reviewRequestId);
    if (error) throw error;
  }

  // ── Advisor-only (RLS requires an authenticated session for these) ─────

  async listPendingRequests(): Promise<ReviewRequestRow[]> {
    const { data, error } = await this.client
      .from('review_requests')
      .select('*')
      .order('created_at', { ascending: false });
    if (error) throw error;
    return (data as ReviewRequestRow[]) ?? [];
  }

  /** Returns the inserted row (advisor-only, same reasoning as
   * postAdvisorComment() above -- meeting_proposals grants SELECT only to
   * `authenticated`, and this is only ever called by a signed-in advisor)
   * so the caller can append it locally instead of re-fetching the whole
   * list via get_review_request_meetings(). */
  async proposeMeeting(
    reviewRequestId: string,
    advisorId: string,
    proposedAt: string,
    note: string,
  ): Promise<MeetingProposalRow> {
    const { data, error } = await this.client
      .from('meeting_proposals')
      .insert({ review_request_id: reviewRequestId, advisor_id: advisorId, proposed_at: proposedAt, note: note || null })
      .select()
      .single();
    if (error) throw error;
    return data as MeetingProposalRow;
  }

  /** Student side: accept/decline -- no login needed, same link-is-the-key
   * trust boundary as everything else in this subsystem. Via RPC, not a
   * direct table update -- see 0003_restrict_advisor_only_policies.sql for
   * why a direct anon UPDATE here fails (PostgREST needs SELECT on the
   * WHERE-clause column, and granting anon a listable SELECT on this table
   * would let anyone enumerate every advisor-student meeting).
   *
   * Throws (message safe to show the student) if the proposal was already
   * responded to or no longer exists. As of migration 0018 the RPC returns
   * that message as text (null on success) instead of raising, so its own
   * security_events row for the rejected attempt survives -- a RAISE in
   * the same transaction rolled it back. Same caller shape as
   * SupabaseService.claimAdvisorProfile (0016). */
  async setMeetingStatus(meetingId: string, status: 'accepted' | 'declined') {
    const { data, error } = await this.client.rpc('respond_to_meeting_proposal', {
      meeting_id: meetingId,
      new_status: status,
    });
    if (error) throw error;
    if (data) throw new Error(data);
  }
}
