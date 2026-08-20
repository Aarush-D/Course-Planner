import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { LowCostMinor, NextSemester, Recommendation } from '../../models/course-plan.model';

@Component({
  selector: 'app-recommendations',
  standalone: true,
  templateUrl: './recommendations.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block h-full min-h-0' },
})
export class RecommendationsComponent {
  isLoading = input.required<boolean>();
  recommendations = input<Recommendation[] | null>(null);
  nextSemester = input<NextSemester | null>(null);
  tips = input<string[] | null>(null);
  rawText = input<string | null>(null);
  lowCostMinors = input<LowCostMinor[] | null>(null);

  minorAdded = output<string>();

  isFlowchartSource(rec: Recommendation): boolean {
    return (rec.source || '').toLowerCase().includes('flowchart');
  }
}
