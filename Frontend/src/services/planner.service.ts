import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class PlannerService {
  loading = signal(false);

  constructor(private http: HttpClient) {}

  async generatePlan(payload: {
    dept: string;
    prompt: string;
    completed: string[];
    semantic_query?: string;
    search_query?: string;
    why_not_query?: string;
  }): Promise<any> {
    this.loading.set(true);
    try {
      const res: any = await firstValueFrom(
        this.http.post('/api/plan', payload)
      );
  
      return {
        flowchart: res.graph,
        recommendations: res.rag_response
          ? res.rag_response.split('\n').filter(Boolean)
          : [],
        explanation: res.llm_flowchart?.explanation ?? ''
      };
    } finally {
      this.loading.set(false);
    }
  }
}