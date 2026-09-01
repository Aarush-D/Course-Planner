import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
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
  imports: [RouterLink],
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

  /** Course cards keyed by code -- lets the checklists below show a real
   * name/credits next to a bare completed/scheduled code, when that course
   * happens to be one of the ones the backend already sent down as a card
   * (flowchart carries completed + recommended). Falls back to just the
   * code if a course isn't on that list (e.g. an older completed course
   * no longer surfaced anywhere else). */
  private readonly courseByCode = computed(() => {
    const map = new Map<string, { name?: string; credits?: number | null }>();
    for (const c of this.planner.coursePlan()?.flowchart ?? []) {
      if (c.id) map.set(c.id.trim().toUpperCase(), { name: c.name, credits: c.credits });
    }
    return map;
  });

  /** Checklist of everything completed so far. */
  completedChecklist = computed(() => {
    const byCode = this.courseByCode();
    return (this.planner.state().completed ?? []).map((code) => {
      const norm = code.trim().toUpperCase();
      return { code: norm, ...byCode.get(norm) };
    });
  });

  /** Checklist of what's coming up next -- reuses scheduledCourseIds (the
   * "Add to schedule" toggle on the Weekly Schedule preview, see
   * weekly-schedule.component.ts) rather than inventing a separate
   * "currently taking" concept: a course a student has marked there IS
   * what they're planning to take next, so it doubles as the natural
   * "currently taking" list here without a new, parallel piece of state. */
  takingNextChecklist = computed(() => {
    const byCode = this.courseByCode();
    return (this.planner.state().scheduledCourseIds ?? []).map((code) => {
      const norm = code.trim().toUpperCase();
      return { code: norm, ...byCode.get(norm) };
    });
  });
}
