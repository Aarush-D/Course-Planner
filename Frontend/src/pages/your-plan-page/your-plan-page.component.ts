import { ChangeDetectionStrategy, Component } from '@angular/core';
import { PlannerSetupComponent } from '../../components/planner-setup/planner-setup.component';

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
  imports: [PlannerSetupComponent],
})
export class YourPlanPageComponent {}
