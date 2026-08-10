import { Injectable, inject, signal } from '@angular/core';
import { CoursePlan, DegreePlanInfo } from '../models/course-plan.model';
import { PlanningSettings, PromptPayload } from '../components/chatbot/chatbot.component';
import { BackendService } from './backend.service';

export type PlannerState = {
  major: string;
  catalogYear?: number;
  completed: string[];
  startYear: number;
  gradYears: number;
  allowSummer: boolean;
  summerUnavailable: string[];
};

/**
 * Single source of truth for the student's plan, shared across every routed
 * page (Home, Flowchart, Progress, Recommendations, ...) and the persistent
 * chat panel. Extracted from AppComponent when the app grew from one screen
 * into a multi-page shell with a router-outlet — every page injects this
 * instead of receiving props down a component tree.
 */
@Injectable({ providedIn: 'root' })
export class PlannerStateService {
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

  async init() {
    this.degreePlans.set(await this.backend.degreePlans());
  }

  async onPromptSubmitted(payload: PromptPayload) {
    const prev = this.state();
    this.state.set({
      ...prev,
      major: (payload.major?.trim() || prev.major).toUpperCase(),
    });
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
      // catalog_year is intentionally NOT sent — start_year (from the
      // "Started college" dropdown, or a chat correction) is the single
      // source of truth for which catalog year to load. Sending a
      // remembered catalog_year here would make it sticky: once the
      // backend echoed one back, it would out-rank every future start_year
      // change in the backend's `catalog_year or start_year` fallback,
      // silently breaking the "Started college" control after the first request.
      const plan = await this.backend.plan({
        major: st.major,
        prompt,
        completed: st.completed,
        start_year: st.startYear,
        grad_years: st.gradYears,
        allow_summer: st.allowSummer,
        summer_unavailable: st.summerUnavailable,
      });

      // The backend is the source of truth: it merges chat-matched courses
      // into `completed`, detects the major from the message, tracks summer
      // availability, and can correct the start year from a chat statement
      // ("oh, I started school in 2022") even if the dropdown was never
      // touched — sync all of it back so the UI reflects what was actually used.
      this.state.set({
        ...st,
        major: plan.major || st.major,
        catalogYear: plan.catalogYear ?? st.catalogYear,
        completed: plan.completed,
        startYear: plan.state?.startYear ?? st.startYear,
        gradYears: plan.state?.gradYears ?? st.gradYears,
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
