import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  OnInit,
  afterNextRender,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { animateModalIn, animateModalOut } from './animations/modal-fade';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { NavComponent } from './components/nav/nav.component';
import { PlannerSetupComponent } from './components/planner-setup/planner-setup.component';
import { PreferencesPanelComponent } from './components/preferences-panel/preferences-panel.component';
import { ToastComponent } from './components/toast/toast.component';
import { TourOverlayComponent } from './components/tour-overlay/tour-overlay.component';
import { ReviewRequestPageComponent } from './pages/review-request-page/review-request-page.component';
import { SharedPlanPageComponent } from './pages/shared-plan-page/shared-plan-page.component';
import { PlannerStateService } from './services/planner-state.service';
import { StudentSessionService } from './services/student-session.service';
import { ThemeService } from './services/theme.service';
import { TourService } from './services/tour.service';

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterOutlet,
    ChatbotComponent,
    NavComponent,
    PlannerSetupComponent,
    PreferencesPanelComponent,
    TourOverlayComponent,
    ToastComponent,
    SharedPlanPageComponent,
    ReviewRequestPageComponent,
  ],
})
export class AppComponent implements OnInit {
  readonly planner = inject(PlannerStateService);
  readonly tour = inject(TourService);
  readonly theme = inject(ThemeService);
  private readonly studentSession = inject(StudentSessionService);
  private readonly injector = inject(Injector);
  private readonly router = inject(Router);

  // A `?shared=<token>` link (see YourPlanPageComponent's Share button) --
  // read once at construction since this is a fresh/external-link scenario,
  // not something that changes via in-app navigation. Its presence swaps
  // out this component's entire normal shell (see app.component.html) for
  // a fully isolated read-only view that never touches planner.init() or
  // any of the live app's state.
  readonly sharedToken = signal<string | null>(new URLSearchParams(location.search).get('shared'));
  readonly isSharedView = computed(() => this.sharedToken() !== null);

  // A `?review=<uuid>` link (see YourPlanPageComponent's "Request advisor
  // review" action) -- same isolation reasoning as isSharedView, just
  // backed by a real Supabase row instead of a client-encoded token.
  readonly reviewId = signal<string | null>(new URLSearchParams(location.search).get('review'));
  readonly isReviewView = computed(() => this.reviewId() !== null);

  // /advisor/* are real routed paths (unlike the two query-param views
  // above), reactively tracked since an advisor navigates BETWEEN them
  // in-app (dashboard -> a specific review) without a fresh page load.
  // They get their own full-page shell too -- an advisor portal has no use
  // for the student sidebar/chat/onboarding around it.
  readonly currentPath = signal(location.pathname);
  readonly isAdvisorRoute = computed(() => this.currentPath().includes('/advisor/'));

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
    this.router.events
      .pipe(filter((e) => e instanceof NavigationEnd), takeUntilDestroyed())
      .subscribe(() => this.currentPath.set(location.pathname));

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
    if (this.isSharedView() || this.isReviewView() || this.isAdvisorRoute()) return;
    await this.planner.init();
    // A no-op for the ~100% of visitors with no student account -- see
    // StudentSessionService for what this actually does when one exists.
    this.studentSession.tryResumeSavedPlan();
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
