import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { FlowchartComponent } from '../../components/flowchart/flowchart.component';
import { CoursePlan } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import { PlannerState } from '../../services/planner-state.service';
import { toPlannerRequest } from '../../utils/planner-request.util';
import { decodeShareToken } from '../../utils/share-token.util';

/**
 * Fully self-contained read-only view for a "Share" link (see
 * YourPlanPageComponent) -- decodes the token straight into a PlannerState
 * and fetches from the (stateless) backend directly, without ever touching
 * PlannerStateService. AppComponent renders this in place of its entire
 * normal shell (nav/chat/onboarding) whenever a `?shared=` token is present,
 * so a visitor here can never see or mutate the live app's own state.
 */
@Component({
  selector: 'app-shared-plan-page',
  standalone: true,
  templateUrl: './shared-plan-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FlowchartComponent],
})
export class SharedPlanPageComponent {
  private readonly backend = inject(BackendService);

  token = input.required<string>();

  loading = signal(true);
  error = signal<string | null>(null);
  decodedState = signal<PlannerState | null>(null);
  plan = signal<CoursePlan | null>(null);
  majorTitle = signal<string | null>(null);

  constructor() {
    // token is a required input set once at creation and never changes for
    // this component's lifetime, so this effect fires exactly once.
    effect(() => this._load(this.token()));
  }

  minorsLabel(): string {
    const minors = this.decodedState()?.minors ?? [];
    return minors.length ? minors.join(', ') : 'None';
  }

  private async _load(token: string) {
    this.loading.set(true);
    this.error.set(null);

    let state: PlannerState;
    try {
      state = decodeShareToken(token);
    } catch (e: any) {
      this.error.set(e?.message ?? 'This link is broken or out of date.');
      this.loading.set(false);
      return;
    }
    this.decodedState.set(state);

    try {
      const [plan, degreePlans] = await Promise.all([
        this.backend.plan(toPlannerRequest(state)),
        this.backend.degreePlans(state.campus),
      ]);
      this.plan.set(plan);
      this.majorTitle.set(degreePlans.find((d) => d.major === state.major)?.title ?? state.major);
    } catch {
      this.error.set("Couldn’t load this plan. Try again in a moment.");
    } finally {
      this.loading.set(false);
    }
  }
}
