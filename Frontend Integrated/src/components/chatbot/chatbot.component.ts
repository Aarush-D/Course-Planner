import {
  ChangeDetectionStrategy,
  Component,
  effect,
  input,
  output,
  signal,
} from '@angular/core';

export interface PromptPayload {
  dept: string;
  prompt: string;
}

type ChatMessage = { role: 'user' | 'assistant'; text: string };

@Component({
  selector: 'app-chatbot',
  standalone: true,
  templateUrl: './chatbot.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatbotComponent {
  isLoading = input.required<boolean>();
  botReply = input<string | undefined>();

  promptSubmitted = output<PromptPayload>();

  // UI state
  dept = signal<string>('CMPSC');
  prompt = signal<string>('');
  messages = signal<ChatMessage[]>([
    {
      role: 'assistant',
      text: 'Pick your department/major and ask a question (e.g., “What should I take next semester?”).',
    },
  ]);

  private lastBotReply = '';

  constructor() {
    // Whenever parent provides a new bot reply, append it in the chat.
    effect(() => {
      const reply = (this.botReply() || '').trim();
      const loading = this.isLoading();
      if (!loading && reply && reply !== this.lastBotReply) {
        this.lastBotReply = reply;
        this.messages.update((m) => [...m, { role: 'assistant', text: reply }]);
      }
    });
  }

  onSubmit() {
    const p = this.prompt().trim();
    if (!p) return;

    const dept = (this.dept() || 'CMPSC').trim().toUpperCase();

    // append user msg
    this.messages.update((m) => [...m, { role: 'user', text: p }]);

    // clear prompt
    this.prompt.set('');

    this.promptSubmitted.emit({ dept, prompt: p });
  }

  onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.onSubmit();
    }
  }
}
