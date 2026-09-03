import { Injectable, computed, inject, signal } from '@angular/core';
import { CoursePlan, DegreePlanInfo, MinorPlanInfo, ReplyLink } from '../models/course-plan.model';
import { toPlannerRequest } from '../utils/planner-request.util';
import { BackendService } from './backend.service';
import { ToastService } from './toast.service';

const TRANSCRIPT_UPLOAD_KEY = 'transcript-last-upload';
// One semester -- long enough not to nag right after a real upload, short
// enough that a plan build on last year's transcript gets flagged.
const TRANSCRIPT_STALE_AFTER_MS = 1000 * 60 * 60 * 24 * 30 * 4;

export interface PromptPayload {
  major?: string;
  prompt: string;
}

export interface ProgramsPayload {
  majors: string[];
  minors: string[];
}

export interface PlanningSettings {
  startYear: number;
  gradYears: number;
  allowSummer: boolean;
  // undefined = let the planner decide (today's behavior: the plan's own
  // max_credits_per_semester, or 17 if it doesn't set one). Set only when
  // a student explicitly picks a load below.
  maxCreditsPerSemester?: number;
}

export type ChatMessage = {
  role: 'user' | 'assistant';
  text: string;
  links?: ReplyLink[];
};

const WELCOME_MESSAGE: ChatMessage = {
  role: 'assistant',
  text:
    'Hi! Tell me which courses you’ve already taken (e.g. “I took CMPSC 131 and calc 1”) ' +
    'or ask “What should I take next semester?” — I’ll match your courses and plan the rest.',
};

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
  // An ALEKS score or "I took calc in high school" mentioned in an earlier
  // turn — same echoed-back, re-sent-every-request reason as
  // consumedSlotIds, since it's also a one-time fact from the prompt, not
  // something restated every message.
  mathPlacementTier?: number;
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
  // "I don't know my major yet" — while true, no degree plan is fetched at
  // all (there's nothing to schedule); chat instead runs the separate
  // /api/explore-majors conversation. See PlannerSetupComponent's
  // Undecided checkbox and onExplorePromptSubmitted below.
  undecided: boolean;
  // Courses the student has added to the Weekly Schedule preview (see
  // components/weekly-schedule and utils/dummy-schedule.util.ts). Purely a
  // client-side "I'm planning to take this" marker -- there's no real
  // registration behind it, and the meeting times shown are made up
  // (PSU's public bulletin data has no real per-section times at all).
  scheduledCourseIds: string[];
  // How many credits to pack into each simulated term at most -- undefined
  // means "let the planner decide" (see PlanningSettings above). The
  // backend already enforced a cap per term either way; this just lets a
  // student who wants a lighter or heavier load than the default actually
  // say so, instead of it being a backend-only parameter no UI ever sent.
  maxCreditsPerSemester?: number;
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
  private readonly toast = inject(ToastService);

  coursePlan = signal<CoursePlan | null>(null);
  loading = signal(false);
  degreePlans = signal<DegreePlanInfo[]>([]);
  minorPlans = signal<MinorPlanInfo[]>([]);
  campuses = signal<string[]>(['University Park']);

  // Shared with the chat panel's own open/close so other pages (e.g. Home's
  // example-prompt chips) can open chat with a prompt pre-filled, without
  // reaching into AppComponent directly.
  chatOpen = signal(false);
  pendingPrompt = signal<string | undefined>(undefined);

  openChatWithPrompt(text: string) {
    this.pendingPrompt.set(text);
    this.chatOpen.set(true);
  }

  // Owned here (not the chat panel component) so the transcript survives
  // the panel closing and reopening — <app-chatbot> is created/destroyed
  // by an @if in app.component.html, so any state that lived on the
  // component itself was wiped every time the panel closed.
  chatMessages = signal<ChatMessage[]>([WELCOME_MESSAGE]);
  private lastRecordedReply = '';

  // True only when a first-time visitor hasn't configured anything yet —
  // gates the onboarding modal (see app.component.html). Demo login also
  // marks this true since it already fully configures a real profile.
  onboarded = signal(false);

  completeOnboarding() {
    this.onboarded.set(true);
  }

  noProgramsForCampus = computed(
    () => this.state().campus !== 'University Park' && this.degreePlans().length === 0,
  );

  // Read once at construction (same pattern as ThemeService's own `dark`
  // signal) -- a plain localStorage.getItem() call inside a computed()
  // wouldn't be tracked, so an external change would never trigger
  // recomputation. Updated via .set() in _recordTranscriptUpload(), which
  // is the only way this legitimately changes during a live session.
  private readonly lastTranscriptUpload = signal(this._readTranscriptUpload());

  // True once a real plan exists AND a transcript was uploaded a semester
  // or more ago -- false (not nudging) for someone who's never uploaded one
  // at all, since typed/chat/demo entry isn't what this is reminding about.
  transcriptStale = computed(() => {
    if (!this.state().completed.length) return false;
    const last = this.lastTranscriptUpload();
    if (last === null) return false;
    return Date.now() - last > TRANSCRIPT_STALE_AFTER_MS;
  });

  state = signal<PlannerState>(this._defaultState());

  /** The same blank slate a fresh, never-used visitor sees -- factored out
   * so resetToDefault() below can reuse it verbatim instead of drifting
   * out of sync with the initial signal value. */
  private _defaultState(): PlannerState {
    return {
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
      undecided: false,
      scheduledCourseIds: [],
    };
  }

  /** Resets `state`/`coursePlan`/`chatMessages` (and the transcript-upload
   * nudge) back to the exact same blank slate a fresh, never-used visitor
   * sees. Called by StudentSessionService right before sign-out (nothing
   * about the just-signed-out student should keep rendering) and, via
   * applyLoadedState() below, right before loading an existing account's
   * saved plan (so the loaded plan fully REPLACES whatever anonymous/local
   * state was showing instead of merging with it). Deliberately leaves
   * degreePlans/minorPlans/campuses/onboarded alone -- those are catalog
   * data and first-visit UI state, not per-student plan state, and this
   * fix is scoped to the plan/chat data that was actually leaking across
   * sign-in/out. */
  resetToDefault(): void {
    this.state.set(this._defaultState());
    this.coursePlan.set(null);
    this.chatMessages.set([WELCOME_MESSAGE]);
    this.lastRecordedReply = '';
    this._clearTranscriptUpload();
  }

  /** Loads a previously-saved plan (existing-account sign-in, or switching
   * between a signed-in student's own saved plans) as the new live state.
   * Resets to blank first so the loaded plan fully REPLACES whatever was
   * showing rather than merging with it, then re-derives `coursePlan` from
   * the loaded state via the normal refreshPlan() pipeline -- only the raw
   * PlannerState is persisted (see StudentPlanService.loadPlan), never the
   * derived CoursePlan, so it must be recomputed here, the same way every
   * other state change in this service re-derives it. */
  async applyLoadedState(saved: PlannerState): Promise<void> {
    this.resetToDefault();
    this.state.set(saved);
    await this.refreshPlan('');
  }

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

  /** Undecided checkbox toggled (PlannerSetupComponent). Turning it on
   * clears whatever major/minors/plan currently exist — there's nothing
   * to schedule while undecided, so any stale plan would be misleading.
   * Turning it off just clears the flag; the student picks a real major
   * from Setup next, which re-plans through the normal path. */
  setUndecided(value: boolean) {
    if (value) {
      this.coursePlan.set(null);
      this.state.update((s) => ({
        ...s,
        undecided: true,
        additionalMajors: [],
        minors: [],
      }));
    } else {
      this.state.update((s) => ({ ...s, undecided: false }));
    }
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

  /** A real chat submission (non-empty prompt) or a Setup-driven major
   * change (empty prompt, major only — see PlannerSetupComponent). Only
   * the former appends a user bubble to the transcript; a silent major
   * switch from the sidebar isn't something the student "said". */
  async onPromptSubmitted(payload: PromptPayload) {
    const priorMessages = this.chatMessages();
    const lastAssistantReply = [...priorMessages].reverse().find((m) => m.role === 'assistant')?.text;
    const turnIndex = priorMessages.filter((m) => m.role === 'user').length;

    if (payload.prompt.trim()) {
      this.chatMessages.update((m) => [...m, { role: 'user', text: payload.prompt }]);
    }

    const prev = this.state();
    const nextMajor = (payload.major?.trim() || prev.major).toUpperCase();
    this.state.set({
      ...prev,
      major: nextMajor,
      // Reaching this method at all means a real, major-driven plan is
      // being requested — while undecided is true, chat routes to
      // onExplorePromptSubmitted instead, so the only caller here while
      // still undecided is Setup's own major picker, which is exactly
      // the "I've decided" moment.
      undecided: false,
      // Slot ids are small sequential integers assigned fresh per (major,
      // catalog_year) plan — switching majors loads a completely different
      // plan whose own item ids start over from the same low numbers, so a
      // carried-over id could coincidentally collide with a real item in
      // the new plan and silently mark an un-completed requirement done.
      // The backend already validates ids against the current plan's real
      // item ids (drops anything that doesn't exist), but that can't catch
      // a *different* item that happens to share the same id — only a
      // fresh start here can.
      consumedSlotIds: nextMajor === prev.major ? prev.consumedSlotIds : [],
    });
    await this.refreshPlan(payload.prompt, lastAssistantReply?.slice(0, 400), turnIndex);
  }

  /** PDF transcript upload (the chat panel's grey + button) — an
   * alternate INPUT PATH into the same completed-courses list a typed
   * "I took CMPSC 131" message would produce, not a separate system.
   * See BackendService.parseTranscript / Backend/app.py's
   * /api/parse-transcript. */
  async onTranscriptUploaded(file: File) {
    const st = this.state();
    this.loading.set(true);
    try {
      const { matched, unmatched } = await this.backend.parseTranscript(file, {
        major: st.major,
        catalog_year: st.catalogYear,
        start_year: st.startYear,
        second_major: st.additionalMajors[0],
        additional_majors: st.additionalMajors.slice(1),
        minors: st.minors,
      });

      const newCodes = matched.map((m) => m.code).filter((c) => !st.completed.includes(c));
      if (newCodes.length) {
        this.state.update((s) => ({ ...s, completed: [...s.completed, ...newCodes] }));
        this.toast.show(
          `${newCodes.length} course${newCodes.length === 1 ? '' : 's'} added from transcript`
        );
        this._recordTranscriptUpload();
      }

      const parts: ChatMessage[] = [];
      if (matched.length) {
        parts.push({
          role: 'assistant',
          text:
            `✓ Matched ${matched.length} course${matched.length === 1 ? '' : 's'} from your transcript: ` +
            matched.map((m) => `${m.code} (${m.name})`).join(', '),
        });
      } else {
        parts.push({
          role: 'assistant',
          text: "Didn't find any recognizable courses in that transcript.",
        });
      }
      if (unmatched.length) {
        parts.push({
          role: 'assistant',
          text:
            "Couldn't match: " + unmatched.join(', ') +
            ' — check the course codes, or add them by typing instead.',
        });
      }
      this.chatMessages.update((msgs) => [...msgs, ...parts]);

      if (newCodes.length) {
        await this.refreshPlan('');
      } else {
        this.loading.set(false);
      }
    } catch (e: any) {
      const message = e?.message || "Couldn't read that transcript.";
      this.chatMessages.update((msgs) => [
        ...msgs,
        { role: 'assistant', text: `⚠ ${message}` },
      ]);
      this.toast.show(message, 'error');
      this.loading.set(false);
    }
  }

  /** Chat submission while Undecided is checked — routes to
   * /api/explore-majors (pure conversation, no scheduling engine) instead
   * of onPromptSubmitted's normal plan pipeline. Shares the same visible
   * transcript (chatMessages) but never touches coursePlan/state.major. */
  async onExplorePromptSubmitted(prompt: string) {
    const text = prompt.trim();
    if (!text) return;

    const priorMessages = this.chatMessages();
    const lastAssistantReply = [...priorMessages].reverse().find((m) => m.role === 'assistant')?.text;
    const turnIndex = priorMessages.filter((m) => m.role === 'user').length;

    this.chatMessages.update((m) => [...m, { role: 'user', text }]);
    this.loading.set(true);
    try {
      const reply = await this.backend.exploreMajors({
        prompt: text,
        campus: this.state().campus,
        recent_reply: lastAssistantReply?.slice(0, 400),
        turn_index: turnIndex,
      });
      if (reply.trim()) {
        this.chatMessages.update((m) => [...m, { role: 'assistant', text: reply }]);
      }
    } catch (e) {
      console.error('Failed to fetch major-exploration reply:', e);
    } finally {
      this.loading.set(false);
    }
  }

  /** Demo-login entry point (see DemoLoginPageComponent) — seeds major/minors
   * then submits a real class-standing phrase ("I'm a junior") through the
   * exact same bulk-completion path a typed chat message would use, rather
   * than hand-listing which courses a demo student has "taken". That keeps
   * every demo profile's completed courses real and prereq-consistent
   * (derived from the actual degree plan) instead of invented data that
   * could silently drift from the plan JSON it's supposed to represent.
   * Resets to a clean slate first, matching what a fresh login implies. */
  async loginAsDemoStudent(major: string, standingPrompt: string, minors: string[] = [], campus?: string) {
    // Every profile so far has been University Park, so this never mattered
    // before -- but a campus-specific major (e.g. Behrend's MEBH) isn't in
    // the currently-cached degreePlans()/minorPlans() list unless we load
    // that campus's own lists first, same as a manual campus switch does.
    const targetCampus = campus ?? this.state().campus;
    if (campus && campus !== this.state().campus) {
      await this._loadPlansForCampus(campus);
    }
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
      campus: targetCampus,
      undecided: false,
      scheduledCourseIds: [],
    });
    // A different demo student is a fresh conversation, not a continuation
    // of whatever the last one (or a real visitor) was discussing.
    this.chatMessages.set([WELCOME_MESSAGE]);
    this.lastRecordedReply = '';
    // Deliberately NOT setting onboarded here -- callers each have their
    // own timing needs for when onboarding should visibly complete (e.g.
    // the welcome modal wants to finish its close animation first, rather
    // than @if hard-cutting it out from under an in-flight async call).
    await this.refreshPlan(standingPrompt);
  }

  /** Year-planning controls changed (start year / grad years / summer toggle). */
  async onPlanningChanged(settings: PlanningSettings) {
    const prev = this.state();
    // A start year moved into the past with nothing marked completed yet
    // means the plan is about to assume zero progress for someone who's
    // likely already partway through — ask instead of silently building a
    // plan that ignores however many semesters have already passed.
    const shouldAskAboutPastProgress =
      settings.startYear !== prev.startYear &&
      settings.startYear < new Date().getFullYear() &&
      prev.completed.length === 0;
    this.state.set({
      ...prev,
      startYear: settings.startYear,
      gradYears: settings.gradYears,
      allowSummer: settings.allowSummer,
      maxCreditsPerSemester: settings.maxCreditsPerSemester,
    });
    if (shouldAskAboutPastProgress) {
      this.chatMessages.update((m) => [
        ...m,
        {
          role: 'assistant',
          text:
            `Looks like you started college in ${settings.startYear} — have you already completed most of ` +
            `your courses? Upload your transcript (the + button below) or just tell me what you've taken ` +
            `(e.g. "I took CMPSC 131 and MATH 140") so your plan reflects what you've actually done, not a ` +
            `fresh start.`,
        },
      ]);
      this.chatOpen.set(true);
    }
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
    // The Flowchart page (where this button lives) doesn't require the chat
    // panel to be open, so the removal needs its own confirmation -- without
    // it the course just silently vanishes from the list.
    this.toast.show(`${code.trim().toUpperCase()} removed`);
    await this.refreshPlan('');
  }

  /** Weekly Schedule preview's "Add/Remove from schedule" -- purely a local
   * marker, no re-plan needed (unlike onRemoveCompleted above, this can't
   * change prereqs/progress/recommendations, so there's nothing to refetch). */
  toggleScheduled(code: string) {
    const normalized = code.trim().toUpperCase();
    const prev = this.state();
    const already = prev.scheduledCourseIds.includes(normalized);
    this.state.set({
      ...prev,
      scheduledCourseIds: already
        ? prev.scheduledCourseIds.filter((c) => c !== normalized)
        : [...prev.scheduledCourseIds, normalized],
    });
    this.toast.show(already ? `${normalized} removed from schedule` : `${normalized} added to schedule`);
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
      const plan = await this.backend.plan(toPlannerRequest(st, prompt, { recentReply, turnIndex }));

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
        mathPlacementTier: plan.state?.mathPlacementTier ?? st.mathPlacementTier,
      });
      this.coursePlan.set(plan);
      this._recordAssistantReply(plan);
    } catch (e) {
      console.error('Failed to fetch plan:', e);
      // Surfaced in-chat rather than silently swallowed -- without this, a
      // slow/unreachable backend (e.g. a cold-started Render instance, or
      // the advisor's LLM call timing out) just spins the loading state
      // and then quietly reverts with zero explanation, which reads as the
      // whole page having frozen rather than one request having failed.
      this.chatMessages.update((msgs) => [
        ...msgs,
        {
          role: 'assistant',
          text: this._describeFetchError(e),
        },
      ]);
    } finally {
      this.loading.set(false);
    }
  }

  private _recordTranscriptUpload() {
    const now = Date.now();
    this.lastTranscriptUpload.set(now);
    try {
      localStorage.setItem(TRANSCRIPT_UPLOAD_KEY, String(now));
    } catch {
      // A full/blocked localStorage shouldn't break the upload itself, just
      // the "remind me next semester" part surviving a reload.
    }
  }

  private _readTranscriptUpload(): number | null {
    try {
      const v = localStorage.getItem(TRANSCRIPT_UPLOAD_KEY);
      return v ? Number(v) : null;
    } catch {
      return null;
    }
  }

  /** Part of resetToDefault() -- the "your transcript may be stale" nudge
   * is keyed by browser, not by account, so without this it would keep
   * referencing whichever student last uploaded a transcript in this
   * browser instead of resetting for the next student (or anonymous
   * visitor) who signs in/out here. */
  private _clearTranscriptUpload(): void {
    this.lastTranscriptUpload.set(null);
    try {
      localStorage.removeItem(TRANSCRIPT_UPLOAD_KEY);
    } catch {
      // Best-effort, same as _recordTranscriptUpload above.
    }
  }

  private _describeFetchError(e: unknown): string {
    const err = e as { name?: string; status?: number } | null;
    if (err?.name === 'TimeoutError') {
      return "⚠ That took too long to respond (the server may be waking up from being idle, or the advisor is under heavy load right now) — please try again in a moment.";
    }
    if (err?.status === 0) {
      return '⚠ Could not reach the server — check your connection and try again.';
    }
    return '⚠ Something went wrong fetching your plan. Please try again.';
  }

  /** Appends the backend's reply (and any matched/removed/unmatched-course
   * preamble) to the persistent transcript. Runs for every plan refresh,
   * not just chat submissions — a Setup/settings change re-plans too and
   * its resulting reply belongs in the same history. Deduped against the
   * last recorded reply so an unrelated refresh with unchanged text
   * doesn't spam a duplicate bubble. */
  private _recordAssistantReply(plan: CoursePlan) {
    const reply = (plan.rag_response || '').trim();
    if (!reply || reply === this.lastRecordedReply) return;
    this.lastRecordedReply = reply;

    const m = plan.matched;
    const parts: ChatMessage[] = [];
    if (m && m.courses.length && m.treatedAsCompleted) {
      parts.push({
        role: 'assistant',
        text: '✓ Matched your courses: ' + m.courses.map((c) => `${c.code} (${c.name})`).join(', '),
      });
    }
    if (m && m.removed?.length) {
      parts.push({
        role: 'assistant',
        text: '➖ Removed from completed: ' + m.removed.map((c) => `${c.code} (${c.name})`).join(', '),
      });
    }
    if (m && m.summerUnavailable?.length) {
      parts.push({
        role: 'assistant',
        text:
          '☀️ Noted as not offered in summer — plan adjusted: ' +
          m.summerUnavailable.map((c) => c.code).join(', '),
      });
    }
    if (m && m.unmatched.length) {
      parts.push({ role: 'assistant', text: '⚠ Couldn’t match: ' + m.unmatched.join(', ') });
    }
    parts.push({ role: 'assistant', text: reply, links: plan.replyLinks });
    this.chatMessages.update((msgs) => [...msgs, ...parts]);
  }
}
