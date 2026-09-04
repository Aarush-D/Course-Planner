import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Course } from '../../models/course-plan.model';
import { PlannerStateService } from '../../services/planner-state.service';

export type ChecklistStatus = 'done' | 'in_progress' | 'not_taken';
export type ChecklistFilter = 'all' | ChecklistStatus;

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
  // A single-domain Gen Ed slot gets its own "gen_ed:GA"-style category
  // (see _item_category's docstring in Backend/planner_engine.py) -- this
  // flat "gen_ed" entry is what's left over: slots offering a CHOICE of
  // several domains ("GA or GH"), which can't fairly be filed under either
  // domain's own bar until a specific completed course resolves which one.
  gen_ed: 'General education (flexible choice)',
  world_language: 'World language',
  supporting: 'Supporting courses',
  elective: 'Electives',
  other: 'Other requirements',
};

// Real PSU Gen Ed domain names (Backend/data/gen_ed_courses.json's own
// "name" field per domain) -- keeps this label wording in sync with the
// same source of truth the backend's course-picker already reads from.
const GEN_ED_DOMAIN_LABELS: Record<string, string> = {
  GWS: 'Writing/Speaking (GWS)',
  GQ: 'Quantification (GQ)',
  GH: 'Humanities (GH)',
  GA: 'Arts (GA)',
  GS: 'Social and Behavioral Sciences (GS)',
  GN: 'Natural Sciences (GN)',
  GHW: 'Health and Wellness (GHW)',
  'INTER-D': 'Interdomain (INTER-D)',
  IL: 'International Cultures (IL)',
  US: 'United States Cultures (US)',
};

/** Turns a merge_plans-generated key like "minor:STATMIN" or "major:MATH"
 * into "STATMIN minor" / "MATH major" for display, or a backend
 * "gen_ed:GA"-style domain key into its real domain name. */
function _dynamicLabel(key: string): string {
  const [kind, code] = key.split(':');
  if (kind === 'gen_ed' && code) {
    return GEN_ED_DOMAIN_LABELS[code] ?? `General education (${code})`;
  }
  if (code && (kind === 'minor' || kind === 'major')) {
    return `${code} ${kind}`;
  }
  return key;
}

// One color per category so the bars read at a glance instead of every
// requirement type blurring together as the same indigo -- loosely matches
// the color language the Flowchart page already uses for course badges
// (Gen Ed = amber, etc.) where a natural match exists.
// Light-mode shades darkened one step from the obvious -500 choice (and
// "other" two steps) -- at -500 they don't clear the 3:1 non-text-contrast
// minimum against the bg-slate-100 track these fill (confirmed via the
// WCAG relative-luminance formula: e.g. amber-500 on slate-100 is only
// 1.96:1). Dark-mode -400 shades against bg-slate-800 already passed.
const CATEGORY_COLORS: Record<string, string> = {
  major: 'bg-indigo-500 dark:bg-indigo-400',
  gen_ed: 'bg-amber-600 dark:bg-amber-400',
  world_language: 'bg-teal-600 dark:bg-teal-400',
  supporting: 'bg-sky-600 dark:bg-sky-400',
  elective: 'bg-emerald-600 dark:bg-emerald-400',
  other: 'bg-slate-500 dark:bg-slate-500',
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
  // Every gen_ed:XX domain bar shares the flat "gen_ed" bucket's amber --
  // they're the SAME requirement type split into more rows, not different
  // types, so a shared color keeps them reading as one visual group.
  if (kind === 'gen_ed') return CATEGORY_COLORS['gen_ed'];
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
    // appear (e.g. most majors have no "world_language" bucket). Each Gen
    // Ed domain gets its own slot in this order (Foundations first, then
    // Knowledge Domains, then the rest, roughly matching PSU's own Gen Ed
    // grouping) — "gen_ed" itself (the flexible-choice leftover bucket)
    // sits right after them.
    const order = [
      'major',
      'gen_ed:GWS', 'gen_ed:GQ',
      'gen_ed:GH', 'gen_ed:GA', 'gen_ed:GS', 'gen_ed:GN',
      'gen_ed:GHW', 'gen_ed:INTER-D', 'gen_ed:IL', 'gen_ed:US',
      'gen_ed',
      'world_language', 'supporting', 'elective', 'other',
    ];
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

  /** Which of the four tabs above the checklist is active -- filters
   * requirementChecklist() for display only, never recomputes it (the full
   * list is still what every count below is derived from). */
  activeFilter = signal<ChecklistFilter>('all');

  /** One count per tab, computed off the same full list so a tab's number
   * never drifts from what selecting it actually shows. */
  filterCounts = computed(() => {
    const rows = this.requirementChecklist();
    return {
      all: rows.length,
      done: rows.filter((r) => r.status === 'done').length,
      in_progress: rows.filter((r) => r.status === 'in_progress').length,
      not_taken: rows.filter((r) => r.status === 'not_taken').length,
    };
  });

  filteredChecklist = computed(() => {
    const filter = this.activeFilter();
    const rows = this.requirementChecklist();
    return filter === 'all' ? rows : rows.filter((r) => r.status === filter);
  });

  filterLabel(filter: ChecklistFilter): string {
    if (filter === 'all') return 'All';
    if (filter === 'done') return 'Completed';
    if (filter === 'in_progress') return 'In progress';
    return 'Incomplete';
  }

  /** The status circle/bar in the template is aria-hidden (it's a purely
   * visual indicator, redundant with this text once it exists) -- this is
   * that text, exposed via aria-label on the row itself so a screen
   * reader announces status alongside the course code instead of silence. */
  statusLabel(status: ChecklistStatus): string {
    if (status === 'done') return 'Done';
    if (status === 'in_progress') return 'In progress';
    return 'Not yet taken';
  }

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
