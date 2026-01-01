import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { CoursePlan } from './models/course-plan.model';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
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

  async onPromptSubmitted(prompt: string) {
    this.loading.set(true);
    this.coursePlan.set(null);

    try {
      const res = await this.backend.askPlanner(prompt);
      // Support either {coursePlan: ...} or direct plan object
      const plan = (res?.coursePlan ?? res?.plan ?? res) as CoursePlan;
      this.coursePlan.set(plan);
    } catch (e) {
      console.error('Failed to fetch plan:', e);
      this.coursePlan.set(null);
    } finally {
      this.loading.set(false);
    }
  }
}