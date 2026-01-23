import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { CoursePlan } from './models/course-plan.model';
import { ChatbotComponent, PromptPayload } from './components/chatbot/chatbot.component';
import { FlowchartComponent } from './components/flowchart/flowchart.component';
import { RecommendationsComponent } from './components/recommendations/recommendations.component';
import { BackendService } from './services/backend.service';

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChatbotComponent, FlowchartComponent, RecommendationsComponent],
})
export class AppComponent {
  private readonly backend = inject(BackendService);

  coursePlan = signal<CoursePlan | null>(null);
  loading = signal(false);

  async onPromptSubmitted(payload: PromptPayload) {
    this.loading.set(true);

    try {
      const plan = await this.backend.plan({
        dept: payload.dept,
        prompt: payload.prompt,
        completed: payload.completed ?? [],
      });
      this.coursePlan.set(plan);
    } catch (e) {
      console.error('Failed to fetch plan:', e);
      this.coursePlan.set(null);
    } finally {
      this.loading.set(false);
    }
  }
}
