import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AdvisorRosterService } from '../../services/advisor-roster.service';
import { AdviseeCommentRow, SupabaseService } from '../../services/supabase.service';

/** A student's own view of one advisor relationship -- the ongoing
 * comment thread and a reply box. The mirror image of
 * AdvisorAdviseePageComponent's own comment section, minus the plan
 * view (a student already has their own plan on /your-plan; this page
 * exists purely for the conversation). No route guard: like every other
 * student-facing route, it must keep working with no session at all --
 * if the visitor isn't signed in, or isn't actually on this advisor's
 * roster, the underlying queries just come back empty (getAdviseeComments
 * is plain RLS-scoped, so it silently returns nothing rather than
 * erroring, same as every other student-optional page in this app). */
@Component({
  selector: 'app-my-advisor-page',
  standalone: true,
  templateUrl: './my-advisor-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe],
})
export class MyAdvisorPageComponent {
  private readonly roster = inject(AdvisorRosterService);
  private readonly supabase = inject(SupabaseService);

  advisorId = input.required<string>();

  loading = signal(true);
  error = signal<string | null>(null);
  advisorDisplayName = signal<string | null>(null);
  comments = signal<AdviseeCommentRow[]>([]);

  commentBody = signal('');
  postingComment = signal(false);
  actionError = signal<string | null>(null);

  private studentId = computed(() => this.supabase.session()?.user.id ?? null);

  constructor() {
    effect(() => this._load(this.advisorId(), this.studentId()));
  }

  async postComment() {
    const body = this.commentBody().trim();
    const studentId = this.studentId();
    if (!body || !studentId) return;
    this.postingComment.set(true);
    this.actionError.set(null);
    try {
      const posted = await this.roster.postAdviseeComment(this.advisorId(), studentId, 'student', 'You', body);
      this.commentBody.set('');
      this.comments.update((comments) => [...comments, posted]);
    } catch (e: any) {
      this.actionError.set(e?.message ?? "Couldn’t post that comment. Try again in a moment.");
    } finally {
      this.postingComment.set(false);
    }
  }

  isOwnComment(c: AdviseeCommentRow): boolean {
    return c.author_role === 'student';
  }

  private async _load(advisorId: string, studentId: string | null) {
    if (!studentId) {
      this.loading.set(false);
      this.error.set('Sign in to see your conversation with this advisor.');
      return;
    }
    this.loading.set(true);
    this.error.set(null);
    try {
      const [advisors, comments] = await Promise.all([
        this.roster.listMyAdvisors(),
        this.roster.getAdviseeComments(advisorId, studentId),
      ]);
      const advisor = advisors.find((a) => a.advisorId === advisorId);
      if (!advisor) {
        this.error.set("You're not connected with this advisor — they may have removed you from their roster.");
        return;
      }
      this.advisorDisplayName.set(advisor.displayName);
      this.comments.set(comments);
    } catch {
      this.error.set("Couldn’t load this conversation. Try again in a moment.");
    } finally {
      this.loading.set(false);
    }
  }
}
