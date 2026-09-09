import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { FlowchartComponent } from '../../components/flowchart/flowchart.component';
import { CoursePlan } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import { PlannerState } from '../../services/planner-state.service';
import { ReviewRequestService } from '../../services/review-request.service';
import { MeetingProposalRow, PlanCommentRow } from '../../services/supabase.service';
import { toPlannerRequest } from '../../utils/planner-request.util';

/**
 * The student's own view of a "Request advisor review" link (`?review=`),
 * parallel to SharedPlanPageComponent's `?shared=` handling but backed by
 * a real Supabase row instead of a client-encoded token, since comments
 * and meeting proposals need something stable to attach to. Never touches
 * PlannerStateService -- fully isolated the same way the read-only share
 * view is (see AppComponent's isReviewView branch).
 */
@Component({
  selector: 'app-review-request-page',
  standalone: true,
  templateUrl: './review-request-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FlowchartComponent, DatePipe],
})
export class ReviewRequestPageComponent {
  private readonly reviewRequests = inject(ReviewRequestService);
  private readonly backend = inject(BackendService);

  id = input.required<string>();

  loading = signal(true);
  error = signal<string | null>(null);
  planState = signal<PlannerState | null>(null);
  plan = signal<CoursePlan | null>(null);
  comments = signal<PlanCommentRow[]>([]);
  meetings = signal<MeetingProposalRow[]>([]);

  replyBody = signal('');
  postingReply = signal(false);

  /** Failure of a reply or a meeting response. Inline (not a toast): this
   * page renders in AppComponent's isolated `?review=` branch, outside the
   * student shell, so the shell's <app-toast> isn't a given here. Cleared
   * at the start of the next action. */
  actionError = signal<string | null>(null);

  /** Brief sr-only aria-live announcement after posting a reply -- cleared
   * shortly after so a repeat post still gets announced. */
  announcement = signal('');

  constructor() {
    effect(() => this._load(this.id()));
  }

  async postReply() {
    const body = this.replyBody().trim();
    if (!body) return;
    this.postingReply.set(true);
    this.actionError.set(null);
    try {
      await this.reviewRequests.postComment(this.id(), 'student', 'You', body);
      this.replyBody.set('');
      this.comments.set(await this.reviewRequests.getComments(this.id()));
      this.announcement.set('Reply posted');
      setTimeout(() => this.announcement.set(''), 1000);
    } catch (e: any) {
      // Previously try/finally only: a rejected RPC reset the busy flag and
      // otherwise vanished -- the reply just silently never appeared.
      this.actionError.set(e?.message ?? "Couldn’t post that reply. Try again in a moment.");
    } finally {
      this.postingReply.set(false);
    }
  }

  async respondToMeeting(meetingId: string, status: 'accepted' | 'declined') {
    this.actionError.set(null);
    try {
      await this.reviewRequests.setMeetingStatus(meetingId, status);
    } catch (e: any) {
      // The RPC deliberately rejects a stale/duplicate response ("already
      // responded to, or no longer exists") -- expected for a double-click
      // or an old tab, and it used to surface as an unhandled rejection
      // with the buttons appearing to do nothing.
      this.actionError.set(e?.message ?? "Couldn’t record that response. Try again in a moment.");
    } finally {
      // Re-fetch either way: on the rejection path this is what replaces
      // the stale Accept/Decline buttons with the already-settled status.
      try {
        this.meetings.set(await this.reviewRequests.getMeetingProposals(this.id()));
      } catch {
        // Leave the list as-is; the action error above is the message that matters.
      }
    }
  }

  private async _load(id: string) {
    this.loading.set(true);
    this.error.set(null);
    try {
      const request = await this.reviewRequests.getReviewRequest(id);
      if (!request) {
        this.error.set('This review request no longer exists.');
        return;
      }
      const state = request.plan_state as PlannerState;
      this.planState.set(state);
      const [plan, comments, meetings] = await Promise.all([
        this.backend.plan(toPlannerRequest(state)),
        this.reviewRequests.getComments(id),
        this.reviewRequests.getMeetingProposals(id),
      ]);
      this.plan.set(plan);
      this.comments.set(comments);
      this.meetings.set(meetings);
    } catch {
      this.error.set("Couldn’t load this review request. Try again in a moment.");
    } finally {
      this.loading.set(false);
    }
  }
}
