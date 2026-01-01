import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { CoursePlan } from '../models/course-plan.model';

@Injectable({ providedIn: 'root' })
export class GeminiService {
  private readonly http = inject(HttpClient);

  // Your UI already uses this
  loading = signal(false);

  async generateCoursePlan(prompt: string): Promise<CoursePlan> {
    this.loading.set(true);
    try {
      // proxy.conf.json should forward /api to Flask :5000
      const res$ = this.http.post<CoursePlan>('/api/plan', { prompt });
      return await firstValueFrom(res$);
    } finally {
      this.loading.set(false);
    }
  }
}