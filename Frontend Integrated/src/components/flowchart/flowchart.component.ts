import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  afterNextRender,
  effect,
  input,
  signal,
  viewChild,
} from '@angular/core';
import mermaid from 'mermaid';
import { Course, LlmFlowchart } from '../../models/course-plan.model';

@Component({
  selector: 'app-flowchart',
  standalone: true,
  templateUrl: './flowchart.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FlowchartComponent {
  isLoading = input.required<boolean>();
  llm = input<LlmFlowchart | null>();
  courses = input<Course[] | null>();

  private readonly mermaidHost = viewChild.required<ElementRef<HTMLDivElement>>('mermaidHost');

  mermaidError = signal<string | null>(null);

  constructor() {
    // Mermaid needs DOM to exist. We initialize after first render.
    afterNextRender(() => {
      mermaid.initialize({ startOnLoad: false });
    });

    effect(() => {
      const isLoading = this.isLoading();
      const llm = this.llm();
      if (isLoading) return;

      const code = llm?.mermaid?.trim();
      if (!code) {
        this._clearMermaid();
        return;
      }

      // Render diagram
      this._renderMermaid(code);
    });
  }

  private _clearMermaid() {
    this.mermaidError.set(null);
    const el = this.mermaidHost().nativeElement;
    el.innerHTML = '';
  }

  private async _renderMermaid(code: string) {
    this.mermaidError.set(null);
    const el = this.mermaidHost().nativeElement;

    // Mermaid is picky: ensure it starts with flowchart
    const normalized = code.match(/^\s*flowchart\s+/i) ? code : `flowchart TD\n${code}`;

    try {
      const { svg } = await mermaid.render(`mmd-${Date.now()}`, normalized);
      el.innerHTML = svg;
    } catch (e: any) {
      this._clearMermaid();
      this.mermaidError.set(e?.message ?? 'Failed to render Mermaid diagram');
    }
  }
}
