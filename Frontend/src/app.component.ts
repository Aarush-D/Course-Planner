import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { CoursePlan, DegreePlanInfo } from './models/course-plan.model';
import { ChatbotComponent, PlanningSettings, PromptPayload } from './components/chatbot/chatbot.component';
import { FlowchartComponent } from './components/flowchart/flowchart.component';
import { RecommendationsComponent } from './components/recommendations/recommendations.component';
import { BackendService } from './services/backend.service';

type PlannerState = {
  major: string;
  catalogYear?: number;
  completed: string[];
  startYear: number;
  gradYears: number;
  allowSummer: boolean;
  summerUnavailable: string[];
};

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChatbotComponent, FlowchartComponent, RecommendationsComponent],
})
export class AppComponent implements OnInit {
  private readonly backend = inject(BackendService);

  coursePlan = signal<CoursePlan | null>(null);
  loading = signal(false);
  degreePlans = signal<DegreePlanInfo[]>([]);

  state = signal<PlannerState>({
    major: 'CMPSC',
    catalogYear: undefined,
    completed: [],
    startYear: new Date().getFullYear(),
    gradYears: 4,
    allowSummer: false,
    summerUnavailable: [],
  });

  async ngOnInit() {
    this.degreePlans.set(await this.backend.degreePlans());
  }

  async onPromptSubmitted(payload: PromptPayload) {
    const prev = this.state();
    const next: PlannerState = {
      ...prev,
      major: (payload.major?.trim() || prev.major).toUpperCase(),
      catalogYear: payload.catalogYear ?? prev.catalogYear,
    };
    this.state.set(next);
    await this.refreshPlan(payload.prompt);
  }

  /** Year-planning controls changed (start year / grad years / summer toggle). */
  async onPlanningChanged(settings: PlanningSettings) {
    const prev = this.state();
    this.state.set({
      ...prev,
      startYear: settings.startYear,
      gradYears: settings.gradYears,
      allowSummer: settings.allowSummer,
    });
    await this.refreshPlan('');
  }

  /** Remove a completed course (chip X button) and re-plan. */
  async onRemoveCompleted(code: string) {
    const prev = this.state();
    this.state.set({
      ...prev,
      completed: prev.completed.filter(
        (c) => c.trim().toUpperCase() !== code.trim().toUpperCase()
      ),
    });
    await this.refreshPlan('');
  }

  private async refreshPlan(prompt: string) {
    const st = this.state();
    this.loading.set(true);
    try {
      const plan = await this.backend.plan({
        major: st.major,
        catalog_year: st.catalogYear,
        prompt,
        completed: st.completed,
        start_year: st.startYear,
        grad_years: st.gradYears,
        allow_summer: st.allowSummer,
        summer_unavailable: st.summerUnavailable,
      });

      // The backend is the source of truth: it merges chat-matched courses
      // into `completed`, detects the major from the message, and tracks
      // summer availability the student reports.
      this.state.set({
        ...st,
        major: plan.major || st.major,
        catalogYear: plan.catalogYear ?? st.catalogYear,
        completed: plan.completed,
        summerUnavailable: plan.state?.summerUnavailable ?? st.summerUnavailable,
      });
      this.coursePlan.set(plan);
    } catch (e) {
      console.error('Failed to fetch plan:', e);
    } finally {
      this.loading.set(false);
    }
  }
}
