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
  catalogYear?: number;
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
  activeCatalogYear = input<number | undefined>();

  promptSubmitted = output<PromptPayload>();
  planningChanged = output<PlanningSettings>();

  // UI state — value encodes "MAJOR|YEAR"
  selectedPlan = signal<string>('CMPSC|');
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

  planOptions = computed(() => {
    const plans = this.degreePlans();
    if (!plans.length) {
      return [{ value: 'CMPSC|', label: 'CMPSC (Computer Science)' }];
    }
    return plans.map((p) => ({
      value: `${p.major}|${p.catalog_year ?? ''}`,
      label: `${p.major} — ${p.title} (${p.catalog_year})`,
    }));
  });

  private readonly messagesArea =
    viewChild<ElementRef<HTMLDivElement>>('messagesArea');

  private lastBotReply = '';

  constructor() {
    // Sync the dropdown with the major the backend detected from chat.
    effect(() => {
      const major = (this.activeMajor() || '').toUpperCase();
      if (!major) return;
      const year = this.activeCatalogYear();
      const match = this.planOptions().find((o) => {
        const [m, y] = o.value.split('|');
        return m === major && (year === undefined || String(year) === y);
      });
      if (match && match.value !== this.selectedPlan()) {
        this.selectedPlan.set(match.value);
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

    const [major, year] = this.selectedPlan().split('|');

    this.messages.update((m) => [...m, { role: 'user', text: p }]);
    this.prompt.set('');

    this.promptSubmitted.emit({
      major: (major || 'CMPSC').toUpperCase(),
      catalogYear: year ? Number(year) : undefined,
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
