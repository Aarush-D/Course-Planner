import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
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

  readonly examplePrompts = [
    "I'm a junior CMPSC major minoring in Math, taken everything except my last year",
    'I just started as a freshman, want to double major in MATH and ECON',
    "I'm a sophomore transferring in with Calc 1 and Calc 2 already done",
  ];

  tryExample(text: string) {
    this.planner.openChatWithPrompt(text);
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
