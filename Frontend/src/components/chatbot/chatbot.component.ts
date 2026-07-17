import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { DegreePlanInfo, MatchedInfo } from '../../models/course-plan.model';

export interface PromptPayload {
  major?: string;
  prompt: string;
}

export interface PlanningSettings {
  startYear: number;
  gradYears: number;
  allowSummer: boolean;
}

type ChatMessage = { role: 'user' | 'assistant'; text: string };

@Component({
  selector: 'app-chatbot',
  standalone: true,
  templateUrl: './chatbot.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  // Fill the parent panel so the inner messages area gets a real height to scroll in.
  host: { class: 'block h-full min-h-0 overflow-hidden' },
})
export class ChatbotComponent {
  isLoading = input.required<boolean>();
  botReply = input<string | undefined>();
  matched = input<MatchedInfo | undefined>();
  degreePlans = input<DegreePlanInfo[]>([]);
  // Backend-detected state; keeps the dropdown in sync when the student
  // states their major in chat ("I am a CMPSC major") instead of using it.
  activeMajor = input<string | undefined>();
  // Same idea for a chat-stated start year ("oh, I started school in 2022")
  // — the backend can correct start_year/grad_years even when the student
  // never touched these dropdowns, so the dropdowns must reflect it back.
  activeStartYear = input<number | undefined>();
  activeGradYears = input<number | undefined>();

  promptSubmitted = output<PromptPayload>();
  planningChanged = output<PlanningSettings>();

  // UI state — just the major code. Catalog year comes from the "Started
  // college" dropdown / chat-detected start year, not from here.
  selectedPlan = signal<string>('CMPSC');
  prompt = signal<string>('');

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
      return [{ value: 'CMPSC', label: 'CMPSC — Computer Science, B.S.' }];
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

  private readonly messagesArea =
    viewChild<ElementRef<HTMLDivElement>>('messagesArea');

  private lastBotReply = '';

  constructor() {
    // Sync the dropdown with the major the backend detected from chat.
    effect(() => {
      const major = (this.activeMajor() || '').toUpperCase();
      if (!major) return;
      const match = this.planOptions().find((o) => o.value === major);
      if (match && match.value !== this.selectedPlan()) {
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
        parts.push({ role: 'assistant', text: reply });
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
    if (p === '' || this.isLoading()) return;

    this.messages.update((m) => [...m, { role: 'user', text: p }]);
    this.prompt.set('');

    // Catalog year comes from the "Started college" control (or a chat
    // correction), not from this dropdown — see planOptions above.
    this.promptSubmitted.emit({
      major: (this.selectedPlan() || 'CMPSC').toUpperCase(),
      prompt: p,
    });
  }

  onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.onSubmit();
    }
  }
}
