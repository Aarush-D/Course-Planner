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

/** Turns a merge_plans-generated key like "minor:STATMIN" or "major:MATH"
 * into "STATMIN minor" / "MATH major" for display. */
function _dynamicLabel(key: string): string {
  const [kind, code] = key.split(':');
  if (code && (kind === 'minor' || kind === 'major')) {
    return `${code} ${kind}`;
  }
  return key;
}

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
    // Fixed, sensible display order for the categories every single-major
    // plan can have — categories with zero items for this major just won't
    // appear (e.g. most majors have no "world_language" bucket).
    const order = ['major', 'gen_ed', 'world_language', 'supporting', 'elective', 'other'];
    // A second major or minor adds its own dynamically-named bucket
    // ("major:MATH", "minor:STATMIN", ...) via merge_plans — not in the
    // fixed list above since the code is only known once a program is
    // actually selected. Append any of those, sorted for stability.
    const dynamicKeys = Object.keys(byCategory)
      .filter((key) => !order.includes(key))
      .sort();
    return [...order, ...dynamicKeys]
      .filter((key) => byCategory[key] && byCategory[key].totalItems > 0)
      .map((key) => ({ key, label: CATEGORY_LABELS[key] ?? _dynamicLabel(key), ...byCategory[key] }));
  });
}
