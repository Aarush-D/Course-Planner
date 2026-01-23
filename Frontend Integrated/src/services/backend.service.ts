import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { CoursePlan } from '../models/course-plan.model';

export interface PlannerRequest {
  dept: string;
  prompt: string;
  completed: string[];
}

@Injectable({ providedIn: 'root' })
export class BackendService {
  private readonly http = inject(HttpClient);

  async plan(req: PlannerRequest): Promise<CoursePlan> {
    const res = await firstValueFrom(this.http.post<any>('/api/plan', req));

    // backend returns either {coursePlan: {...}} or direct object
    const plan = (res?.coursePlan ?? res) as CoursePlan;
    return plan;
  }
}
