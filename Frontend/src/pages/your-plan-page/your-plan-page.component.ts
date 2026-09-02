import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, ElementRef, HostListener, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlanCompareComponent } from '../../components/plan-compare/plan-compare.component';
import { PlannerSetupComponent } from '../../components/planner-setup/planner-setup.component';
import { RateCourseModalComponent } from '../../components/rate-course-modal/rate-course-modal.component';
import { PlannerStateService } from '../../services/planner-state.service';
import { ReviewRequestService } from '../../services/review-request.service';
import { StudentSessionService } from '../../services/student-session.service';
import { SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { normalizeCourseCode } from '../../utils/course-code.util';
import { encodeShareToken } from '../../utils/share-token.util';

/**
 * Routed home for the "set this once" fields (Campus/Major/Minors/Number
 * of majors/Started college/Graduate in) — a real page like Flowchart or
 * Recommendations, not a permanent fixture eating sidebar space. The same
 * <app-planner-setup> also renders inside the first-visit onboarding
 * modal (app.component.html); this page is where a student comes back to
 * change their mind later.
 */
@Component({
  selector: 'app-your-plan-page',
  standalone: true,
  templateUrl: './your-plan-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [PlannerSetupComponent, PlanCompareComponent, RateCourseModalComponent, RouterLink, DatePipe],
})
export class YourPlanPageComponent {
  private readonly planner = inject(PlannerStateService);
  private readonly toast = inject(ToastService);
  private readonly reviewRequests = inject(ReviewRequestService);
  readonly supabase = inject(SupabaseService);
  readonly studentSession = inject(StudentSessionService);

  requestingReview = signal(false);

  // ── My plans (see migration 0008 -- a signed-in student can save more
  // than one named plan) ────────────────────────────────────────────────
  creatingPlan = signal(false);
  newPlanName = signal('');
  savingNewPlan = signal(false);
  renamingId = signal<string | null>(null);
  renameValue = signal('');
  switchingId = signal<string | null>(null);
  deletingId = signal<string | null>(null);

  startNewPlan() {
    this.creatingPlan.set(true);
    this.newPlanName.set('');
  }

  cancelNewPlan() {
    this.creatingPlan.set(false);
  }

  async confirmNewPlan() {
    if (this.savingNewPlan()) return;
    this.savingNewPlan.set(true);
    try {
      await this.studentSession.saveAsNewPlan(this.newPlanName().trim() || 'My Plan');
      this.creatingPlan.set(false);
      this.toast.show('New plan saved!');
    } catch {
      this.toast.show("Couldn't save that plan — try again in a moment.", 'error');
    } finally {
      this.savingNewPlan.set(false);
    }
  }

  async switchPlan(planId: string) {
    if (planId === this.studentSession.activePlanId() || this.switchingId()) return;
    this.switchingId.set(planId);
    try {
      await this.studentSession.switchToPlan(planId);
    } catch {
      this.toast.show("Couldn't load that plan — try again in a moment.", 'error');
    } finally {
      this.switchingId.set(null);
    }
  }

  startRename(planId: string, currentName: string) {
    this.renamingId.set(planId);
    this.renameValue.set(currentName);
  }

  cancelRename() {
    this.renamingId.set(null);
  }

  async confirmRename() {
    const planId = this.renamingId();
    if (!planId) return;
    try {
      await this.studentSession.renamePlan(planId, this.renameValue().trim() || 'My Plan');
    } catch {
      this.toast.show("Couldn't rename that plan — try again in a moment.", 'error');
    } finally {
      this.renamingId.set(null);
    }
  }

  async deletePlan(planId: string, name: string) {
    if (this.studentSession.savedPlans().length <= 1) return;
    if (!window.confirm(`Delete "${name}"? This can't be undone.`)) return;
    this.deletingId.set(planId);
    try {
      await this.studentSession.deletePlan(planId);
      this.toast.show(`Deleted "${name}."`);
    } catch {
      this.toast.show("Couldn't delete that plan — try again in a moment.", 'error');
    } finally {
      this.deletingId.set(null);
    }
  }

  /** Popover with the page's explainer text, toggled from the "?" icon next
   * to the heading -- replaces the always-visible paragraphs that used to
   * sit under "Your plan", freeing up vertical space above the fold. */
  infoOpen = signal(false);
  private readonly infoToggleButton = viewChild<ElementRef<HTMLButtonElement>>('infoToggleButton');

  toggleInfo() {
    this.infoOpen.update((v) => !v);
  }

  /** The popover's own Close button and Escape both remove the element
   * that currently has focus (Angular's @if unmounts it) with nothing
   * else claiming focus -- the browser silently drops it to <body>. This
   * explicitly returns it to the "?" toggle button instead. */
  closeInfo() {
    this.infoOpen.set(false);
    this.infoToggleButton()?.nativeElement.focus();
  }

  /** Document-level, not a template (keydown.escape) on the popover itself
   * -- the popover opens without moving focus into it (no
   * appModalFocusTrap here), so focus stays on the "?" toggle button, a
   * SIBLING of the popover, not a descendant. A handler bound only on the
   * popover element never sees a keydown whose target is that button,
   * since keydown only bubbles through ancestors of the focused element --
   * Escape pressed right after opening (the natural first thing a
   * keyboard user tries) would silently do nothing. Matches
   * account-menu.component.ts/preferences-panel.component.ts's own
   * @HostListener('document:keydown.escape'), which fires regardless of
   * where focus currently is. */
  @HostListener('document:keydown.escape')
  onEscape() {
    if (!this.infoOpen()) return;
    this.closeInfo();
  }

  /** A course code the student typed here directly, for the "rate a course
   * you've taken" entry point -- covers courses that never rendered on a
   * card at all (transfer credit, an older transcript). Not validated
   * against a real catalog (Flask has no course-lookup endpoint), just
   * normalized client-side like every other course code in this app. */
  rateCourseInput = signal('');
  ratingModalFor = signal<string | null>(null);

  openRatingModal() {
    const code = normalizeCourseCode(this.rateCourseInput());
    if (!code) return;
    this.ratingModalFor.set(code);
  }

  closeRatingModal() {
    this.ratingModalFor.set(null);
    this.rateCourseInput.set('');
  }

  /** Builds a read-only link to the CURRENT plan and copies it -- the
   * backend is stateless, so the whole state fits in the URL itself (see
   * utils/share-token.util.ts), no database/share-code needed.
   *
   * Deliberately NOT document.baseURI: in a client-routed SPA that reflects
   * the CURRENT route path (e.g. ".../your-plan") once the router has
   * navigated away from the root, not the app's actual base -- only the
   * <base href> tag (present in the production build, absent in dev, where
   * it defaults to "/") gives the real root reliably in both environments. */
  shareLink() {
    const token = encodeShareToken(this.planner.state());
    const baseHref = document.querySelector('base')?.getAttribute('href') ?? '/';
    const url = new URL(baseHref, location.origin);
    url.search = `?shared=${token}`;
    navigator.clipboard.writeText(url.toString()).then(
      () => this.toast.show('Link copied!'),
      () => this.toast.show("Couldn't copy the link — check your browser's clipboard permission and try again.", 'error'),
    );
  }

  /** Creates a real, persisted review request (unlike the plain share link
   * above, this needs a stable server-side id for an advisor's comments
   * and meeting proposals to attach to) and copies a link to it -- same
   * copy/toast pattern as shareLink(). Still no student account needed. */
  async requestAdvisorReview() {
    this.requestingReview.set(true);
    let id: string;
    try {
      id = await this.reviewRequests.createReviewRequest(this.planner.state());
    } catch {
      this.toast.show("Couldn't create a review request. Try again in a moment.", 'error');
      this.requestingReview.set(false);
      return;
    }
    this.requestingReview.set(false);
    const baseHref = document.querySelector('base')?.getAttribute('href') ?? '/';
    const url = new URL(baseHref, location.origin);
    url.search = `?review=${id}`;
    navigator.clipboard.writeText(url.toString()).then(
      () => this.toast.show('Review link copied! Send it to your advisor.'),
      () => this.toast.show("Created, but couldn't copy the link — check your browser's clipboard permission and try again.", 'error'),
    );
  }
}
