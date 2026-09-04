import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom, timeout } from 'rxjs';

// The backend's own worst-case single Ollama attempt is OLLAMA_TIMEOUT_S
// (25s by default, see Backend/app.py) plus the deterministic planning
// work itself -- 45s gives real headroom above that without leaving a
// genuinely stuck request to hang indefinitely with no feedback (the
// original bug: no client-side timeout at all on this call).
const PLAN_REQUEST_TIMEOUT_MS = 45_000;
import { environment } from '../environments/environment';
import type {
  AmbiguousGenEdCourse,
  Course,
  CourseGraphEntry,
  CoursePlan,
  DegreePlanInfo,
  FullPlan,
  GenEdDetail,
  GenEdSlot,
  Graph,
  LlmFlowchart,
  MatchedInfo,
  MinorPlanInfo,
  NextSemester,
  PlannerStateInfo,
  Progress,
  Recommendation,
} from '../models/course-plan.model';

/** One domain's static entry from GET /api/gen-ed-courses, camelCased for
 * the frontend -- see BackendService.genEdCourses(). */
export interface GenEdDomainInfo {
  name: string;
  creditsRequired: number;
  courses: { code: string; title: string; credits: string }[];
}

/** An in-progress "switch major to X" (and/or add/remove minors) the
 * student hasn't yet confirmed or cancelled -- see
 * PlannerState.pendingMajorChange in planner-state.service.ts, the field
 * this type also backs on the wire (PlannerRequest.pending_major_change
 * below, and PlannerStateInfo.pendingMajorChange via the module
 * augmentation further down this file). Round-trips opaquely: the backend
 * doesn't need to interpret its shape, just echo back whatever it last set
 * so the next turn can tell whether this message is a confirm/cancel of it. */
export type PendingMajorChange = {
  toMajor: string | null;
  addMinors?: string[];
  removeMinors?: string[];
};

export interface PlannerRequest {
  major: string;
  catalog_year?: number;
  prompt: string;
  completed: string[];
  start_year?: number;
  grad_years?: number;
  allow_summer?: boolean;
  summer_unavailable?: string[];
  // Non-course items a prior bulk-completion phrase marked done — see
  // PlannerState.consumedSlotIds for why the client must re-send this.
  consumed_slot_ids?: number[];
  // An ALEKS score or "I took calc in high school" mentioned in an earlier
  // turn — same persist-and-resend reason as consumed_slot_ids. See
  // PlannerState.mathPlacementTier.
  math_placement_tier?: number;
  // Lets the backend vary its reply's opening line instead of repeating the
  // same one every turn — the excerpt of its own last reply plus how many
  // prior turns this conversation has had.
  recent_reply?: string;
  turn_index?: number;
  // Double/triple/quad major / minors — entirely optional; omitting all of
  // them keeps the response identical to a single-major request.
  // second_major is the original single-extra-major field; additional_majors
  // holds any majors beyond that (a 3rd, 4th, ...).
  second_major?: string;
  additional_majors?: string[];
  minors?: string[];
  // How many credits to pack into each simulated term at most -- omitted
  // means the backend picks its own default (the plan's own
  // max_credits_per_semester, or 17). See PlannerState.maxCreditsPerSemester.
  max_credits?: number;
  // Courses the student explicitly asked for / asked to avoid -- a priority
  // signal for the deterministic engine, never a way to bypass real
  // eligibility rules (wanted_codes boosts an eligible-but-optional pick;
  // excluded_codes hard-filters a course out of every recommendation/plan
  // slot even when it would otherwise qualify). Same 300-entry cap pattern
  // as `completed`. See PlannerState.wantedCourses/excludedCourses.
  wanted_courses?: string[];
  excluded_courses?: string[];
  // See PendingMajorChange above / PlannerState.pendingMajorChange. null (or
  // omitted) means nothing pending.
  pending_major_change?: PendingMajorChange | null;
  // Course code -> the ONE Gen Ed domain code the student wants that course
  // credited toward, for a completed course eligible for more than one open
  // domain slot in this plan (see CoursePlan.genEdDetail.ambiguousCourses).
  // Only relevant for a genuinely ambiguous course; ignored (never errors)
  // for every other course, an override naming a domain the course isn't
  // eligible for, or a nonexistent course/domain -- validated server-side.
  // See PlannerState.genEdOverrides. Deliberately camelCase on the wire
  // (unlike every other field on this interface) -- matches the fixed
  // /api/plan contract exactly, not this file's usual snake_case convention.
  genEdOverrides?: Record<string, string>;
}

