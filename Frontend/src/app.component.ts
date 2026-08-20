import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { NavComponent } from './components/nav/nav.component';
import { TourOverlayComponent } from './components/tour-overlay/tour-overlay.component';
import { PlannerStateService } from './services/planner-state.service';
import { TourService } from './services/tour.service';

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, ChatbotComponent, NavComponent, TourOverlayComponent],
})
export class AppComponent implements OnInit {
  readonly planner = inject(PlannerStateService);
  readonly tour = inject(TourService);

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

  startTour() {
    this.helpOpen.set(false);
    this.tour.start();
  }
}
