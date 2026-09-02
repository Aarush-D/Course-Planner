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
import { CourseExplorerComponent } from '../course-explorer/course-explorer.component';
import { CourseReviewsModalComponent } from '../course-reviews-modal/course-reviews-modal.component';
import { RateCourseModalComponent } from '../rate-course-modal/rate-course-modal.component';
import { StarRatingComponent } from '../ui/star-rating/star-rating.component';
import { Course, FullPlan, LlmFlowchart, Progress } from '../../models/course-plan.model';
import { CourseRatingService } from '../../services/course-rating.service';
import { CourseRatingSummaryRow } from '../../services/supabase.service';
import { ThemeService } from '../../services/theme.service';
import { normalizeCourseCode } from '../../utils/course-code.util';

@Component({
  selector: 'app-flowchart',
  standalone: true,
  templateUrl: './flowchart.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StarRatingComponent, RateCourseModalComponent, CourseReviewsModalComponent, CourseExplorerComponent],
})
export class FlowchartComponent {
  isLoading      = input.required<boolean>();
  courses        = input<Course[] | null>();      // completed + recommended cards from backend
  completedCodes = input<string[]>([]);           // raw completed codes from app state
  fullPlan       = input<FullPlan | null>();
  progress       = input<Progress | null>();
  unlockMap      = input<LlmFlowchart | null>();  // completed -> next -> future map -- the one diagram (see GitHub issue #2)
  major          = input<string | null>();        // for the Course Explorer search panel -- null hides it (e.g. undecided)
  catalogYear    = input<number | undefined>();
  scheduledCourseIds = input<string[]>([]);       // for the Weekly Schedule preview's "added" state
  // Shared-plan viewers see the same flowchart but can't edit it -- hides
  // just the per-course remove (x) buttons, nothing else.
  readOnly       = input(false);

  removeCompleted = output<string>();
  toggleScheduled = output<string>();

  private readonly theme = inject(ThemeService);
  private readonly ratings = inject(CourseRatingService);

  // Optional (not required): the host div lives inside an @if branch, so it
  // can be absent while loading — reading a required query then throws NG0951.
  private readonly unlockHost =
    viewChild<ElementRef<HTMLDivElement>>('unlockHost');

  unlockError = signal<string | null>(null);

  // Only used to give the graduation-goal banner a "today" anchor when the
  // student's start year is in the past — see the goal banner in the
  // template. A plain field, not a signal: "today" doesn't need to be
  // reactive within a single page view.
  readonly currentYear = new Date().getFullYear();

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

  // Courses marked "in progress" via the Weekly Schedule's "Add to
  // schedule" toggle (see scheduledCourseIds/toggleScheduled above) --
  // shown here in place of Completed Courses, since a student's current
  // in-flight semester is more useful to see at a glance than a static
  // list of what's already done (that's still tracked and shown on the
  // Progress page's checklist).
  inProgressCourses = computed<Course[]>(() => {
    const codes = new Set(
      (this.scheduledCourseIds() ?? []).map((c) => c.trim().toUpperCase())
    );
    return (this.courses() ?? []).filter((c) =>
      codes.has(c.id.trim().toUpperCase())
    );
  });

  // ── Anonymous course ratings ──────────────────────────────────────────
  // Submitting attaches to Completed Courses (a course you've actually
  // taken); the read-only average shown on Recommended cards is
  // informational, for a student deciding what to take next.
  ratingModalFor = signal<Course | null>(null);
  reviewsModalFor = signal<Course | null>(null);
  private ratingSummaries = signal<Map<string, CourseRatingSummaryRow>>(new Map());

  openRatingModal(course: Course) {
    this.ratingModalFor.set(course);
  }

  closeRatingModal() {
    this.ratingModalFor.set(null);
  }

  openReviewsModal(course: Course) {
    this.reviewsModalFor.set(course);
  }

  closeReviewsModal() {
    this.reviewsModalFor.set(null);
  }

  ratingSummaryFor(code: string): CourseRatingSummaryRow | undefined {
    return this.ratingSummaries().get(normalizeCourseCode(code));
  }

  // ── Course search on the Unlock Map (GitHub issue #2) ────────────────────
  // Built from the rendered SVG's own node text, not a separate data source
  // -- guarantees the search list can never drift from what's actually
  // drawn, regardless of exactly how the backend formats a node's label.
  private unlockMapNodes = signal<{ text: string; el: SVGGraphicsElement }[]>([]);
  unlockSearchQuery = signal('');
  unlockSearchOpen = signal(false);

