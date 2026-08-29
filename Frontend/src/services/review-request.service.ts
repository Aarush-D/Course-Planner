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
   * table insert + .select() doesn't work for an anonymous caller here). */
  async createReviewRequest(planState: PlannerState, studentLabel?: string): Promise<string> {
    const { data, error } = await this.client.rpc('create_review_request', {
      plan_state: planState,
      student_label: studentLabel || null,
    });
    if (error) throw error;
    return data as string;
  }

  /** Student side: fetch one request by id -- via RPC, never a table list. */
  async getReviewRequest(id: string): Promise<ReviewRequestRow | null> {
    const { data, error } = await this.client.rpc('get_review_request', { request_id: id });
    if (error) throw error;
    return (data as ReviewRequestRow) ?? null;
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
   * RLS when the caller is really authenticated). */
  async postComment(reviewRequestId: string, authorRole: 'advisor' | 'student', authorName: string, body: string) {
    const { error } = await this.client
      .from('plan_comments')
      .insert({ review_request_id: reviewRequestId, author_role: authorRole, author_name: authorName, body });
    if (error) throw error;
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

  async proposeMeeting(reviewRequestId: string, advisorId: string, proposedAt: string, note: string) {
    const { error } = await this.client
      .from('meeting_proposals')
      .insert({ review_request_id: reviewRequestId, advisor_id: advisorId, proposed_at: proposedAt, note: note || null });
    if (error) throw error;
  }

  /** Student side: accept/decline -- no login needed, same link-is-the-key
   * trust boundary as everything else in this subsystem. */
  async setMeetingStatus(meetingId: string, status: 'accepted' | 'declined') {
    const { error } = await this.client.from('meeting_proposals').update({ status }).eq('id', meetingId);
    if (error) throw error;
  }
}
