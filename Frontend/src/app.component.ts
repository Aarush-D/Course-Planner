import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { PlannerService } from './services/planner.service';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { FlowchartComponent } from './components/flowchart/flowchart.component';
import { RecommendationsComponent } from './components/recommendations/recommendations.component';

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChatbotComponent, FlowchartComponent, RecommendationsComponent],
})
export class AppComponent {
  private readonly planner = inject(PlannerService);

  loading = this.planner.loading;

  // store backend response (you can strongly type this later)
  plan = signal<any | null>(null);

  async onPromptSubmitted(prompt: string) {
    // for now: hardcode dept + completed; later wire these to UI controls
    const res = await this.planner.generatePlan({
      dept: 'CMPSC',
      prompt,
      completed: [],
    });

    this.plan.set(res);
  }
}