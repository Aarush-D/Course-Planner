import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { ChatbotComponent } from './components/chatbot/chatbot.component';
import { FlowchartComponent } from './components/flowchart/flowchart.component';
import { RecommendationsComponent } from './components/recommendations/recommendations.component';
import { GeminiService } from './services/gemini.service';

@Component({
  selector: 'app-root',
  standalone: true, // ✅ ADD THIS
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChatbotComponent, FlowchartComponent, RecommendationsComponent],
})
export class AppComponent {
  private readonly geminiService = inject(GeminiService);

  coursePlan = signal(null);
  loading = this.geminiService.loading;

  async onPromptSubmitted(prompt: string) {
    this.coursePlan.set(null);
    const plan = await this.geminiService.generateCoursePlan(prompt);
    this.coursePlan.set(plan);
  }
}