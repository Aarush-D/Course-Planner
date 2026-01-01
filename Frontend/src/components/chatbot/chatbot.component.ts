import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

interface Message {
  sender: 'user' | 'bot';
  text: string;
}

@Component({
  selector: 'app-chatbot',
  standalone: true,
  templateUrl: './chatbot.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
})
export class ChatbotComponent {
  promptSubmitted = output<string>();
  isLoading = input.required<boolean>();

  userInput = signal('');
  messages = signal<Message[]>([
    {
      sender: 'bot',
      text:
        "Welcome! Tell me about your major and the courses you've already taken. I'll generate a potential course plan and some recommendations for you.",
    },
  ]);

  hasUserInput = computed(() => this.userInput().trim().length > 0);

  sendMessage() {
    if (!this.hasUserInput()) return;

    const userMessage = this.userInput().trim();
    this.messages.update((msgs) => [...msgs, { sender: 'user', text: userMessage }]);
    this.promptSubmitted.emit(userMessage);
    this.userInput.set('');
  }
}