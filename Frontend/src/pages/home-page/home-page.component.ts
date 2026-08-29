import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlannerStateService } from '../../services/planner-state.service';

@Component({
  selector: 'app-home-page',
  standalone: true,
  templateUrl: './home-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
})
export class HomePageComponent {
  readonly planner = inject(PlannerStateService);

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
