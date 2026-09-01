import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Course } from '../../models/course-plan.model';
import { PlannerStateService } from '../../services/planner-state.service';

export type ChecklistStatus = 'done' | 'in_progress' | 'not_taken';

export interface ChecklistRow {
  key: string;
  label: string;
  name?: string | null;
  credits?: number | null;
  categoryKey: string;
  categoryLabel: string;
  categoryColor: string;
  status: ChecklistStatus;
}

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

  /** Every requirement in the degree — not just what's done so far. Two
   * sources cover the whole thing: completed cards off `flowchart` (the
   * ones actually marked done) for the top of the list, then every course
   * `fullPlan` simulates for every remaining term (already resolved to
   * real courses in flowchart order, all the way to graduation) for
   * everything still ahead. A course counts "in progress" when it's been
   * marked via the Weekly Schedule preview's "Add to schedule" toggle
   * (scheduledCourseIds) -- reusing that state rather than inventing a
   * separate "currently taking" concept, since marking a course there IS
   * the student saying that's what they're taking next. */
  requirementChecklist = computed<ChecklistRow[]>(() => {
    const plan = this.planner.coursePlan();
    if (!plan) return [];
    const completedSet = new Set(
      (this.planner.state().completed ?? []).map((c) => c.trim().toUpperCase()),
    );
    const scheduledSet = new Set(
      (this.planner.state().scheduledCourseIds ?? []).map((c) => c.trim().toUpperCase()),
    );

    const rows: ChecklistRow[] = [];

    for (const c of plan.flowchart ?? []) {
      const code = (c.id ?? '').trim().toUpperCase();
      if (!code || !completedSet.has(code)) continue;
      rows.push(this._toRow(c, code, 'done'));
    }

    let slotIndex = 0;
    for (const term of plan.fullPlan?.terms ?? []) {
      for (const c of term.courses ?? []) {
        const code = (c.id ?? '').trim().toUpperCase();
        if (code && completedSet.has(code)) continue; // already listed above
        const status: ChecklistStatus = code && scheduledSet.has(code) ? 'in_progress' : 'not_taken';
        rows.push(this._toRow(c, code || `slot-${slotIndex++}`, status));
      }
    }

    return rows;
  });

  private _toRow(c: Course, key: string, status: ChecklistStatus): ChecklistRow {
    const categoryKey = c.category ?? 'other';
    return {
      key,
      label: c.id || c.name,
      name: c.name,
      credits: c.credits,
      categoryKey,
      // ETM (Entrance-to-Major) is its own label here even though it's
      // filed under the "major" bucket for the by-requirement-type bars
      // above -- those bars intentionally never split ETM out (see
      // _item_category's docstring in planner_engine.py), but a student
      // reading a full course-by-course checklist benefits from knowing
      // exactly which ones gate declaring the major.
      categoryLabel: c.etm ? 'Entrance to Major' : (CATEGORY_LABELS[categoryKey] ?? _dynamicLabel(categoryKey)),
      categoryColor: categoryColor(categoryKey),
      status,
    };
  }
}
