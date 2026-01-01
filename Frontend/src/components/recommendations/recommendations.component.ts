import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { Recommendation } from '../../models/course-plan.model';

@Component({
  selector: 'app-recommendations',
  templateUrl: './recommendations.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RecommendationsComponent {
  recommendations = input<Recommendation[] | undefined>();
  isLoading = input.required<boolean>();
}
