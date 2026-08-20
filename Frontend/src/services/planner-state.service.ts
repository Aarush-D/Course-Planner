import { Injectable, inject, signal } from '@angular/core';
import { CoursePlan, DegreePlanInfo, MinorPlanInfo } from '../models/course-plan.model';
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
  // Non-course plan items (e.g. a generic "GEN ED" box) a bulk-completion
  // phrase ("I'm a junior") marked done — echoed back by the backend and
  // re-sent on every later request, otherwise a settings-only change (no
  // new prompt) would silently forget them and un-complete requirements
  // that were already satisfied.
  consumedSlotIds: number[];
  // Double/triple/quad major / minors — every major beyond the primary
  // `major` field above, in slot order; empty means a plain single-major
  // request, identical to before this feature existed.
  additionalMajors: string[];
  minors: string[];
  // Which PSU campus's degree/minor plans are shown — University Park is
  // the only campus with real data today (every plan built so far defaults
  // to it server-side), so this is purely a display/filter choice, not
  // something sent to /api/plan.
  campus: string;
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
  minorPlans = signal<MinorPlanInfo[]>([]);
  campuses = signal<string[]>(['University Park']);

  state = signal<PlannerState>({
    major: 'CMPSC',
    catalogYear: undefined,
    completed: [],
    startYear: new Date().getFullYear(),
    gradYears: 4,
    allowSummer: false,
    summerUnavailable: [],
    consumedSlotIds: [],
    additionalMajors: [],
    minors: [],
    campus: 'University Park',
  });

  async init() {
    const { campuses, default: defaultCampus } = await this.backend.campuses();
    this.campuses.set(campuses);
    this.state.update((s) => ({ ...s, campus: defaultCampus }));
    await this._loadPlansForCampus(defaultCampus);
  }

  /** Campus dropdown changed — refetch the major/minor lists scoped to it.
   * A campus with no plan data yet (every PSU campus besides University
   * Park, today) comes back with empty lists; the chat panel shows that
   * plainly instead of falling back to a misleading default major. */
  async onCampusChanged(campus: string) {
    const prev = this.state();
    if (campus === prev.campus) return;
    this.state.set({ ...prev, campus, additionalMajors: [], minors: [] });
    await this._loadPlansForCampus(campus);
  }

  private async _loadPlansForCampus(campus: string) {
    const [plans, minors] = await Promise.all([
      this.backend.degreePlans(campus),
      this.backend.minorPlans(campus),
    ]);
    this.degreePlans.set(plans);
    this.minorPlans.set(minors);
    // If the currently selected major isn't offered at the new campus,
    // fall back to whatever the new list's first option is (or leave it —
    // the chatbot's own empty-state handles a fully empty list).
    if (plans.length && !plans.some((p) => p.major === this.state().major)) {
      this.state.update((s) => ({ ...s, major: plans[0].major }));
    }
  }

  /** Extra major slots / minors picker changed. */
  async onProgramsChanged(majors: string[], minors: string[]) {
    const prev = this.state();
    this.state.set({ ...prev, additionalMajors: majors, minors });
    await this.refreshPlan('');
  }

  async onPromptSubmitted(payload: PromptPayload) {
    const prev = this.state();
    this.state.set({
      ...prev,
      major: (payload.major?.trim() || prev.major).toUpperCase(),
    });
    await this.refreshPlan(payload.prompt, payload.recentReply, payload.turnIndex);
  }

  /** Demo-login entry point (see DemoLoginPageComponent) — seeds major/minors
   * then submits a real class-standing phrase ("I'm a junior") through the
   * exact same bulk-completion path a typed chat message would use, rather
   * than hand-listing which courses a demo student has "taken". That keeps
   * every demo profile's completed courses real and prereq-consistent
   * (derived from the actual degree plan) instead of invented data that
   * could silently drift from the plan JSON it's supposed to represent.
   * Resets to a clean slate first, matching what a fresh login implies. */
  async loginAsDemoStudent(major: string, standingPrompt: string, minors: string[] = []) {
    this.state.set({
      major: major.toUpperCase(),
      catalogYear: undefined,
      completed: [],
      startYear: new Date().getFullYear(),
      gradYears: 4,
      allowSummer: false,
      summerUnavailable: [],
      consumedSlotIds: [],
      additionalMajors: [],
      minors,
      campus: this.state().campus,
    });
    await this.refreshPlan(standingPrompt);
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

  private async refreshPlan(prompt: string, recentReply?: string, turnIndex?: number) {
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
        consumed_slot_ids: st.consumedSlotIds,
        recent_reply: recentReply,
        turn_index: turnIndex,
        // st.additionalMajors[0] fills the backend's original second_major
        // field for backward compatibility; anything beyond that (a
        // 3rd/4th major) goes through the newer additional_majors list.
        second_major: st.additionalMajors[0],
        additional_majors: st.additionalMajors.slice(1),
        minors: st.minors,
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
        consumedSlotIds: plan.state?.consumedSlotIds ?? st.consumedSlotIds,
      });
      this.coursePlan.set(plan);
    } catch (e) {
      console.error('Failed to fetch plan:', e);
    } finally {
      this.loading.set(false);
    }
  }
}
