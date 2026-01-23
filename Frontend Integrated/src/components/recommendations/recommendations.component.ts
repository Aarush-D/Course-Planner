import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { Recommendation } from '../../models/course-plan.model';

@Component({
  selector: 'app-recommendations',
  standalone: true,
  templateUrl: './recommendations.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RecommendationsComponent {
  isLoading = input.required<boolean>();
  recommendations = input<Recommendation[] | null>(null);
  rawText = input<string | null>(null);
}
