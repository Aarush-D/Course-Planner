import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { CoursePlan } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import { PlannerStateService } from '../../services/planner-state.service';
import { ListboxNavigator, buildListboxRows } from '../../utils/listbox-navigation';
import { linkQueryParam } from '../../utils/url-state';
import { toPlannerRequest } from '../../utils/planner-request.util';

type Option = { value: string; label: string };
type OptionGroup = { college: string; options: Option[] };

/**
 * Non-destructive "what if I switched majors" preview -- fetches the
 * current plan and a hypothetical one (major swapped, minor optionally
 * replaced) in parallel via the stateless /api/plan endpoint, entirely
 * client-side. Reads PlannerStateService.state() for the "current" side but
 * never calls any of its mutating methods, so the student's real plan is
 * untouched no matter what they explore here.
 */
@Component({
  selector: 'app-plan-compare',
  standalone: true,
  templateUrl: './plan-compare.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PlanCompareComponent {
  private readonly planner = inject(PlannerStateService);
  private readonly backend = inject(BackendService);

  open = signal(false);
  loading = signal(false);
  error = signal<string | null>(null);
  currentPlan = signal<CoursePlan | null>(null);
  hypotheticalPlan = signal<CoursePlan | null>(null);

  // Search-dropdown state for the hypothetical major -- same pattern as
  // PlannerSetupComponent's own major picker, re-derived at the smaller
  // scale this panel needs rather than reused directly (that component is
  // tightly coupled to live PlannerStateService writes throughout).
  majorQuery = signal('');
  showMajorDropdown = signal(false);
  hypotheticalMajor = signal('');

  constructor() {
    // 'replace': picking a major to compare against is refining this
    // panel, not navigating -- but it IS the one thing worth putting in a
    // link ("look at what switching to CMPSC does to my plan"). Empty
    // string maps to no param at all rather than `?compare=`.
    linkQueryParam({
      key: 'compare',
      signal: this.hypotheticalMajor,
      toParam: (major) => major || null,
      fromParam: (param) => param ?? '',
      history: 'replace',
    });
  }
  hypotheticalMinor = signal('');

  planOptions = computed<Option[]>(() =>
    this.planner
      .degreePlans()
      .slice()
      .sort((a, b) => a.major.localeCompare(b.major))
      .map((p) => ({ value: p.major, label: `${p.major} — ${p.title}` })),
  );

  groupedPlanOptions = computed(() => this._groupOptions(this.planOptions()));

  // Same keyboard gap the planner-setup pickers had -- this combobox
  // declared the ARIA pattern and implemented none of its key handling.
  // See utils/listbox-navigation.ts.
  private readonly majorListbox = computed(() => buildListboxRows(this.filteredGroupedPlanOptions()));
  readonly majorRows = computed(() => this.majorListbox().rows);
  readonly majorNav = new ListboxNavigator<Option>(
    'plan-compare-major',
    () => this.majorListbox().options,
    {
      isOpen: () => this.showMajorDropdown(),
      open: () => this.showMajorDropdown.set(true),
      close: () => this.showMajorDropdown.set(false),
      select: (option) => this.selectHypotheticalMajor(option.value),
    },
  );

  filteredGroupedPlanOptions = computed(() => {
    const query = this.majorQuery().trim().toLowerCase();
    const options = query
      ? this.planOptions().filter(
          (o) => o.label.toLowerCase().includes(query) || o.value.toLowerCase().includes(query),
        )
      : this.planOptions();
    return this._groupOptions(options);
  });

  selectedMajorLabel = computed(() => {
    const value = this.hypotheticalMajor();
    if (!value) return '';
    return this.planOptions().find((o) => o.value === value)?.label ?? value;
  });

  minorOptions = computed(() =>
    this.planner
      .minorPlans()
      .slice()
      .sort((a, b) => a.minor.localeCompare(b.minor))
      .map((m) => ({ value: m.minor, label: `${m.minor} — ${m.title}` })),
  );

  // "You'd need to add" -- course codes the hypothetical plan schedules
  // that the current plan doesn't, computed purely client-side from each
  // plan's own remaining-term course list.
  newlyNeeded = computed<string[]>(() => {
    const current = this.currentPlan();
    const hypothetical = this.hypotheticalPlan();
    if (!current || !hypothetical) return [];
    const codesOf = (p: CoursePlan) =>
      new Set((p.fullPlan?.terms ?? []).flatMap((t) => t.courses).map((c) => c.id.toUpperCase()));
    const currentCodes = codesOf(current);
    return [...codesOf(hypothetical)].filter((c) => !currentCodes.has(c)).sort();
  });

  toggleOpen() {
    this.open.update((v) => !v);
  }

  /** See planner-setup.component.ts's onMajorInput -- same two reasons:
   * typing must reopen a list dismissed with Escape, and the navigator's
   * `open` must not clear the query. */
  onMajorInput(value: string) {
    this.majorQuery.set(value);
    this.showMajorDropdown.set(true);
    this.majorNav.reset();
  }

  onMajorFocus() {
    this.majorQuery.set('');
    this.showMajorDropdown.set(true);
    this.majorNav.reset();
  }

  onMajorBlur(event: FocusEvent) {
    // A keyboard user Tabbing FROM the input INTO its own results list
    // fires this blur too -- closing unconditionally 150ms later would
    // unmount the very option they just tabbed onto, dropping focus to
    // <body>. Same fix as PlannerSetupComponent's own major picker
    // (_focusStayedWithin there) -- skip the close if relatedTarget is
    // still inside this field's own wrapper.
    const related = event.relatedTarget as Node | null;
    const container = (event.currentTarget as HTMLElement)?.closest('.relative');
    if (related && container?.contains(related)) return;
    setTimeout(() => this.showMajorDropdown.set(false), 150);
  }

  selectHypotheticalMajor(value: string) {
    this.hypotheticalMajor.set(value);
    this.majorQuery.set('');
    this.showMajorDropdown.set(false);
    this.majorNav.reset();
  }

  async runCompare() {
    if (!this.hypotheticalMajor()) return;
    this.loading.set(true);
    this.error.set(null);
    try {
      const st = this.planner.state();
      const [current, hypothetical] = await Promise.all([
        this.backend.plan(toPlannerRequest(st)),
        this.backend.plan(
          toPlannerRequest({
            ...st,
            major: this.hypotheticalMajor(),
            // Replaces (not adds to) the current minor list -- "what if
            // this were my only minor" is the simplest, clearest framing
            // for a comparison like this.
            minors: this.hypotheticalMinor() ? [this.hypotheticalMinor()] : st.minors,
          }),
        ),
      ]);
      this.currentPlan.set(current);
      this.hypotheticalPlan.set(hypothetical);
    } catch {
      this.error.set("Couldn’t run that comparison. Try again in a moment.");
    } finally {
      this.loading.set(false);
    }
  }

  creditsRemaining(plan: CoursePlan | null): number | null {
    const p = plan?.progress;
    if (!p) return null;
    return Math.max(0, p.totalCredits - p.creditsDone);
  }

  semesterCount(plan: CoursePlan | null): number | null {
    return plan?.fullPlan?.terms.length ?? null;
  }

  onPace(plan: CoursePlan | null): boolean | null {
    return plan?.fullPlan?.goal?.met ?? null;
  }

  private _groupOptions(options: Option[]): OptionGroup[] {
    const groups = new Map<string, Option[]>();
    for (const opt of options) {
      const college = this._collegeFromLabel(opt.label);
      const bucket = groups.get(college) ?? [];
      bucket.push(opt);
      groups.set(college, bucket);
    }
    return [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([college, options]) => ({ college, options }));
  }

  private _collegeFromLabel(label: string): string {
    const match = label.match(/\(([^)]+)\)\s*$/);
    let college = match?.[1]?.trim() ?? 'Other';
    if (college === 'Engineering') college = 'College of Engineering';
    if (college === 'Intercollege') college = 'Intercollege Programs';
    return college;
  }
}
