import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { GraphPayload, LlmFlowchart } from '../../models/course-plan.model';

@Component({
  selector: 'app-flowchart',
  standalone: true,
  templateUrl: './flowchart.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FlowchartComponent {
  // Backend response fields
  graph = input<GraphPayload | undefined>();
  llm = input<LlmFlowchart | undefined>();
  isLoading = input.required<boolean>();

  // Prefer LLM mermaid if present, otherwise show nothing (the vis-network graph can be wired later)
  mermaidCode = computed(() => this.llm()?.mermaid ?? '');
  explanation = computed(() => this.llm()?.explanation ?? '');
}