// Merges the three fields above onto the shared PlannerStateInfo type
// (declared in course-plan.model.ts, out of scope for this pass) so the
// backend's echo of them back in `plan.state` type-checks the same way
// completed/summerUnavailable/consumedSlotIds/etc already do -- see
// PlannerStateService.refreshPlan's `plan.state?.x ?? st.x` reads.
declare module '../models/course-plan.model' {
  interface PlannerStateInfo {
    wantedCourses?: string[];
    excludedCourses?: string[];
    pendingMajorChange?: PendingMajorChange | null;
  }
}

/** The plan-context subset POST /api/gen-ed-autofill needs -- the SAME
 * fields POST /api/plan already parses (major/catalog_year/start_year/
 * second_major/additional_majors/minors/completed/excluded_courses/
 * wanted_courses), minus everything that's irrelevant to a single-slot
 * lookup (prompt, chat-turn bookkeeping, genEdOverrides, etc.). See
 * GenEdPageComponent's own context-builder for how a live PlannerState
 * maps onto this, mirroring toPlannerRequest()'s mapping for the same
 * fields exactly. */
export type GenEdAutofillContext = Pick<
  PlannerRequest,
  | 'major'
  | 'catalog_year'
  | 'start_year'
  | 'second_major'
  | 'additional_majors'
  | 'minors'
  | 'completed'
  | 'excluded_courses'
  | 'wanted_courses'
>;

export interface GenEdAutofillResult {
  code: string;
  name: string;
  credits: number;
}

function isCourse(x: any): x is Course {
  return (
    x &&
    typeof x.id === 'string' &&
    typeof x.name === 'string' &&
    Array.isArray(x.prerequisites)
  );
}

function isRecommendation(x: any): x is Recommendation {
  return x && typeof x.name === 'string' && typeof x.reason === 'string';
}

function isLlmFlowchart(x: any): x is LlmFlowchart {
  return x && typeof x.mermaid === 'string' && typeof x.explanation === 'string';
}

function isGraph(x: any): x is Graph {
  return x && Array.isArray(x.nodes) && Array.isArray(x.edges);
}

function toGenEdSlot(x: any): GenEdSlot | null {
  if (!x || typeof x.id !== 'number' || typeof x.label !== 'string' || !Array.isArray(x.domains)) {
    return null;
  }
  return {
    id: x.id,
    label: x.label,
    domains: x.domains.filter((d: any) => typeof d === 'string'),
    isChoice: !!x.isChoice,
    credits: typeof x.credits === 'number' ? x.credits : 0,
    done: !!x.done,
    satisfiedBy: typeof x.satisfiedBy === 'string' ? x.satisfiedBy : null,
  };
}

function toAmbiguousGenEdCourse(x: any): AmbiguousGenEdCourse | null {
  if (!x || typeof x.code !== 'string' || typeof x.name !== 'string') return null;
  return {
    code: x.code,
    name: x.name,
    eligibleDomains: Array.isArray(x.eligibleDomains)
      ? x.eligibleDomains.filter((d: any) => typeof d === 'string')
      : [],
    currentDomain: typeof x.currentDomain === 'string' ? x.currentDomain : '',
  };
}

function toGenEdDetail(x: any): GenEdDetail | undefined {
  if (!x || !Array.isArray(x.slots)) return undefined;
  return {
    slots: x.slots.map(toGenEdSlot).filter((s: GenEdSlot | null): s is GenEdSlot => s !== null),
    ambiguousCourses: Array.isArray(x.ambiguousCourses)
      ? x.ambiguousCourses
          .map(toAmbiguousGenEdCourse)
          .filter((c: AmbiguousGenEdCourse | null): c is AmbiguousGenEdCourse => c !== null)
      : [],
  };
}

@Injectable({ providedIn: 'root' })
export class BackendService {
  private readonly http = inject(HttpClient);

  // Empty in dev (relative /api/... path, handled by proxy.conf.json);
  // the real Render origin in a production build (see
  // src/environments/environment.prod.ts and docs/HOSTING_PLAN.md) — a
  // static build has no dev-server proxy to fall back on, so every
  // request needs the backend's actual origin once deployed away from
  // localhost.
  private readonly base = environment.apiBaseUrl;

  async campuses(): Promise<{ campuses: string[]; default: string }> {
    try {
      const res = await firstValueFrom(this.http.get<any>(`${this.base}/api/campuses`));
      return {
        campuses: Array.isArray(res?.campuses) ? res.campuses : ['University Park'],
        default: typeof res?.default === 'string' ? res.default : 'University Park',
      };
    } catch {
      return { campuses: ['University Park'], default: 'University Park' };
    }
  }

  async degreePlans(campus?: string): Promise<DegreePlanInfo[]> {
    try {
      const params = campus ? { campus } : {};
      const res = await firstValueFrom(this.http.get<any>(`${this.base}/api/degree-plans`, { params }));
      return Array.isArray(res?.plans) ? res.plans : [];
    } catch {
      return [];
    }
  }

