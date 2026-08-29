import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  OnInit,
  afterNextRender,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { animateModalIn, animateModalOut } from './animations/modal-fade';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { NavComponent } from './components/nav/nav.component';
import { PlannerSetupComponent } from './components/planner-setup/planner-setup.component';
import { ToastComponent } from './components/toast/toast.component';
import { TourOverlayComponent } from './components/tour-overlay/tour-overlay.component';
import { PlannerStateService } from './services/planner-state.service';
import { ThemeService } from './services/theme.service';
import { TourService } from './services/tour.service';

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, ChatbotComponent, NavComponent, PlannerSetupComponent, TourOverlayComponent, ToastComponent],
})
export class AppComponent implements OnInit {
  readonly planner = inject(PlannerStateService);
  readonly tour = inject(TourService);
  readonly theme = inject(ThemeService);
  private readonly injector = inject(Injector);

  helpOpen = signal(false);

  // First-visit onboarding leads with "how does this work," not the setup
  // form -- the tour/explanation itself tells a new student where campus/
  // major/minors get configured (the "Your plan" nav item), so it can
  // guide them there rather than forcing the form as the very first thing
  // they see. 'setup' is reached only via the explicit skip button below.
  onboardingStage = signal<'intro' | 'setup'>('intro');

  // Backdrop/panel refs for the 3 modals below -- used to fade+scale them
  // in/out with `motion` instead of Angular's @if hard-cutting them.
  private readonly helpBackdrop = viewChild('helpBackdrop', { read: ElementRef<HTMLElement> });
  private readonly helpPanel = viewChild('helpPanel', { read: ElementRef<HTMLElement> });
  private readonly introBackdrop = viewChild('introBackdrop', { read: ElementRef<HTMLElement> });
  private readonly introPanel = viewChild('introPanel', { read: ElementRef<HTMLElement> });
  private readonly setupBackdrop = viewChild('setupBackdrop', { read: ElementRef<HTMLElement> });
  private readonly setupPanel = viewChild('setupPanel', { read: ElementRef<HTMLElement> });

  constructor() {
    // One effect per modal, each firing (only) on its own false->true
    // transition -- afterNextRender is needed since @if only just created
    // the backdrop/panel nodes this same tick, so the viewChild queries
    // above aren't populated yet at the moment the effect body runs.
    effect(() => {
      if (!this.helpOpen()) return;
      afterNextRender(() => this._animateIn(this.helpBackdrop(), this.helpPanel()), { injector: this.injector });
    });
    effect(() => {
      if (this.planner.onboarded() || this.onboardingStage() !== 'intro') return;
      afterNextRender(() => this._animateIn(this.introBackdrop(), this.introPanel()), { injector: this.injector });
    });
    effect(() => {
      if (this.planner.onboarded() || this.onboardingStage() !== 'setup') return;
      afterNextRender(() => this._animateIn(this.setupBackdrop(), this.setupPanel()), { injector: this.injector });
    });
  }

  async ngOnInit() {
    await this.planner.init();
  }

  toggleChat() {
    this.planner.chatOpen.update((v) => !v);
  }

  /** Header button, the modal's own X, and its backdrop click all call this
   * -- it opens when closed and (animated) closes when open, so every
   * trigger stays wired to the same one method regardless of direction. */
  toggleHelp() {
    if (this.helpOpen()) {
      this._closeHelpAnimated();
    } else {
      this.helpOpen.set(true);
    }
  }

  async startTour() {
    // No-ops harmlessly if help isn't open (backdrop/panel refs are
    // undefined when @if hasn't rendered them, and setting an
    // already-false signal back to false doesn't trigger anything).
    await this._closeHelpAnimated();
    this.tour.start();
  }

  async startTourFromOnboarding() {
    await this._animateOut(this.introBackdrop(), this.introPanel());
    afterNextRender(() => this.planner.completeOnboarding(), { injector: this.injector });
    this.startTour();
  }

  async showExplanationFromOnboarding() {
    await this._animateOut(this.introBackdrop(), this.introPanel());
    afterNextRender(() => {
      this.planner.completeOnboarding();
      this.helpOpen.set(true);
    }, { injector: this.injector });
  }

  async skipOnboardingIntroToSetup() {
    await this._animateOut(this.introBackdrop(), this.introPanel());
    afterNextRender(() => this.onboardingStage.set('setup'), { injector: this.injector });
  }

  async closeSetup() {
    await this._animateOut(this.setupBackdrop(), this.setupPanel());
    afterNextRender(() => this.planner.completeOnboarding(), { injector: this.injector });
  }

  private async _closeHelpAnimated(): Promise<void> {
    await this._animateOut(this.helpBackdrop(), this.helpPanel());
    afterNextRender(() => this.helpOpen.set(false), { injector: this.injector });
  }

  private _animateIn(backdrop: ElementRef<HTMLElement> | undefined, panel: ElementRef<HTMLElement> | undefined) {
    if (backdrop && panel) animateModalIn(backdrop.nativeElement, panel.nativeElement);
  }

  private async _animateOut(
    backdrop: ElementRef<HTMLElement> | undefined,
    panel: ElementRef<HTMLElement> | undefined,
  ): Promise<void> {
    if (backdrop && panel) await animateModalOut(backdrop.nativeElement, panel.nativeElement);
  }
}
