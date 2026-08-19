import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { NavComponent } from './components/nav/nav.component';
import { PlannerStateService } from './services/planner-state.service';

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, ChatbotComponent, NavComponent],
})
export class AppComponent implements OnInit {
  readonly planner = inject(PlannerStateService);

  chatOpen = signal(false);
  helpOpen = signal(false);

  async ngOnInit() {
    await this.planner.init();
  }

  toggleChat() {
    this.chatOpen.update((v) => !v);
  }

  toggleHelp() {
    this.helpOpen.update((v) => !v);
  }
}
