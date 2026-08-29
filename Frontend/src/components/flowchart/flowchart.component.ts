import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import mermaid from 'mermaid';
import { Course, FullPlan, LlmFlowchart, Progress } from '../../models/course-plan.model';
import { ThemeService } from '../../services/theme.service';

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
  // Shared-plan viewers see the same flowchart but can't edit it -- hides
  // just the per-course remove (x) buttons, nothing else.
  readOnly       = input(false);

  removeCompleted = output<string>();

  private readonly theme = inject(ThemeService);

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
    afterNextRender(() => this._initMermaid());

    effect(() => {
      const host = this.mermaidHost(); // tracked: effect re-runs once the div exists
      const isLoading = this.isLoading();
      const llm = this.llm();
      this.theme.dark(); // re-run (and redraw with matching colors) when the theme toggles
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
      this.theme.dark();
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
      this.theme.dark();
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

  /** Mermaid bakes colors into the SVG at render time (it isn't CSS-restylable
   * after the fact), so switching themes means re-initializing with the
   * matching palette before every render, not just once at startup. */
  private _initMermaid() {
    mermaid.initialize({
      startOnLoad: false,
      theme: this.theme.dark() ? 'dark' : 'default',
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

  // build_unlock_map/build_semester_flowchart (Backend/planner_engine.py) bake
  // literal light-mode hex colors into `classDef`/`linkStyle` lines -- Mermaid
  // renders those as-is regardless of the `theme` option, so dark mode has to
  // patch this fixed, known set of hex triples before every render instead.
  private static readonly DARK_MERMAID_COLORS: ReadonlyArray<[string, string]> = [
    ['fill:#dcfce7,stroke:#16a34a,color:#166534', 'fill:#052e16,stroke:#22c55e,color:#86efac'],
    ['fill:#dbeafe,stroke:#2563eb,color:#1e40af', 'fill:#172554,stroke:#3b82f6,color:#93c5fd'],
    ['fill:#f1f5f9,stroke:#94a3b8,color:#475569', 'fill:#1e293b,stroke:#64748b,color:#cbd5e1'],
    ['fill:#fee2e2,stroke:#dc2626,color:#991b1b', 'fill:#450a0a,stroke:#ef4444,color:#fca5a5'],
    ['stroke:#16a34a', 'stroke:#22c55e'],
    ['stroke:#dc2626', 'stroke:#ef4444'],
    ['stroke:#94a3b8', 'stroke:#64748b'],
  ];

  private _forTheme(code: string): string {
    if (!this.theme.dark()) return code;
    let out = code;
    for (const [from, to] of FlowchartComponent.DARK_MERMAID_COLORS) {
      out = out.split(from).join(to);
    }
    return out;
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
    this._initMermaid();
    const normalized = code.match(/^\s*flowchart\s+/i)
      ? code
      : `flowchart TD\n${code}`;
    try {
      const { svg } = await mermaid.render(
        `${idPrefix}-${Date.now()}`,
        this._forTheme(normalized),
      );
      host.nativeElement.innerHTML = svg;
    } catch (e: any) {
      this._clearHost(host, error);
      error.set(e?.message ?? 'Failed to render Mermaid diagram');
    }
  }
}
