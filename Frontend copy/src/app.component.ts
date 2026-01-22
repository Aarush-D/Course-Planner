import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';

import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { FlowchartComponent } from './components/flowchart/flowchart.component';
import { RecommendationsComponent } from './components/recommendations/recommendations.component';
import { BackendService } from './services/backend.service';
import type { CoursePlanResponse } from './models/course-plan.model';

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChatbotComponent, FlowchartComponent, RecommendationsComponent],
})
export class AppComponent {
  private readonly backend = inject(BackendService);

  coursePlan = signal<CoursePlanResponse | null>(null);
  loading = signal(false);

  async onPromptSubmitted(prompt: string) {
    this.loading.set(true);
    this.coursePlan.set(null);

    try {
      const res = await this.backend.askPlanner(prompt);
      this.coursePlan.set(res);
    } catch (e) {
      console.error('Failed to fetch plan:', e);
      this.coursePlan.set(null);
    } finally {
      this.loading.set(false);
    }
  }
}
