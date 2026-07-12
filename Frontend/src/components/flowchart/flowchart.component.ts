import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  afterNextRender,
  computed,
  effect,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import mermaid from 'mermaid';
import { Course, FullPlan, LlmFlowchart, Progress } from '../../models/course-plan.model';

@Component({
  selector: 'app-flowchart',
  standalone: true,
  templateUrl: './flowchart.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FlowchartComponent {
  isLoading      = input.required<boolean>();
  llm            = input<LlmFlowchart | null>();
  courses        = input<Course[] | null>();      // completed + recommended cards from backend
  completedCodes = input<string[]>([]);           // raw completed codes from app state
  fullPlan       = input<FullPlan | null>();
  progress       = input<Progress | null>();
  unlockMap      = input<LlmFlowchart | null>();  // completed -> next -> future map

  removeCompleted = output<string>();

  // Optional (not required): the host divs live inside @if branches, so they
  // can be absent while loading — reading a required query then throws NG0951.
  private readonly mermaidHost =
    viewChild<ElementRef<HTMLDivElement>>('mermaidHost');
  private readonly unlockHost =
    viewChild<ElementRef<HTMLDivElement>>('unlockHost');

  mermaidError = signal<string | null>(null);
  unlockError = signal<string | null>(null);

  progressPct = computed(() => {
    const p = this.progress();
    if (!p || !p.totalCredits) return 0;
    return Math.min(100, Math.round((p.creditsDone / p.totalCredits) * 100));
  });

  // ── Split cards into completed vs recommended ────────────────────────────
  completedCourses = computed<Course[]>(() => {
    const codes = new Set(
      (this.completedCodes() ?? []).map((c) => c.trim().toUpperCase())
    );
    return (this.courses() ?? []).filter((c) =>
      codes.has(c.id.trim().toUpperCase())
    );
  });

  recommendedCourses = computed<Course[]>(() => {
    const codes = new Set(
      (this.completedCodes() ?? []).map((c) => c.trim().toUpperCase())
    );
    return (this.courses() ?? []).filter(
      (c) => !codes.has(c.id.trim().toUpperCase())
    );
  });

  constructor() {
    afterNextRender(() => {
      mermaid.initialize({ startOnLoad: false });
    });

    effect(() => {
      const host = this.mermaidHost(); // tracked: effect re-runs once the div exists
      const isLoading = this.isLoading();
      const llm = this.llm();
      if (isLoading || !host) return;

      const code = llm?.mermaid?.trim();
      if (!code) {
        this._clearHost(host, this.mermaidError);
        return;
      }
      this._renderInto(host, code, this.mermaidError, 'mmd');
    });

    effect(() => {
      const host = this.unlockHost();
      const isLoading = this.isLoading();
      const map = this.unlockMap();
      if (isLoading || !host) return;

      const code = map?.mermaid?.trim();
      if (!code) {
        this._clearHost(host, this.unlockError);
        return;
      }
      this._renderInto(host, code, this.unlockError, 'unlock');
    });
  }

  onRemove(code: string) {
    this.removeCompleted.emit(code);
  }

  formatCredits(c: number | null | undefined): string {
    if (c === null || c === undefined) return '';
    return Number.isInteger(c) ? `${c} cr` : `${c} cr`;
  }

  private _clearHost(
    host: ElementRef<HTMLDivElement>,
    error: ReturnType<typeof signal<string | null>>
  ) {
    error.set(null);
    host.nativeElement.innerHTML = '';
  }

  private async _renderInto(
    host: ElementRef<HTMLDivElement>,
    code: string,
    error: ReturnType<typeof signal<string | null>>,
    idPrefix: string
  ) {
    error.set(null);
    const normalized = code.match(/^\s*flowchart\s+/i)
      ? code
      : `flowchart TD\n${code}`;
    try {
      const { svg } = await mermaid.render(`${idPrefix}-${Date.now()}`, normalized);
      host.nativeElement.innerHTML = svg;
    } catch (e: any) {
      this._clearHost(host, error);
      error.set(e?.message ?? 'Failed to render Mermaid diagram');
    }
  }
}