  async minorPlans(campus?: string): Promise<MinorPlanInfo[]> {
    try {
      const params = campus ? { campus } : {};
      const res = await firstValueFrom(this.http.get<any>(`${this.base}/api/minor-plans`, { params }));
      return Array.isArray(res?.minors) ? res.minors : [];
    } catch {
      return [];
    }
  }

  /** Every course in one major's catalog, with its real prereqs/unlocks —
   * backs the Flowchart page's course-explorer search (Backend/app.py's
   * /api/course-graph). Scoped to a single major, not a student's full
   * merged plan with minors/additional majors. */
  async courseGraph(major: string, catalogYear?: number): Promise<CourseGraphEntry[]> {
    try {
      const params: Record<string, string> = { major };
      if (catalogYear) params['catalog_year'] = String(catalogYear);
      const res = await firstValueFrom(this.http.get<any>(`${this.base}/api/course-graph`, { params }));
      return Array.isArray(res?.courses) ? res.courses : [];
    } catch {
      return [];
    }
  }

  /** Undecided-major mode — pure conversation, no scheduling engine
   * involved (there's no plan yet). See Backend/app.py's
   * /api/explore-majors and _real_majors_summary. */
  async exploreMajors(req: {
    prompt: string;
    campus?: string;
    recent_reply?: string;
    turn_index?: number;
  }): Promise<string> {
    try {
      const res = await firstValueFrom(this.http.post<any>(`${this.base}/api/explore-majors`, req));
      return typeof res?.reply === 'string' ? res.reply : '';
    } catch {
      return '';
    }
  }

  /** Upload a PDF transcript instead of typing courses one by one — the
   * backend extracts its text and runs it through the exact same
   * real-catalog course matcher chat-typed course mentions already go
   * through (Backend/app.py's /api/parse-transcript). Throws with a
   * readable message on a real failure (not a PDF, unreadable, no text)
   * so the caller can show it, rather than swallowing it like
   * exploreMajors does — a silent failure here would look like the
   * upload just did nothing. */
  async parseTranscript(
    file: File,
    context: {
      major: string;
      catalog_year?: number;
      start_year?: number;
      second_major?: string;
      additional_majors?: string[];
      minors?: string[];
    },
  ): Promise<{ matched: { code: string; name: string; credits: number }[]; unmatched: string[] }> {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('major', context.major);
    if (context.catalog_year) form.append('catalog_year', String(context.catalog_year));
    if (context.start_year) form.append('start_year', String(context.start_year));
    if (context.second_major) form.append('second_major', context.second_major);
    for (const m of context.additional_majors ?? []) form.append('additional_majors', m);
    for (const m of context.minors ?? []) form.append('minors', m);

    try {
      const res = await firstValueFrom(
        this.http.post<any>(`${this.base}/api/parse-transcript`, form),
      );
      return {
        matched: Array.isArray(res?.matched) ? res.matched : [],
        unmatched: Array.isArray(res?.unmatched) ? res.unmatched : [],
      };
    } catch (e: any) {
      const message = e?.error?.error || e?.message || 'Could not read that transcript.';
      throw new Error(message);
    }
  }

