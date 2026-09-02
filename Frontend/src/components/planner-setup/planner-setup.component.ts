import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { PlannerStateService } from '../../services/planner-state.service';
import { ToastService } from '../../services/toast.service';

type Option = { value: string; label: string };
type OptionGroup = { college: string; options: Option[] };

const MAX_MAJORS = 4;

// This component genuinely mounts twice at once in one real scenario: a
// not-yet-onboarded visitor who navigates straight to /your-plan by URL
// gets both the always-mounted welcome modal AND the Your Plan page's own
// copy in the DOM simultaneously. Static ids (id="planner-campus") would
// collide and break every <label for=...> association for whichever copy
// rendered second -- this counter makes each instance's ids unique instead.
let nextPlannerSetupInstanceId = 0;

/**
 * "Set this once" configuration — campus, major, minors, number of majors,
 * started college, graduate in. Split out of the chat panel because none of
 * it is conversational: a student picks it once at the start, not on every
 * turn. Injects PlannerStateService directly and writes straight through
 * its existing onCampusChanged/onPromptSubmitted/onProgramsChanged/
 * onPlanningChanged methods — no input/output plumbing, since (unlike the
 * chat panel) this component isn't meant to be reusable/standalone, just
 * two different shells around the same fields: permanently in the nav
 * sidebar, and inside the first-visit onboarding modal (see
 * app.component.html).
 */
