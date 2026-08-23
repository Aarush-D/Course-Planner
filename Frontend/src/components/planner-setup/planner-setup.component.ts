import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { PlannerStateService } from '../../services/planner-state.service';

type Option = { value: string; label: string };
type OptionGroup = { college: string; options: Option[] };

const MAX_MAJORS = 4;

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
    this.planner.setUndecided(checked);
  }

  onMajorFocus() {
    this.majorQuery.set('');
    this.showMajorDropdown.set(true);
  }

  onMajorBlur() {
    // Deferred so a (mousedown) on a dropdown option still registers before
    // the list disappears — a plain (click) would lose the race to blur.
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
    this.planner.onProgramsChanged(extras, this.planner.state().minors);
  }

  onExtraMajorChange(index: number, value: string) {
    const extras = this.planner.state().additionalMajors.map((s, i) => (i === index ? value : s));
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

  onMinorFocus() {
    this.minorQuery.set('');
    this.showMinorDropdown.set(true);
  }

  onMinorBlur() {
    setTimeout(() => this.showMinorDropdown.set(false), 150);
  }

  toggleMinor(value: string) {
    const chosen = this.planner.state().minors;
    const next = chosen.includes(value) ? chosen.filter((v) => v !== value) : [...chosen, value];
    this.planner.onProgramsChanged(this.planner.state().additionalMajors, next);
  }

  onStartYearChange(value: string) {
    const s = this.planner.state();
    this.planner.onPlanningChanged({
      startYear: Number(value) || this.currentYear,
      gradYears: s.gradYears,
      allowSummer: s.allowSummer,
    });
  }

  onGradYearsChange(value: string) {
    const s = this.planner.state();
    this.planner.onPlanningChanged({
      startYear: s.startYear,
      gradYears: Number(value) || 4,
      allowSummer: s.allowSummer,
    });
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
