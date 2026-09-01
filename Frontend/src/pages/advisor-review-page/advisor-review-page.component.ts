import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FlowchartComponent } from '../../components/flowchart/flowchart.component';
import { PlannerState } from '../../services/planner-state.service';
import { ReviewRequestService } from '../../services/review-request.service';
import { BackendService } from '../../services/backend.service';
import { SupabaseService } from '../../services/supabase.service';
import { CoursePlan } from '../../models/course-plan.model';
import { MeetingProposalRow, PlanCommentRow, ReviewRequestRow } from '../../services/supabase.service';
import { toPlannerRequest } from '../../utils/planner-request.util';

/** The advisor's own view of one review request -- the student's read-only
 * plan (reusing FlowchartComponent the same way SharedPlanPageComponent
 * does), the comment thread with a comment box, and a "Propose a meeting"
 * form. Guarded by advisorAuthGuard. */
@Component({
  selector: 'app-advisor-review-page',
  standalone: true,
  templateUrl: './advisor-review-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FlowchartComponent, RouterLink, DatePipe],
})
export class AdvisorReviewPageComponent {
  private readonly reviewRequests = inject(ReviewRequestService);
  private readonly backend = inject(BackendService);
  private readonly supabase = inject(SupabaseService);

  id = input.required<string>();

  loading = signal(true);
  error = signal<string | null>(null);
  request = signal<ReviewRequestRow | null>(null);
  plan = signal<CoursePlan | null>(null);
  comments = signal<PlanCommentRow[]>([]);
  meetings = signal<MeetingProposalRow[]>([]);

  commentBody = signal('');
  postingComment = signal(false);
  private advisorDisplayName = signal('Advisor');

  meetingDate = signal('');
  meetingTime = signal('');
  meetingNote = signal('');
  proposingMeeting = signal(false);
  meetingError = signal<string | null>(null);

  planState = computed(() => this.request()?.plan_state as PlannerState | null);

  constructor() {
    effect(() => this._load(this.id()));
    // Re-runs if the session populates after construction (the guard
    // already awaited a real session check before allowing navigation
    // here, so in practice this resolves on the first run).
    effect(() => this._loadAdvisorDisplayName(this.supabase.session()?.user.id));
  }

  async postComment() {
    const body = this.commentBody().trim();
    if (!body) return;
    this.postingComment.set(true);
    try {
      await this.reviewRequests.postComment(this.id(), 'advisor', this.advisorDisplayName(), body);
      this.commentBody.set('');
      this.comments.set(await this.reviewRequests.getComments(this.id()));
    } finally {
      this.postingComment.set(false);
    }
  }

  async proposeMeeting() {
    this.meetingError.set(null);
    if (!this.meetingDate() || !this.meetingTime()) {
      this.meetingError.set('Pick a date and time.');
      return;
    }
    const advisorId = this.supabase.session()?.user.id;
    if (!advisorId) return;
    this.proposingMeeting.set(true);
    try {
      const proposedAt = new Date(`${this.meetingDate()}T${this.meetingTime()}`).toISOString();
      await this.reviewRequests.proposeMeeting(this.id(), advisorId, proposedAt, this.meetingNote().trim());
      this.meetingDate.set('');
      this.meetingTime.set('');
      this.meetingNote.set('');
      this.meetings.set(await this.reviewRequests.getMeetingProposals(this.id()));
    } catch (e: any) {
      this.meetingError.set(e?.message ?? 'Could not propose that meeting.');
    } finally {
      this.proposingMeeting.set(false);
    }
  }

  async markReviewed() {
    await this.reviewRequests.updateStatus(this.id(), 'reviewed');
    this.request.update((r) => (r ? { ...r, status: 'reviewed' } : r));
  }

  private async _loadAdvisorDisplayName(userId: string | undefined) {
    if (!userId) return;
    const { data } = await this.supabase.client
      .from('advisor_profiles')
      .select('display_name')
      .eq('id', userId)
      .single();
    if (data?.display_name) this.advisorDisplayName.set(data.display_name);
  }

  private async _load(id: string) {
    this.loading.set(true);
    this.error.set(null);
    try {
      const request = await this.reviewRequests.getReviewRequest(id);
      if (!request) {
        this.error.set('That review request no longer exists.');
        return;
      }
      this.request.set(request);
      const [plan, comments, meetings] = await Promise.all([
        this.backend.plan(toPlannerRequest(request.plan_state as PlannerState)),
        this.reviewRequests.getComments(id),
        this.reviewRequests.getMeetingProposals(id),
      ]);
      this.plan.set(plan);
      this.comments.set(comments);
      this.meetings.set(meetings);
    } catch {
      this.error.set("Couldn't load this review request. Try again in a moment.");
    } finally {
      this.loading.set(false);
    }
  }
}
