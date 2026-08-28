import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RecommendationsComponent } from '../../components/recommendations/recommendations.component';
import { PlannerStateService } from '../../services/planner-state.service';
import { ToastService } from '../../services/toast.service';

@Component({
  selector: 'app-recommendations-page',
  standalone: true,
  templateUrl: './recommendations-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RecommendationsComponent],
})
export class RecommendationsPageComponent {
  readonly planner = inject(PlannerStateService);
  private readonly toast = inject(ToastService);

  async onMinorAdded(minor: string) {
    const current = this.planner.state();
    if (current.minors.includes(minor)) return;
    // Strip a trailing "(college)" suffix if the title carries one -- same
    // treatment as the "Your plan" minor toast, too long for a one-line toast.
    const title = (this.planner.coursePlan()?.lowCostMinors?.find((m) => m.minor === minor)?.title ?? minor)
      .replace(/\s*\([^)]*\)\s*$/, '');
    this.toast.show(`${title} added`);
    await this.planner.onProgramsChanged(current.additionalMajors, [...current.minors, minor]);
  }
}