  filteredUnlockMapNodes = computed(() => {
    const query = this.unlockSearchQuery().trim().toLowerCase();
    const nodes = this.unlockMapNodes();
    const matches = query ? nodes.filter((n) => n.text.toLowerCase().includes(query)) : nodes;
    return matches.slice(0, 20); // a 40-60 node diagram shouldn't dump an equally long list
  });

  onUnlockSearchFocus() {
    this.unlockSearchOpen.set(true);
  }

  onUnlockSearchBlur() {
    setTimeout(() => this.unlockSearchOpen.set(false), 150);
  }

  /** Scrolls the diagram's own scroll container to the matching node and
   * briefly outlines it -- a direct attribute mutation (not a CSS class)
   * since Mermaid bakes fill/stroke in as presentation attributes that
   * would otherwise need fighting for specificity. */
  selectUnlockMapNode(node: { text: string; el: SVGGraphicsElement }) {
    this.unlockSearchQuery.set('');
    this.unlockSearchOpen.set(false);
    node.el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
    const shape = node.el.querySelector('rect, polygon, circle, ellipse');
    if (!shape) return;
    const prevStroke = shape.getAttribute('stroke');
    const prevWidth = shape.getAttribute('stroke-width');
    shape.setAttribute('stroke', '#4f46e5');
    shape.setAttribute('stroke-width', '4');
    setTimeout(() => {
      prevStroke === null ? shape.removeAttribute('stroke') : shape.setAttribute('stroke', prevStroke);
      prevWidth === null ? shape.removeAttribute('stroke-width') : shape.setAttribute('stroke-width', prevWidth);
    }, 2000);
  }

  constructor() {
    afterNextRender(() => this._initMermaid());

    effect(() => {
      const host = this.unlockHost();
      const isLoading = this.isLoading();
      const map = this.unlockMap();
      this.theme.dark(); // re-run (and redraw with matching colors) when the theme toggles
      if (isLoading || !host) return;

      const code = map?.mermaid?.trim();
      if (!code) {
        this._clearHost(host, this.unlockError);
        this.unlockMapNodes.set([]);
        return;
      }
      this._renderInto(host, code, this.unlockError, 'unlock').then(() => this._scanUnlockMapNodes(host));
    });

    effect(() => {
      const codes = this.recommendedCourses().map((c) => c.id).filter(Boolean);
      if (!codes.length) return;
      // Ratings are a nice-to-have enhancement, not core functionality --
      // a failed fetch (network hiccup, or the migration simply not run
      // yet) should just mean no rating badges show, never a console error.
      this.ratings.getSummaries(codes).then((map) => this.ratingSummaries.set(map)).catch(() => {});
    });
  }

  /** Mermaid bakes colors into the SVG at render time (it isn't CSS-restylable
   * after the fact), so switching themes means re-initializing with the
   * matching palette before every render, not just once at startup. */
  private _initMermaid() {
    mermaid.initialize({
      startOnLoad: false,
      theme: this.theme.dark() ? 'dark' : 'default',
      flowchart: { useMaxWidth: false, nodeSpacing: 35, rankSpacing: 65, padding: 12 },
      themeVariables: { fontSize: '14px' },
    });
  }

  private _scanUnlockMapNodes(host: ElementRef<HTMLDivElement>) {
    const svg = host.nativeElement.querySelector('svg');
    if (!svg) {
      this.unlockMapNodes.set([]);
      return;
    }
    const nodes = [...svg.querySelectorAll<SVGGraphicsElement>('.node')]
      .map((el) => ({ text: (el.textContent || '').trim(), el }))
      .filter((n) => n.text);
    this.unlockMapNodes.set(nodes);
  }

  onRemove(code: string) {
    this.removeCompleted.emit(code);
  }

  /** The "In Progress" section's own cross-out -- didn't actually take it
   * after all, so un-mark it the same way the Weekly Schedule's own
   * "remove" toggle already does (same underlying signal, just a second
   * place to reach it from). */
  onRemoveInProgress(code: string) {
    this.toggleScheduled.emit(code);
  }

  formatCredits(c: number | null | undefined): string {
    if (c === null || c === undefined) return '';
    return Number.isInteger(c) ? `${c} cr` : `${c} cr`;
  }

  // build_unlock_map (Backend/planner_engine.py) bakes literal light-mode hex
  // colors into `classDef`/`linkStyle` lines -- Mermaid renders those as-is
  // regardless of the `theme` option, so dark mode has to patch this fixed,
  // known set of hex triples before every render instead.
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
