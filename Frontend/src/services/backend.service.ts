import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import type {
  Course,
  CoursePlan,
  DegreePlanInfo,
  FullPlan,
  Graph,
  LlmFlowchart,
  MatchedInfo,
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

  async degreePlans(): Promise<DegreePlanInfo[]> {
    try {
      const res = await firstValueFrom(this.http.get<any>('/api/degree-plans'));
      return Array.isArray(res?.plans) ? res.plans : [];
    } catch {
      return [];
    }
  }

  async plan(req: PlannerRequest): Promise<CoursePlan> {
    const res = await firstValueFrom(this.http.post<any>('/api/plan', req));

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