  async plan(req: PlannerRequest): Promise<CoursePlan> {
    const res = await firstValueFrom(
      this.http.post<any>(`${this.base}/api/plan`, req).pipe(timeout(PLAN_REQUEST_TIMEOUT_MS)),
    );

    // Prefer the structured payload if present
    const raw = (res?.coursePlan ?? res) as any;

    const rawFlow = Array.isArray(raw?.flowchart) ? raw.flowchart : [];
    const rawRecs = Array.isArray(raw?.recommendations) ? raw.recommendations : [];

    const flowchart: Course[] = rawFlow.filter(isCourse);
    const recommendations: Recommendation[] = rawRecs.filter(isRecommendation);
    const graph: Graph = isGraph(raw?.graph) ? raw.graph : { nodes: [], edges: [] };

    const matched: MatchedInfo | undefined =
      raw?.matched && Array.isArray(raw.matched.courses) ? raw.matched : undefined;

    const nextSemester: NextSemester | undefined =
      raw?.nextSemester && Array.isArray(raw.nextSemester.courses)
        ? raw.nextSemester
        : undefined;

    const fullPlan: FullPlan | undefined =
      raw?.fullPlan && Array.isArray(raw.fullPlan.terms) ? raw.fullPlan : undefined;

    const progress: Progress | undefined =
      raw?.progress && typeof raw.progress.totalItems === 'number'
        ? raw.progress
        : undefined;

    const genEdDetail = toGenEdDetail(raw?.genEdDetail);

    return {
      major: typeof raw?.major === 'string' ? raw.major : req.major,
      catalogYear:
        typeof raw?.catalogYear === 'number' ? raw.catalogYear : req.catalog_year,
      dept: typeof raw?.dept === 'string' ? raw.dept : req.major,
      completed: Array.isArray(raw?.completed) ? raw.completed : req.completed,
      eligible: Array.isArray(raw?.eligible) ? raw.eligible : [],
      graph,
      rag_response: typeof raw?.rag_response === 'string' ? raw.rag_response : '',
      flowchart,
      recommendations,
      tips: Array.isArray(raw?.tips)
        ? raw.tips.filter((t: any) => typeof t === 'string')
        : [],
      llm_flowchart: isLlmFlowchart(raw?.llm_flowchart) ? raw.llm_flowchart : undefined,
      unlockMap: isLlmFlowchart(raw?.unlockMap) ? raw.unlockMap : undefined,
      semesterFlowchart: isLlmFlowchart(raw?.semesterFlowchart) ? raw.semesterFlowchart : undefined,
      lowCostMinors: Array.isArray(raw?.lowCostMinors) ? raw.lowCostMinors : undefined,
      replyLinks: Array.isArray(raw?.replyLinks) ? raw.replyLinks : undefined,
      state:
        raw?.state && Array.isArray(raw.state.completed)
          ? (raw.state as PlannerStateInfo)
          : undefined,
      matched,
      nextSemester,
      fullPlan,
      progress,
      genEdDetail,
    };
  }

  // Static data (data/gen_ed_courses.json) fetched once and cached for the
  // life of the app -- it never changes mid-session, so every caller after
  // the first (e.g. revisiting the Gen Ed page) reuses the same in-flight
  // or resolved promise instead of re-requesting it. Mirrors the lru_cache
  // the backend already applies to load_gen_ed_courses() itself.
  private _genEdCoursesPromise?: Promise<Record<string, GenEdDomainInfo>>;

  async genEdCourses(): Promise<Record<string, GenEdDomainInfo>> {
    if (!this._genEdCoursesPromise) {
      this._genEdCoursesPromise = this._fetchGenEdCourses();
    }
    try {
      return await this._genEdCoursesPromise;
    } catch (e) {
      // Don't leave a failed fetch cached forever -- a transient network
      // error (or the endpoint not existing yet during parallel backend
      // work) shouldn't permanently blank the page for the rest of the
      // session; the next call retries.
      this._genEdCoursesPromise = undefined;
      throw e;
    }
  }

  /** One Gen Ed slot's "pick a real course for me" button (Gen Ed page) --
   * POST /api/gen-ed-autofill, reusing the exact plan-context fields
   * /api/plan already requires plus the one new `domain` field. Resolves
   * to null both on a legitimate empty result (the response's own
   * `{code: null}` for an invalid domain / a Firewall blocking every
   * course / no eligible course) and on a genuine network/parse failure --
   * same soft-fail convention as exploreMajors() above, so a caller never
   * has to distinguish "nothing eligible" from "couldn't reach the
   * backend" and both just read as "no course to offer right now". */
  async genEdAutofill(
    domain: string,
    context: GenEdAutofillContext,
  ): Promise<GenEdAutofillResult | null> {
    try {
      const res = await firstValueFrom(
        this.http.post<any>(`${this.base}/api/gen-ed-autofill`, { ...context, domain }),
      );
      if (!res || typeof res.code !== 'string') return null;
      return {
        code: res.code,
        name: typeof res.name === 'string' ? res.name : '',
        credits: typeof res.credits === 'number' ? res.credits : 0,
      };
    } catch (e) {
      console.error('Failed to auto-fill Gen Ed slot:', e);
      return null;
    }
  }

  private async _fetchGenEdCourses(): Promise<Record<string, GenEdDomainInfo>> {
    const res = await firstValueFrom(this.http.get<any>(`${this.base}/api/gen-ed-courses`));
    const out: Record<string, GenEdDomainInfo> = {};
    if (!res || typeof res !== 'object') return out;
    for (const [domain, entry] of Object.entries<any>(res)) {
      if (!entry || typeof entry.name !== 'string') continue;
      out[domain] = {
        name: entry.name,
        creditsRequired: typeof entry.credits_required === 'number' ? entry.credits_required : 0,
        courses: Array.isArray(entry.courses)
          ? entry.courses
              .filter((c: any) => c && typeof c.code === 'string' && typeof c.title === 'string')
              .map((c: any) => ({ code: c.code, title: c.title, credits: String(c.credits ?? '') }))
          : [],
      };
    }
    return out;
  }
}
