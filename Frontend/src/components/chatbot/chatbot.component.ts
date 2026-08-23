import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { DegreePlanInfo, MatchedInfo, MinorPlanInfo } from '../../models/course-plan.model';

export interface PromptPayload {
  major?: string;
  prompt: string;
  recentReply?: string;
  turnIndex?: number;
}

export interface ProgramsPayload {
  // Every major BEYOND the primary "Major" dropdown — i.e. what a double
  // major sends as one entry, a triple major as two, etc. Never contains
  // duplicates of each other or of the primary major (enforced by the
  // per-slot option filtering below, not just trusted from the caller).
  majors: string[];
  minors: string[];
}

export interface PlanningSettings {
  startYear: number;
  gradYears: number;
  allowSummer: boolean;
}

type ChatMessage = {
  role: 'user' | 'assistant';
  text: string;
  links?: { label: string; route: string }[];
};
type Option = { value: string; label: string };
type OptionGroup = { college: string; options: Option[] };

const MAX_MAJORS = 4;

@Component({
  selector: 'app-chatbot',
  standalone: true,
  templateUrl: './chatbot.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  // Fill the parent panel so the inner messages area gets a real height to scroll in.
  host: { class: 'block h-full min-h-0 overflow-hidden' },
})
export class ChatbotComponent {
  isLoading = input.required<boolean>();
  botReply = input<string | undefined>();
  replyLinks = input<{ label: string; route: string }[] | undefined>();
  matched = input<MatchedInfo | undefined>();
  degreePlans = input<DegreePlanInfo[]>([]);
  minorPlans = input<MinorPlanInfo[]>([]);
  campuses = input<string[]>(['University Park']);
  activeCampus = input<string>('University Park');
  // Backend-detected state; keeps the dropdown in sync when the student
  // states their major in chat ("I am a CMPSC major") instead of using it.
  activeMajor = input<string | undefined>();
  // Same idea for a chat-stated start year ("oh, I started school in 2022")
  // — the backend can correct start_year/grad_years even when the student
  // never touched these dropdowns, so the dropdowns must reflect it back.
  activeStartYear = input<number | undefined>();
  activeGradYears = input<number | undefined>();
  // Same idea again for minors set outside this component entirely (e.g.
  // loginAsDemoStudent seeding a profile's minor) — without this, the
  // panel silently shows "None selected" while the rest of the app (and
  // the backend) already has a minor active.
  activeMinors = input<string[] | undefined>();

  promptSubmitted = output<PromptPayload>();
  planningChanged = output<PlanningSettings>();
  programsChanged = output<ProgramsPayload>();
  campusChanged = output<string>();
  closed = output<void>();

  // One-shot seed for the prompt box — e.g. Home's example-prompt chips
  // open the panel with a suggestion already typed in. Consumed once via
  // the effect below, then the parent clears it back to undefined so a
  // later close/reopen of this panel doesn't restore stale text.
  initialPrompt = input<string | undefined>();
  initialPromptConsumed = output<void>();

  readonly maxMajors = MAX_MAJORS;
  readonly majorCountOptions = Array.from({ length: MAX_MAJORS }, (_, i) => i + 1);

  // UI state — just the major code. Catalog year comes from the "Started
  // college" dropdown / chat-detected start year, not from here.
  selectedPlan = signal<string>('CMPSC');
  prompt = signal<string>('');

  // Double/triple/quadruple major — how many total majors, and the codes
  // for every slot beyond the primary "Major" picker above. A slot's own
  // dropdown options always exclude the primary and every OTHER slot's
  // current pick (see extraMajorOptionsFor), so picking the same major
  // twice — e.g. CMPSC in slot 1 and slot 2 — is never offered as a
  // possibility in the first place, not just rejected after the fact.
  majorCount = signal<number>(1);
  extraMajors = signal<string[]>([]);

  selectedMinors = signal<string[]>([]);

