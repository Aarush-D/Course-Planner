import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlannerStateService } from '../../services/planner-state.service';

/**
 * Now just the conversational surface — free-text input, message history,
 * and the one setting ("Allow Summer Courses") that genuinely can change
 * turn-to-turn rather than being configured once up front. Campus/Major/
 * Minors/Number-of-majors/Started-college/Graduate-in moved to
 * PlannerSetupComponent (nav sidebar + onboarding modal); message history
 * moved to PlannerStateService.chatMessages so it survives this panel
 * closing and reopening. Injects the service directly instead of the
 * input/output plumbing this used when it also owned the settings —
 * that indirection only earned its keep while there was local state
 * needing to be kept in sync with the backend's echoed-back corrections.
 */
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
  readonly planner = inject(PlannerStateService);

  prompt = signal<string>('');

  private readonly messagesArea =
    viewChild<ElementRef<HTMLDivElement>>('messagesArea');

  constructor() {
    // Home's example-prompt chips (and anything else calling
    // openChatWithPrompt) seed the input via pendingPrompt — consumed once,
    // then cleared so a later close/reopen of this panel doesn't restore it.
    effect(() => {
      const seed = this.planner.pendingPrompt();
      if (!seed) return;
      this.prompt.set(seed);
      this.planner.pendingPrompt.set(undefined);
    });

    // Keep the newest message in view whenever the list grows.
    effect(() => {
      this.planner.chatMessages();
      const el = this.messagesArea()?.nativeElement;
      if (!el) return;
      setTimeout(() => el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }));
    });
  }

  onToggleSummer() {
    const s = this.planner.state();
    this.planner.onPlanningChanged({
      startYear: s.startYear,
      gradYears: s.gradYears,
      allowSummer: !s.allowSummer,
    });
  }

  onSubmit() {
    const p = this.prompt().trim();
    if (p === '' || this.planner.loading() || this.planner.noProgramsForCampus()) return;
    this.prompt.set('');
    this.planner.onPromptSubmitted({ prompt: p });
  }

  onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.onSubmit();
    }
  }

  onClose() {
    this.planner.chatOpen.set(false);
  }
}
