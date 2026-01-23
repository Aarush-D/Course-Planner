import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  input,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

interface Message {
  sender: 'user' | 'bot';
  text: string;
}

@Component({
  selector: 'app-chatbot',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './chatbot.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatbotComponent {
  promptSubmitted = output<string>();
  isLoading = input.required<boolean>();

  // ✅ NEW: AI reply comes from parent
  botReply = input<string>('');

  userInput = signal('');
  messages = signal<Message[]>([
    {
      sender: 'bot',
      text:
        "Welcome! Tell me your major and the courses you've taken. I'll help plan your next steps.",
    },
  ]);

  hasUserInput = computed(() => this.userInput().trim().length > 0);

  constructor() {
    // Automatically push AI replies into chat
    effect(() => {
      const reply = this.botReply().trim();
      if (!reply) return;

      const msgs = this.messages();
      const last = msgs[msgs.length - 1];

      // Prevent duplicates
      if (last?.sender === 'bot' && last.text === reply) return;

      this.messages.update((m) => [...m, { sender: 'bot', text: reply }]);
    });
  }

  sendMessage() {
    if (!this.hasUserInput()) return;

    const msg = this.userInput();
    this.messages.update((m) => [...m, { sender: 'user', text: msg }]);
    this.promptSubmitted.emit(msg);
    this.userInput.set('');
  }
}