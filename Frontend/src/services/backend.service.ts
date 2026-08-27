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
  Course,
  CoursePlan,
  DegreePlanInfo,
  FullPlan,
  Graph,
  LlmFlowchart,
  MatchedInfo,
  MinorPlanInfo,
  NextSemester,
  PlannerStateInfo,
  Progress,
  Recommendation,
} from '../models/course-plan.model';

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
    };
  }
}
