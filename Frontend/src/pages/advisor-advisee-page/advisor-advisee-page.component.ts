import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FlowchartComponent } from '../../components/flowchart/flowchart.component';
import { AdviseePlanRow, AdvisorRosterService } from '../../services/advisor-roster.service';
import { BackendService } from '../../services/backend.service';
import { PlannerState } from '../../services/planner-state.service';
import { AdviseeCommentRow, MeetingRequestRow } from '../../services/supabase.service';
import { SupabaseService } from '../../services/supabase.service';
import { CoursePlan } from '../../models/course-plan.model';
import { toPlannerRequest } from '../../utils/planner-request.util';

/** The advisor's own view of one rostered student -- their live, current
 * plan(s) (read-only, via the same FlowchartComponent pipeline
 * AdvisorReviewPageComponent already uses), a plan picker if they have
 * more than one saved plan, and an ongoing comment thread. Unlike a
 * review request, there's no single snapshot and no "mark reviewed" --
 * this relationship is standing, not one-off. Guarded by advisorAuthGuard;
 * roster membership itself is enforced server-side by get_advisee_plans,
 * not this guard. */
@Component({
  selector: 'app-advisor-advisee-page',
  standalone: true,
  templateUrl: './advisor-advisee-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FlowchartComponent, RouterLink, DatePipe],
})
export class AdvisorAdviseePageComponent {
  private readonly roster = inject(AdvisorRosterService);
  private readonly backend = inject(BackendService);
  private readonly supabase = inject(SupabaseService);

  studentId = input.required<string>();

  loading = signal(true);
  error = signal<string | null>(null);
  plans = signal<AdviseePlanRow[]>([]);
  selectedPlanId = signal<string | null>(null);
  renderedPlan = signal<CoursePlan | null>(null);
  planLoading = signal(false);
  planError = signal<string | null>(null);
  comments = signal<AdviseeCommentRow[]>([]);

  meetings = signal<MeetingRequestRow[]>([]);
  meetingDrafts = signal<Record<string, { at: string; msg: string }>>({});
  respondingMeetingId = signal<string | null>(null);

  commentBody = signal('');
  postingComment = signal(false);
  actionError = signal<string | null>(null);
  private advisorId = computed(() => this.supabase.session()?.user.id ?? null);
  private advisorDisplayName = signal('Advisor');

  selectedPlan = computed(() => this.plans().find((p) => p.id === this.selectedPlanId()) ?? null);
  selectedPlanState = computed<PlannerState | null>(() => this.selectedPlan()?.plan_state ?? null);

  constructor() {
    effect(() => this._load(this.studentId()));
    effect(() => this._loadAdvisorDisplayName(this.advisorId()));
    // Re-render whenever the selected plan changes.
    effect(() => this._renderSelectedPlan());
  }

  selectPlan(planId: string) {
    this.selectedPlanId.set(planId);
  }

  async postComment() {
    const body = this.commentBody().trim();
    const advisorId = this.advisorId();
    if (!body || !advisorId) return;
    this.postingComment.set(true);
    this.actionError.set(null);
    try {
      const posted = await this.roster.postAdviseeComment(
        advisorId,
        this.studentId(),
        'advisor',
        this.advisorDisplayName(),
        body,
      );
      this.commentBody.set('');
      this.comments.update((comments) => [...comments, posted]);
    } catch (e: any) {
      this.actionError.set(e?.message ?? "Couldn’t post that comment. Try again in a moment.");
    } finally {
      this.postingComment.set(false);
    }
  }

  draftFor(id: string): { at: string; msg: string } {
    return this.meetingDrafts()[id] ?? { at: '', msg: '' };
  }

  setDraft(id: string, patch: Partial<{ at: string; msg: string }>) {
    this.meetingDrafts.update((d) => ({ ...d, [id]: { ...this.draftFor(id), ...patch } }));
  }

  async respondToMeeting(m: MeetingRequestRow, status: 'confirmed' | 'declined') {
    const draft = this.draftFor(m.id);
    if (status === 'confirmed' && !draft.at) {
      this.actionError.set('Pick a date and time before confirming.');
      return;
    }
    this.respondingMeetingId.set(m.id);
    this.actionError.set(null);
    try {
      const updated = await this.roster.respondToMeetingRequest(
        m.id,
        status,
        status === 'confirmed' ? new Date(draft.at).toISOString() : undefined,
        draft.msg,
      );
      this.meetings.update((rows) => rows.map((r) => (r.id === m.id ? updated : r)));
    } catch (e: any) {
      this.actionError.set(e?.message ?? "Couldn’t send that reply. Try again in a moment.");
    } finally {
      this.respondingMeetingId.set(null);
    }
  }

  isOwnComment(c: AdviseeCommentRow): boolean {
    return c.author_role === 'advisor' && c.author_name === this.advisorDisplayName();
  }

  private async _loadAdvisorDisplayName(advisorId: string | null) {
    if (!advisorId) return;
    const { data } = await this.supabase.client
      .from('advisor_profiles')
      .select('display_name')
      .eq('id', advisorId)
      .single();
    if (data?.display_name) this.advisorDisplayName.set(data.display_name);
  }

  private async _renderSelectedPlan() {
    const state = this.selectedPlanState();
    this.planError.set(null);
    if (!state) {
      this.renderedPlan.set(null);
      return;
    }
    this.planLoading.set(true);
    try {
      this.renderedPlan.set(await this.backend.plan(toPlannerRequest(state)));
    } catch {
      // Scoped to the plan area on purpose: the planner backend being slow
      // or asleep shouldn't hide the comment thread or meeting requests,
      // which don't depend on it.
      this.renderedPlan.set(null);
      this.planError.set("Couldn’t load this student’s plan right now. Comments and meeting requests below still work.");
    } finally {
      this.planLoading.set(false);
    }
  }

  private async _load(studentId: string) {
    this.loading.set(true);
    this.error.set(null);
    const advisorId = this.advisorId();
    try {
      const [plans, comments, meetings] = await Promise.all([
        this.roster.getAdviseePlans(studentId),
        advisorId ? this.roster.getAdviseeComments(advisorId, studentId) : Promise.resolve([]),
        // A missing table (migration not applied yet) shouldn't take the
        // whole advisee page down with it.
        advisorId
          ? this.roster.listMeetingRequests(advisorId, studentId).catch(() => [] as MeetingRequestRow[])
          : Promise.resolve([] as MeetingRequestRow[]),
      ]);
      if (!plans.length) {
        this.error.set('No plans found for this student — they may not have any saved yet, or are no longer on your roster.');
        return;
      }
      this.plans.set(plans);
      this.selectedPlanId.set(plans[0].id);
      this.comments.set(comments);
      this.meetings.set(meetings);
    } catch {
      this.error.set("Couldn’t load this student. Try again in a moment.");
    } finally {
      this.loading.set(false);
    }
  }
}
