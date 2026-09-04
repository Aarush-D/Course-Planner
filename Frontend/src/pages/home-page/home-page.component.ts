import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { WeeklyScheduleComponent } from '../../components/weekly-schedule/weekly-schedule.component';
import { PlannerStateService } from '../../services/planner-state.service';
import { SupabaseService } from '../../services/supabase.service';

@Component({
  selector: 'app-home-page',
  standalone: true,
  templateUrl: './home-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, WeeklyScheduleComponent],
})
export class HomePageComponent {
  readonly planner = inject(PlannerStateService);
  private readonly supabase = inject(SupabaseService);

  // Only a real signed-in student account can have a name on file (see
  // signUpStudent in supabase.service.ts) -- an anonymous/no-account plan,
  // a demo student, and a pre-existing account from before this feature
  // shipped all fall through to null here, which keeps the heading as the
  // plain "Welcome back" it always was. Deliberately not threaded through
  // PlannerStateService/demo-student login -- out of scope for this change.
  readonly studentFirstName = computed(() => {
    const meta = this.supabase.session()?.user.user_metadata;
    const name = meta?.['first_name'];
    return typeof name === 'string' && name.trim() ? name.trim() : null;
  });

  // Session-only -- deliberately not persisted, so dismissing the nudge
  // once doesn't silently suppress it forever.
  private readonly nudgeDismissed = signal(false);
  showTranscriptNudge = computed(() => this.planner.transcriptStale() && !this.nudgeDismissed());

  dismissTranscriptNudge() {
    this.nudgeDismissed.set(true);
  }

  openChatForTranscript() {
    this.planner.chatOpen.set(true);
  }

  overallPercent = computed(() => {
    const p = this.planner.coursePlan()?.progress;
    if (!p || !p.totalCredits) return 0;
    return Math.round((100 * p.creditsDone) / p.totalCredits);
  });

  goalMet = computed(() => this.planner.coursePlan()?.fullPlan?.goal?.met ?? null);

  goalDeadline = computed(() => this.planner.coursePlan()?.fullPlan?.goal?.deadline ?? null);

  nextCourses = computed(() => this.planner.coursePlan()?.nextSemester?.courses ?? []);

  degreePlanTitle = computed(() => {
    const major = this.planner.state().major;
    const info = this.planner.degreePlans().find((d) => d.major === major);
    return info?.title ?? major;
  });
}
