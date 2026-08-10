import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { PlannerStateService } from '../../services/planner-state.service';

const CATEGORY_LABELS: Record<string, string> = {
  major: 'Major requirements',
  gen_ed: 'General education',
  world_language: 'World language',
  supporting: 'Supporting courses',
  elective: 'Electives',
  other: 'Other requirements',
};

@Component({
  selector: 'app-progress-page',
  standalone: true,
  templateUrl: './progress-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProgressPageComponent {
  readonly planner = inject(PlannerStateService);

  overallPercent = computed(() => {
    const p = this.planner.coursePlan()?.progress;
    if (!p || !p.totalCredits) return 0;
    return Math.round((100 * p.creditsDone) / p.totalCredits);
  });

  categories = computed(() => {
    const byCategory = this.planner.coursePlan()?.progress?.byCategory;
    if (!byCategory) return [];
    // Fixed, sensible display order — categories with zero items for this
    // major just won't appear (e.g. most majors have no "world_language" bucket).
    const order = ['major', 'gen_ed', 'world_language', 'supporting', 'elective', 'other'];
    return order
      .filter((key) => byCategory[key] && byCategory[key].totalItems > 0)
      .map((key) => ({ key, label: CATEGORY_LABELS[key] ?? key, ...byCategory[key] }));
  });
}
