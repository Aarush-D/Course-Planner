import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { NextSemester, Recommendation } from '../../models/course-plan.model';

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

  isFlowchartSource(rec: Recommendation): boolean {
    return (rec.source || '').toLowerCase().includes('flowchart');
  }
}
