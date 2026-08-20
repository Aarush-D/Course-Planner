import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RecommendationsComponent } from '../../components/recommendations/recommendations.component';
import { PlannerStateService } from '../../services/planner-state.service';

@Component({
  selector: 'app-recommendations-page',
  standalone: true,
  templateUrl: './recommendations-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RecommendationsComponent],
})
export class RecommendationsPageComponent {
  readonly planner = inject(PlannerStateService);

  async onMinorAdded(minor: string) {
    const current = this.planner.state();
    if (current.minors.includes(minor)) return;
    await this.planner.onProgramsChanged(current.additionalMajors, [...current.minors, minor]);
  }
}
