import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { CoursePlanResponse } from '../models/course-plan.model';

@Injectable({ providedIn: 'root' })
export class PlannerService {
  // Kept for backward-compatibility; BackendService is the preferred API wrapper.
  loading = signal(false);

  constructor(private http: HttpClient) {}

  async generatePlan(payload: {
    dept: string;
    prompt: string;
    completed: string[];
    semantic_query?: string;
    search_query?: string;
    why_not_query?: string;
  }): Promise<CoursePlanResponse> {
    this.loading.set(true);
    try {
      return await firstValueFrom(
        this.http.post<CoursePlanResponse>('/api/plan', payload)
      );
    } finally {
      this.loading.set(false);
    }
  }
}
