import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { NavComponent } from './components/nav/nav.component';
import { PlannerSetupComponent } from './components/planner-setup/planner-setup.component';
import { TourOverlayComponent } from './components/tour-overlay/tour-overlay.component';
import { PlannerStateService } from './services/planner-state.service';
import { TourService } from './services/tour.service';

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, ChatbotComponent, NavComponent, PlannerSetupComponent, TourOverlayComponent],
})
export class AppComponent implements OnInit {
  readonly planner = inject(PlannerStateService);
  readonly tour = inject(TourService);

  helpOpen = signal(false);

  // First-visit onboarding leads with "how does this work," not the setup
  // form -- the tour/explanation itself tells a new student where campus/
  // major/minors get configured (the "Your plan" nav item), so it can
  // guide them there rather than forcing the form as the very first thing
  // they see. 'setup' is reached only via the explicit skip button below.
  onboardingStage = signal<'intro' | 'setup'>('intro');

  async ngOnInit() {
    await this.planner.init();
  }

  toggleChat() {
    this.planner.chatOpen.update((v) => !v);
  }

  toggleHelp() {
    this.helpOpen.update((v) => !v);
  }

  startTour() {
    this.helpOpen.set(false);
    this.tour.start();
  }

  startTourFromOnboarding() {
    this.planner.completeOnboarding();
    this.startTour();
  }

  showExplanationFromOnboarding() {
    this.planner.completeOnboarding();
    this.helpOpen.set(true);
  }

  skipOnboardingIntroToSetup() {
    this.onboardingStage.set('setup');
  }
}
