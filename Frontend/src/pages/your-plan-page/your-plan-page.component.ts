import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { PlanCompareComponent } from '../../components/plan-compare/plan-compare.component';
import { PlannerSetupComponent } from '../../components/planner-setup/planner-setup.component';
import { PlannerStateService } from '../../services/planner-state.service';
import { ToastService } from '../../services/toast.service';
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
  imports: [PlannerSetupComponent, PlanCompareComponent],
})
export class YourPlanPageComponent {
  private readonly planner = inject(PlannerStateService);
  private readonly toast = inject(ToastService);

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
}