@Component({
  selector: 'app-planner-setup',
  standalone: true,
  templateUrl: './planner-setup.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PlannerSetupComponent {
  readonly planner = inject(PlannerStateService);
  private readonly toast = inject(ToastService);

  readonly instanceId = `planner-setup-${nextPlannerSetupInstanceId++}`;

  readonly maxMajors = MAX_MAJORS;
  readonly majorCountOptions = Array.from({ length: MAX_MAJORS }, (_, i) => i + 1);
  readonly currentYear = new Date().getFullYear();
  readonly startYearOptions = Array.from({ length: 7 }, (_, i) => this.currentYear - i);

  // Free-text search box state for the major/minor pickers — with ~70+
  // majors, scrolling one giant grouped <select> was too much to read, so
  // this filters to matches as the student types (e.g. "comp" -> Computer
  // Science, Computer Engineering) instead of making them scan everything.
  majorQuery = signal<string>('');
  showMajorDropdown = signal<boolean>(false);
  minorQuery = signal<string>('');
  showMinorDropdown = signal<boolean>(false);

  // One entry per MAJOR, not per (major, year) — the "Started college" year
  // dropdown is the single source of catalog year, so a major with 5
  // historical years shows once here, not 5 times. Uses the most recent
  // year's title per major (title is stable across a major's catalog years
  // in practice; falls back gracefully if it isn't).
  planOptions = computed(() => {
    const plans = this.planner.degreePlans();
    if (!plans.length) {
      return this.planner.state().campus === 'University Park'
        ? [{ value: 'CMPSC', label: 'CMPSC — Computer Science, B.S.' }]
        : [];
    }
    const latestByMajor = new Map<string, { major: string; title: string; catalog_year?: number }>();
    for (const p of plans) {
      const existing = latestByMajor.get(p.major);
      if (!existing || (p.catalog_year ?? 0) > (existing.catalog_year ?? 0)) {
        latestByMajor.set(p.major, p);
      }
    }
    return [...latestByMajor.values()]
      .sort((a, b) => a.major.localeCompare(b.major))
      .map((p) => ({ value: p.major, label: `${p.major} — ${p.title}` }));
  });

  groupedPlanOptions = computed(() => this._groupOptions(this.planOptions()));

  filteredGroupedPlanOptions = computed(() => {
    const query = this.majorQuery().trim().toLowerCase();
    const options = query
      ? this.planOptions().filter(
          (o) => o.label.toLowerCase().includes(query) || o.value.toLowerCase().includes(query),
        )
      : this.planOptions();
    return this._groupOptions(options);
  });

  selectedPlanLabel = computed(() => {
    const value = this.planner.state().major;
    return this.planOptions().find((o) => o.value === value)?.label ?? value;
  });

  minorOptions = computed(() =>
    this.planner
      .minorPlans()
      .slice()
      .sort((a, b) => a.minor.localeCompare(b.minor))
      .map((m) => ({ value: m.minor, label: `${m.minor} — ${m.title}` })),
  );

  groupedMinorOptions = computed(() => this._groupOptions(this.minorOptions()));

  filteredGroupedMinorOptions = computed(() => {
    const query = this.minorQuery().trim().toLowerCase();
    const options = query
      ? this.minorOptions().filter(
          (o) => o.label.toLowerCase().includes(query) || o.value.toLowerCase().includes(query),
        )
      : this.minorOptions();
    return this._groupOptions(options);
  });

  selectedMinorsLabel = computed(() => {
    const chosen = this.planner.state().minors;
    if (!chosen.length) return 'None selected';
    if (chosen.length === 1) {
      return this.minorOptions().find((o) => o.value === chosen[0])?.label ?? chosen[0];
    }
    return `${chosen.length} minors selected`;
  });

  onCampusChange(value: string) {
    this.planner.onCampusChanged(value);
  }

  onUndecidedChange(checked: boolean) {
    // Checking this wipes the current plan plus any extra majors/minors --
    // the checkbox itself shows its own new state, but that side effect
    // (and the fact the picker fields disappear right after) isn't
    // otherwise visible at the moment it happens.
    if (checked && (this.planner.state().additionalMajors.length || this.planner.state().minors.length)) {
      this.toast.show('Extra majors and minors cleared');
    }
    this.planner.setUndecided(checked);
  }

  onMajorFocus() {
    this.majorQuery.set('');
    this.showMajorDropdown.set(true);
  }

  onMajorBlur(event: FocusEvent) {
    // A keyboard user Tabbing FROM the input INTO its own results list
    // fires this blur too -- closing unconditionally 150ms later would
    // unmount the very option they just tabbed onto, dropping focus to
    // <body>. relatedTarget is where focus is actually going; skip the
    // close if that's still inside this field's own wrapper (the mouse
    // path is separately protected by (mousedown) preventDefault on each
    // option button, so this check only needs to cover keyboard focus).
    if (this._focusStayedWithin(event)) return;
    setTimeout(() => this.showMajorDropdown.set(false), 150);
  }

  selectMajor(value: string) {
    this.majorQuery.set('');
    this.showMajorDropdown.set(false);
    // The new primary might already be sitting in an extra-major slot
    // (e.g. swapping "Major" to what was slot 2's pick) — drop it there so
    // no major is ever selected in two slots at once.
    const extras = this.planner.state().additionalMajors;
    if (extras.includes(value)) {
      this.planner.onProgramsChanged(
        extras.map((s) => (s === value ? '' : s)).filter(Boolean),
        this.planner.state().minors,
      );
    }
    // The search box already shows the new selection right where the
    // student is looking, but choosing a major re-plans the whole degree
    // path behind the scenes -- worth a toast given how much that changes.
    this.toast.show(`Major set to ${this._shortTitle(value, this.planOptions())}`);
    // Configuring the major here is a real settings change — re-plan right
    // away rather than waiting for the student to also send a chat message.
    this.planner.onPromptSubmitted({ major: value, prompt: '' });
  }

  onMajorCountChange(value: string) {
    const count = Math.min(Math.max(Number(value) || 1, 1), MAX_MAJORS);
    const wanted = count - 1;
    const current = this.planner.state().additionalMajors;
    const extras =
      current.length === wanted
        ? current
        : current.length > wanted
          ? current.slice(0, wanted)
          : [...current, ...Array(wanted - current.length).fill('')];
    // Shrinking the count silently drops any major already picked in a
    // removed slot -- that's a real (and easy to miss) loss of data, not
    // just a slot-count change, so it gets a toast; growing the count just
    // adds an empty dropdown with nothing to confirm yet.
    const dropped = current.slice(wanted).filter(Boolean);
    if (dropped.length) {
      const titles = dropped.map((v) => this._shortTitle(v, this.planOptions()));
      this.toast.show(`${titles.join(', ')} removed`);
    }
    this.planner.onProgramsChanged(extras, this.planner.state().minors);
  }

  onExtraMajorChange(index: number, value: string) {
    this.extraMajorQuery.set('');
    this.openExtraMajorDropdown.set(null);
    const extras = this.planner.state().additionalMajors.map((s, i) => (i === index ? value : s));
    this.toast.show(
      value ? `Major ${index + 2} set to ${this._shortTitle(value, this.planOptions())}` : `Major ${index + 2} cleared`
    );
    this.planner.onProgramsChanged(extras, this.planner.state().minors);
  }

  /** Grouped options for extra-major slot `index` — excludes the primary
   * major and whatever every OTHER slot currently has picked, so the same
   * major can never appear twice across the major pickers. */
  extraMajorOptionsFor(index: number): OptionGroup[] {
    const primary = this.planner.state().major;
    const extras = this.planner.state().additionalMajors;
    const takenByOthers = new Set(extras.filter((_, i) => i !== index));
    takenByOthers.add(primary);
    return this.groupedPlanOptions()
      .map((g) => ({ college: g.college, options: g.options.filter((o) => !takenByOthers.has(o.value)) }))
      .filter((g) => g.options.length > 0);
  }

  // Search-dropdown state for the extra-major slots -- one shared query +
  // "which slot is open" signal rather than per-slot signals, since only
  // one of these pickers is ever focused at a time (same simplification
  // the minor picker doesn't need, since it's a single field).
  extraMajorQuery = signal('');
  openExtraMajorDropdown = signal<number | null>(null);

  onExtraMajorFocus(index: number) {
    this.extraMajorQuery.set('');
    this.openExtraMajorDropdown.set(index);
  }

  onExtraMajorBlur(event: FocusEvent) {
    if (this._focusStayedWithin(event)) return;
    setTimeout(() => this.openExtraMajorDropdown.set(null), 150);
  }

  selectedExtraMajorLabel(index: number): string {
    const value = this.planner.state().additionalMajors[index];
    if (!value) return '';
    return this.planOptions().find((o) => o.value === value)?.label ?? value;
  }

  filteredExtraMajorOptionsFor(index: number): OptionGroup[] {
    const query = this.extraMajorQuery().trim().toLowerCase();
    if (!query) return this.extraMajorOptionsFor(index);
    return this.extraMajorOptionsFor(index)
      .map((g) => ({
        college: g.college,
        options: g.options.filter((o) => o.label.toLowerCase().includes(query) || o.value.toLowerCase().includes(query)),
      }))
      .filter((g) => g.options.length > 0);
  }

  onMinorFocus() {
    this.minorQuery.set('');
    this.showMinorDropdown.set(true);
  }

  onMinorBlur(event: FocusEvent) {
    if (this._focusStayedWithin(event)) return;
    setTimeout(() => this.showMinorDropdown.set(false), 150);
  }

  /** True if focus is moving somewhere still inside the blurred field's own
   * wrapper (its dropdown results) rather than genuinely leaving it -- see
   * onMajorBlur's comment for why this matters. */
  private _focusStayedWithin(event: FocusEvent): boolean {
    const related = event.relatedTarget as Node | null;
    const container = (event.currentTarget as HTMLElement)?.closest('.relative');
    return !!(related && container?.contains(related));
  }

  toggleMinor(value: string) {
    const chosen = this.planner.state().minors;
    const removing = chosen.includes(value);
    const next = removing ? chosen.filter((v) => v !== value) : [...chosen, value];
    const title = this._shortTitle(value, this.minorOptions());
    this.toast.show(`${title} ${removing ? 'removed' : 'added'}`);
    this.planner.onProgramsChanged(this.planner.state().additionalMajors, next);
  }

  onStartYearChange(value: string) {
    const s = this.planner.state();
    this.planner.onPlanningChanged({
      startYear: Number(value) || this.currentYear,
      gradYears: s.gradYears,
      allowSummer: s.allowSummer,
      maxCreditsPerSemester: s.maxCreditsPerSemester,
    });
  }

  onGradYearsChange(value: string) {
    const s = this.planner.state();
    this.planner.onPlanningChanged({
      startYear: s.startYear,
      gradYears: Number(value) || 4,
      allowSummer: s.allowSummer,
      maxCreditsPerSemester: s.maxCreditsPerSemester,
    });
  }

  /** "CODE — Title (College)" -> "Title" -- the college suffix is useful
   * for grouping in the dropdown list but too long for a one-line toast. */
  private _shortTitle(value: string, options: Option[]): string {
    const label = options.find((o) => o.value === value)?.label;
    return label?.split(' — ')[1]?.replace(/\s*\([^)]*\)\s*$/, '') ?? value;
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
