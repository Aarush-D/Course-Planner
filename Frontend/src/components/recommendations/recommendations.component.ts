import { ChangeDetectionStrategy, Component, effect, inject, input, output, signal } from '@angular/core';
import { StarRatingComponent } from '../ui/star-rating/star-rating.component';
import { LowCostMinor, NextSemester, Recommendation } from '../../models/course-plan.model';
import { CourseRatingService } from '../../services/course-rating.service';
import { CourseRatingSummaryRow } from '../../services/supabase.service';
import { normalizeCourseCode } from '../../utils/course-code.util';

@Component({
  selector: 'app-recommendations',
  standalone: true,
  templateUrl: './recommendations.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block h-full min-h-0' },
  imports: [StarRatingComponent],
})
export class RecommendationsComponent {
  isLoading = input.required<boolean>();
  recommendations = input<Recommendation[] | null>(null);
  nextSemester = input<NextSemester | null>(null);
  tips = input<string[] | null>(null);
  rawText = input<string | null>(null);
  lowCostMinors = input<LowCostMinor[] | null>(null);

  minorAdded = output<string>();

  private readonly ratings = inject(CourseRatingService);
  private ratingSummaries = signal<Map<string, CourseRatingSummaryRow>>(new Map());

  constructor() {
    effect(() => {
      const codes = (this.recommendations() ?? []).map((r) => r.name).filter(Boolean);
      if (!codes.length) return;
      // See the matching comment in flowchart.component.ts -- ratings are
      // an enhancement, a failed fetch should never surface as an error.
      this.ratings.getSummaries(codes).then((map) => this.ratingSummaries.set(map)).catch(() => {});
    });
  }

  isFlowchartSource(rec: Recommendation): boolean {
    return (rec.source || '').toLowerCase().includes('flowchart');
  }

  ratingSummaryFor(courseCode: string): CourseRatingSummaryRow | undefined {
    return this.ratingSummaries().get(normalizeCourseCode(courseCode));
  }
}