  minorOptions = computed(() =>
    this.minorPlans()
      .slice()
      .sort((a, b) => a.minor.localeCompare(b.minor))
      .map((m) => ({ value: m.minor, label: `${m.minor} — ${m.title}` })),
  );

  // Year planning
  readonly currentYear = new Date().getFullYear();
  readonly startYearOptions = Array.from({ length: 7 }, (_, i) => this.currentYear - i);
  startYear = signal<number>(this.currentYear);
  gradYears = signal<number>(4);
  allowSummer = signal<boolean>(false);
  messages = signal<ChatMessage[]>([
    {
      role: 'assistant',
      text:
        'Hi! Tell me which courses you’ve already taken (e.g. “I took CMPSC 131 and calc 1”) ' +
        'or ask “What should I take next semester?” — I’ll match your courses and plan the rest.',
    },
  ]);

  // One entry per MAJOR, not per (major, year) — the "Started college" year
  // dropdown is the single source of catalog year, so a major with 5
  // historical years shows once here, not 5 times. Uses the most recent
  // year's title per major (title is stable across a major's catalog years
  // in practice; falls back gracefully if it isn't).
  planOptions = computed(() => {
    const plans = this.degreePlans();
    if (!plans.length) {
      // A genuinely empty list only means "the backend fetch hasn't landed
      // yet" when we're on University Park, the campus every plan defaults
      // to — for any other campus it means "no data yet," not a fetch
      // failure, so no fallback option should be offered (see
      // noProgramsForCampus below, which drives the chat panel's real
      // empty-state message).
      return this.activeCampus() === 'University Park'
        ? [{ value: 'CMPSC', label: 'CMPSC — Computer Science, B.S.' }]
        : [];
    }
    const latestByMajor = new Map<string, DegreePlanInfo>();
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

  // Same list, grouped by college so the dropdown shows "Smeal College of
  // Business" / "Eberly College of Science" / etc. as a heading before its
  // majors, instead of one long alphabetical-by-code list a student has to
  // scan through. The college name is the title's trailing parenthetical
  // (every degree_plans/*.json title ends with it, e.g. "Accounting, B.S.
  // (Smeal College of Business)") — normalized so an inconsistent title
  // like "(Engineering)" from an older catalog year still groups with
  // "(College of Engineering)" rather than becoming its own bucket.
  groupedPlanOptions = computed(() => this._groupOptions(this.planOptions()));

  // Free-text search box state for the major picker — with ~70+ majors,
  // scrolling one giant grouped <select> was too much to read, so this
  // filters to matches as the student types (e.g. "comp" -> Computer
  // Science, Computer Engineering) instead of making them scan everything.
  majorQuery = signal<string>('');
  showMajorDropdown = signal<boolean>(false);

  selectedPlanLabel = computed(() => {
    const value = this.selectedPlan();
    return this.planOptions().find((o) => o.value === value)?.label ?? value;
  });

  // True only when the campus itself has zero majors — distinct from a
  // still-loading fetch, which planOptions handles by falling back to a
  // placeholder CMPSC option on University Park specifically (see above).
  noProgramsForCampus = computed(
    () => this.activeCampus() !== 'University Park' && this.degreePlans().length === 0,
  );

  onCampusChange(value: string) {
    this.campusChanged.emit(value);
  }

  filteredGroupedPlanOptions = computed(() => {
    const query = this.majorQuery().trim().toLowerCase();
    const options = query
      ? this.planOptions().filter(
          (o) => o.label.toLowerCase().includes(query) || o.value.toLowerCase().includes(query),
        )
      : this.planOptions();
    return this._groupOptions(options);
  });

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
    this.selectedPlan.set(value);
    this.majorQuery.set('');
    this.showMajorDropdown.set(false);
    // The new primary might already be sitting in an extra-major slot
    // (e.g. swapping "Major" to what was slot 2's pick) — drop it there so
    // no major is ever selected in two slots at once.
    if (this.extraMajors().includes(value)) {
      this.extraMajors.update((slots) => slots.map((s) => (s === value ? '' : s)));
      this._emitPrograms();
    }
  }

  // --- Number of majors / extra major slots -------------------------------

  onMajorCountChange(value: string) {
    const count = Math.min(Math.max(Number(value) || 1, 1), MAX_MAJORS);
    this.majorCount.set(count);
    const wanted = count - 1;
    this.extraMajors.update((slots) => {
      if (slots.length === wanted) return slots;
      if (slots.length > wanted) return slots.slice(0, wanted);
      return [...slots, ...Array(wanted - slots.length).fill('')];
    });
    this._emitPrograms();
  }

  onExtraMajorChange(index: number, value: string) {
    this.extraMajors.update((slots) => slots.map((s, i) => (i === index ? value : s)));
    this._emitPrograms();
  }

  /** Grouped options for extra-major slot `index` — excludes the primary
   * major and whatever every OTHER slot currently has picked, so the same
   * major can never appear twice across the major pickers. */
  extraMajorOptionsFor(index: number): OptionGroup[] {
    const primary = this.selectedPlan();
    const takenByOthers = new Set(this.extraMajors().filter((_, i) => i !== index));
    takenByOthers.add(primary);
    return this.groupedPlanOptions()
      .map((g) => ({ college: g.college, options: g.options.filter((o) => !takenByOthers.has(o.value)) }))
      .filter((g) => g.options.length > 0);
  }

  // --- Minors: same searchable, grouped-dropdown style as Major, but a
  // multi-select — clicking an option toggles it in/out instead of closing
  // the panel, and the collapsed display summarizes the current picks. ---

  minorQuery = signal<string>('');
  showMinorDropdown = signal<boolean>(false);

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
    const chosen = this.selectedMinors();
    if (!chosen.length) return 'None selected';
    if (chosen.length === 1) {
      return this.minorOptions().find((o) => o.value === chosen[0])?.label ?? chosen[0];
    }
    return `${chosen.length} minors selected`;
  });

  onMinorFocus() {
    this.minorQuery.set('');
    this.showMinorDropdown.set(true);
  }

  onMinorBlur() {
    setTimeout(() => this.showMinorDropdown.set(false), 150);
  }

  toggleMinor(value: string) {
    this.selectedMinors.update((chosen) =>
      chosen.includes(value) ? chosen.filter((v) => v !== value) : [...chosen, value],
    );
    this._emitPrograms();
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

  private readonly messagesArea =
    viewChild<ElementRef<HTMLDivElement>>('messagesArea');

  private lastBotReply = '';

  constructor() {
    effect(() => {
      const seed = this.initialPrompt();
      if (!seed) return;
      this.prompt.set(seed);
      this.initialPromptConsumed.emit();
    });

    // Sync the dropdown with the major the backend detected from chat. Reads
    // selectedPlan() via untracked() — otherwise this effect re-subscribes to
    // its own write target, and re-fires the instant a user manually picks a
    // different major, immediately stomping their pick back to whatever
    // activeMajor (the last backend response) still says.
    effect(() => {
      const major = (this.activeMajor() || '').toUpperCase();
      if (!major) return;
      const match = this.planOptions().find((o) => o.value === major);
      if (match && match.value !== untracked(this.selectedPlan)) {
        this.selectedPlan.set(match.value);
      }
    });

    // Sync "Started college" / "Graduate in" with a chat-stated correction
    // ("oh, I started school in 2022") — these dropdowns are local UI state,
    // so without this they'd silently drift from what the backend actually used.
    effect(() => {
      const year = this.activeStartYear();
      if (year !== undefined && year !== this.startYear()) {
        this.startYear.set(year);
      }
    });
    effect(() => {
      const years = this.activeGradYears();
      if (years !== undefined && years !== this.gradYears()) {
        this.gradYears.set(years);
      }
    });

    // Same pattern for minors: only reacts to activeMinors changing (read
    // selectedMinors via untracked so this doesn't re-fire the instant the
    // student toggles a minor chip themselves).
    effect(() => {
      const minors = this.activeMinors();
      if (minors === undefined) return;
      const current = untracked(this.selectedMinors);
      const same = minors.length === current.length && minors.every((m) => current.includes(m));
      if (!same) {
        this.selectedMinors.set([...minors]);
      }
    });

    // Keep the newest message in view whenever the list grows.
    effect(() => {
      this.messages();
      const el = this.messagesArea()?.nativeElement;
      if (!el) return;
      setTimeout(() => el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }));
    });

    // Whenever parent provides a new bot reply, append it in the chat.
    effect(() => {
      const reply = (this.botReply() || '').trim();
      const loading = this.isLoading();
      if (!loading && reply && reply !== this.lastBotReply) {
        this.lastBotReply = reply;
        const m = this.matched();
        const parts: ChatMessage[] = [];
        if (m && m.courses.length && m.treatedAsCompleted) {
          parts.push({
            role: 'assistant',
            text:
              '✓ Matched your courses: ' +
              m.courses.map((c) => `${c.code} (${c.name})`).join(', '),
          });
        }
        if (m && m.removed?.length) {
          parts.push({
            role: 'assistant',
            text:
              '➖ Removed from completed: ' +
              m.removed.map((c) => `${c.code} (${c.name})`).join(', '),
          });
        }
        if (m && m.summerUnavailable?.length) {
          parts.push({
            role: 'assistant',
            text:
              '☀️ Noted as not offered in summer — plan adjusted: ' +
              m.summerUnavailable.map((c) => c.code).join(', '),
          });
        }
        if (m && m.unmatched.length) {
          parts.push({
            role: 'assistant',
            text: '⚠ Couldn’t match: ' + m.unmatched.join(', '),
          });
        }
        parts.push({ role: 'assistant', text: reply, links: this.replyLinks() });
        this.messages.update((msgs) => [...msgs, ...parts]);
      }
    });
  }

  /** Year-planning controls: any change re-plans immediately. */
  onStartYearChange(value: string) {
    this.startYear.set(Number(value) || this.currentYear);
    this._emitPlanning();
  }

  onGradYearsChange(value: string) {
    this.gradYears.set(Number(value) || 4);
    this._emitPlanning();
  }

  onToggleSummer() {
    this.allowSummer.update((v) => !v);
    this._emitPlanning();
  }

  private _emitPrograms() {
    if (this.isLoading()) return;
    this.programsChanged.emit({
      majors: this.extraMajors().filter(Boolean),
      minors: this.selectedMinors(),
    });
  }

  private _emitPlanning() {
    if (this.isLoading()) return;
    this.planningChanged.emit({
      startYear: this.startYear(),
      gradYears: this.gradYears(),
      allowSummer: this.allowSummer(),
    });
  }

  onSubmit() {
    const p = this.prompt().trim();
    if (p === '' || this.isLoading() || this.noProgramsForCampus()) return;

    // Captured before appending this turn's own message, so the backend can
    // vary its reply's opener instead of repeating whatever it said last —
    // and knows how many turns precede this one.
    const priorMessages = this.messages();
    const lastAssistantReply = [...priorMessages].reverse().find((m) => m.role === 'assistant')?.text;
    const turnIndex = priorMessages.filter((m) => m.role === 'user').length;

    this.messages.update((m) => [...m, { role: 'user', text: p }]);
    this.prompt.set('');

    // Catalog year comes from the "Started college" control (or a chat
    // correction), not from this dropdown — see planOptions above.
    this.promptSubmitted.emit({
      major: (this.selectedPlan() || 'CMPSC').toUpperCase(),
      prompt: p,
      recentReply: lastAssistantReply?.slice(0, 400),
      turnIndex,
    });
  }

  onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.onSubmit();
    }
  }

  onClose() {
    this.closed.emit();
  }
}
