import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PlanCompareComponent } from '../../components/plan-compare/plan-compare.component';
import { PlannerSetupComponent } from '../../components/planner-setup/planner-setup.component';
import { RateCourseModalComponent } from '../../components/rate-course-modal/rate-course-modal.component';
import { PlannerStateService } from '../../services/planner-state.service';
import { ReviewRequestService } from '../../services/review-request.service';
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
  imports: [PlannerSetupComponent, PlanCompareComponent, RateCourseModalComponent, RouterLink],
})
export class YourPlanPageComponent {
  private readonly planner = inject(PlannerStateService);
  private readonly toast = inject(ToastService);
  private readonly reviewRequests = inject(ReviewRequestService);
  readonly supabase = inject(SupabaseService);

  requestingReview = signal(false);

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
