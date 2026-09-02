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
import { AccountMenuComponent } from './components/account-menu/account-menu.component';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { NavComponent } from './components/nav/nav.component';
import { PlannerSetupComponent } from './components/planner-setup/planner-setup.component';
import { PreferencesPanelComponent } from './components/preferences-panel/preferences-panel.component';
import { ToastComponent } from './components/toast/toast.component';
import { TourOverlayComponent } from './components/tour-overlay/tour-overlay.component';
import { ModalFocusTrapDirective } from './directives/modal-focus-trap.directive';
import { DEMO_PROFILES, DemoProfile } from './pages/demo-login-page/demo-login-page.component';
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
    AccountMenuComponent,
    ChatbotComponent,
    NavComponent,
    PlannerSetupComponent,
    PreferencesPanelComponent,
    TourOverlayComponent,
    ToastComponent,
    SharedPlanPageComponent,
    ReviewRequestPageComponent,
    ModalFocusTrapDirective,
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
  // Bound function refs for [onEscape] -- see ModalFocusTrapDirective's
  // doc comment for why this is a plain callback input, not an output().
  readonly toggleHelpFn = () => this.toggleHelp();
  readonly finishWelcomeFn = () => this.finishWelcome();

  // First-visit onboarding is ONE modal with two ways in -- set up a real
  // plan, or try a demo profile -- instead of the two separate modals
  // (an intro choice screen, then a second setup screen) this used to
  // chain together. A visitor who came in via /demo-login already skips
  // this entirely (see that component's constructor).
  readonly demoProfiles: readonly DemoProfile[] = DEMO_PROFILES;
  welcomeTab = signal<'setup' | 'demo'>('setup');
  loggingInAsDemo = signal<string | null>(null);

  // Backdrop/panel refs for the 2 modals below -- used to fade+scale them
  // in/out with `motion` instead of Angular's @if hard-cutting them.
  private readonly helpBackdrop = viewChild('helpBackdrop', { read: ElementRef<HTMLElement> });
  private readonly helpPanel = viewChild('helpPanel', { read: ElementRef<HTMLElement> });
  private readonly welcomeBackdrop = viewChild('welcomeBackdrop', { read: ElementRef<HTMLElement> });
  private readonly welcomePanel = viewChild('welcomePanel', { read: ElementRef<HTMLElement> });

  // The floating chat-toggle button -- only in the DOM while the panel is
  // closed (`@if (!planner.chatOpen())` in the template), so this is
  // undefined whenever the panel itself is open.
  private readonly chatToggleButton = viewChild('chatToggleBtn', { read: ElementRef<HTMLElement> });

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
      if (this.planner.onboarded()) return;
      afterNextRender(() => this._animateIn(this.welcomeBackdrop(), this.welcomePanel()), { injector: this.injector });
    });

    // Returns focus to the floating chat-toggle button whenever the chat
    // panel closes, regardless of which control closed it (this toggle
    // re-clicked, or the chat panel's own close button) -- tracking the
    // signal itself, rather than only the one call site inside
    // toggleChat(), is what actually covers both. afterNextRender is
    // needed because the toggle button is destroyed/recreated by the
    // template's own `@if (!planner.chatOpen())` -- it doesn't exist again
    // until the render this same tick produces.
    let wasChatOpen = this.planner.chatOpen();
    effect(() => {
      const isChatOpen = this.planner.chatOpen();
      if (wasChatOpen && !isChatOpen) {
        afterNextRender(() => this.chatToggleButton()?.nativeElement.focus(), { injector: this.injector });
      }
      wasChatOpen = isChatOpen;
    });
  }

  async ngOnInit() {
    if (this.isSharedView() || this.isReviewView() || this.isAdvisorRoute()) return;
    await this.planner.init();
    // A no-op for the ~100% of visitors with no student account -- see
    // StudentSessionService for what this actually does when one exists.
    this.studentSession.tryResumeSavedPlan();
  }

  /** Focus-return to this button on close is handled by the effect above
   * (it watches planner.chatOpen() itself, not this method), since the
   * chat panel's own close button bypasses this method entirely. */
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

  selectWelcomeTab(tab: 'setup' | 'demo') {
    this.welcomeTab.set(tab);
  }

  demoInitials(name: string): string {
    return name.split(' ').map((n) => n[0]).join('');
  }

  /** "Get started" and the modal's own X both just finish onboarding with
   * whatever's already in the (pre-filled, sensibly-defaulted) setup form
   * -- skippable without touching anything, same as before. */
  async finishWelcome() {
    await this._animateOut(this.welcomeBackdrop(), this.welcomePanel());
    afterNextRender(() => this.planner.completeOnboarding(), { injector: this.injector });
  }

  async startTourFromWelcome() {
    await this._animateOut(this.welcomeBackdrop(), this.welcomePanel());
    afterNextRender(() => this.planner.completeOnboarding(), { injector: this.injector });
    this.startTour();
  }

  async loginAsDemoFromWelcome(profile: DemoProfile) {
    this.loggingInAsDemo.set(profile.id);
    try {
      // Load the demo plan FIRST, modal still open with a "Logging in…"
      // state on the clicked profile -- then animate out once there's
      // something real to show, rather than fading to an empty page for
      // however long the plan fetch takes. completeOnboarding() only
      // happens after the animation, same as the other two exits below --
      // loginAsDemoStudent() itself deliberately doesn't set it, so @if
      // can't yank the modal out mid-fade.
      await this.planner.loginAsDemoStudent(profile.major, profile.standingPrompt, profile.minors, profile.campus);
      await this._animateOut(this.welcomeBackdrop(), this.welcomePanel());
      afterNextRender(() => this.planner.completeOnboarding(), { injector: this.injector });
    } finally {
      this.loggingInAsDemo.set(null);
    }
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
