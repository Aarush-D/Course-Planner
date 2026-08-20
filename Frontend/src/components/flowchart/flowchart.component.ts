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
  semesterFlowchart = input<LlmFlowchart | null>(); // full path, green/red/grey per term

  removeCompleted = output<string>();

  // Optional (not required): the host divs live inside @if branches, so they
  // can be absent while loading — reading a required query then throws NG0951.
  private readonly mermaidHost =
    viewChild<ElementRef<HTMLDivElement>>('mermaidHost');
  private readonly unlockHost =
    viewChild<ElementRef<HTMLDivElement>>('unlockHost');
  private readonly semesterFlowchartHost =
    viewChild<ElementRef<HTMLDivElement>>('semesterFlowchartHost');

  mermaidError = signal<string | null>(null);
  unlockError = signal<string | null>(null);
  semesterFlowchartError = signal<string | null>(null);

  // Path to Graduation: toggle between the card grid and the semester
  // flowchart view (green completed / red next term / grey future).
  pathView = signal<'cards' | 'flowchart'>('cards');

  // Mermaid's own useMaxWidth behavior forces a large diagram to shrink to
  // fit its container — for a partly-completed plan this diagram can carry
  // 40-60+ nodes (every completed course plus every remaining term), which
  // squished into a narrow strip renders as tiny, unreadable text. Instead
  // it's rendered at native size (see mermaid.initialize below) and scaled
  // here via a CSS transform the student controls directly, starting at a
  // computed "fit to container" baseline so it opens legible either way.
  semesterZoom = signal(1);
  semesterZoomPercent = computed(() => Math.round(this.semesterZoom() * 100));
  private semesterFitScale = 1;

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
      mermaid.initialize({
        startOnLoad: false,
        // Native size + wide spacing — legibility over Mermaid's default
        // shrink-to-fit-container behavior, which is what made a 40-60+
        // node semester flowchart (every completed course plus every
        // remaining term) render as an unreadably tiny wall of text. Every
        // host div already scrolls (overflow-x-auto), so a diagram wider
        // than its container scrolls instead of squishing; the semester
        // flowchart additionally gets its own zoom controls (semesterZoom)
        // since it's by far the largest of the three.
        flowchart: { useMaxWidth: false, nodeSpacing: 35, rankSpacing: 65, padding: 12 },
        themeVariables: { fontSize: '14px' },
      });
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

    effect(() => {
      const host = this.semesterFlowchartHost();
      const isLoading = this.isLoading();
      const view = this.pathView();
      const sf = this.semesterFlowchart();
      if (isLoading || !host || view !== 'flowchart') return;

      const code = sf?.mermaid?.trim();
      if (!code) {
        this._clearHost(host, this.semesterFlowchartError);
        return;
      }
      this._renderInto(host, code, this.semesterFlowchartError, 'semflow').then(() =>
        this._fitSemesterFlowchart(host),
      );
    });
  }

  setPathView(view: 'cards' | 'flowchart') {
    this.pathView.set(view);
  }

  /** Scales the just-rendered SVG to exactly fill the container's width —
   * a sensible legible default the student can then zoom in/out from,
   * rather than either a forced illegible shrink or an unbounded overflow
   * with no starting point. */
  private _fitSemesterFlowchart(host: ElementRef<HTMLDivElement>) {
    const svg = host.nativeElement.querySelector('svg');
    const wrapper = host.nativeElement.parentElement;
    if (!svg || !wrapper) return;
    const natural = svg.getBoundingClientRect().width / this.semesterZoom();
    const available = wrapper.clientWidth - 16; // matches the wrapper's own padding
    const fit = natural > 0 ? Math.min(1, available / natural) : 1;
    this.semesterFitScale = fit;
    this.semesterZoom.set(fit);
  }

  zoomSemesterIn() {
    this.semesterZoom.update((z) => Math.min(2.5, +(z + 0.15).toFixed(2)));
  }

  zoomSemesterOut() {
    this.semesterZoom.update((z) => Math.max(0.2, +(z - 0.15).toFixed(2)));
  }

  resetSemesterZoom() {
    this.semesterZoom.set(this.semesterFitScale);
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
