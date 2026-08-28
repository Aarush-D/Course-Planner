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

// One color per category so the bars read at a glance instead of every
// requirement type blurring together as the same indigo -- loosely matches
// the color language the Flowchart page already uses for course badges
// (Gen Ed = amber, etc.) where a natural match exists.
const CATEGORY_COLORS: Record<string, string> = {
  major: 'bg-indigo-500 dark:bg-indigo-400',
  gen_ed: 'bg-amber-500 dark:bg-amber-400',
  world_language: 'bg-teal-500 dark:bg-teal-400',
  supporting: 'bg-sky-500 dark:bg-sky-400',
  elective: 'bg-emerald-500 dark:bg-emerald-400',
  other: 'bg-slate-400 dark:bg-slate-500',
};
// A second major or minor gets its own bucket at runtime (see `categories`
// below) -- one shared color per *kind*, since which specific major/minor
// code shows up isn't known ahead of time.
const DYNAMIC_CATEGORY_COLORS: Record<string, string> = {
  minor: 'bg-violet-500 dark:bg-violet-400',
  major: 'bg-rose-500 dark:bg-rose-400',
};
const DEFAULT_CATEGORY_COLOR = 'bg-indigo-500 dark:bg-indigo-400';

function categoryColor(key: string): string {
  if (CATEGORY_COLORS[key]) return CATEGORY_COLORS[key];
  const [kind] = key.split(':');
  return DYNAMIC_CATEGORY_COLORS[kind] ?? DEFAULT_CATEGORY_COLOR;
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
      .map((key) => ({
        key,
        label: CATEGORY_LABELS[key] ?? _dynamicLabel(key),
        color: categoryColor(key),
        ...byCategory[key],
      }));
  });
}
