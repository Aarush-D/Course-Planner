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
import { CourseEnrollmentService } from '../../services/course-enrollment.service';
import { CourseRatingService } from '../../services/course-rating.service';
import { CourseRatingSummaryRow, SupabaseService } from '../../services/supabase.service';
import { ThemeService } from '../../services/theme.service';
import { ToastService } from '../../services/toast.service';
import { normalizeCourseCode } from '../../utils/course-code.util';

/** Per-card state for the "Recommended Next Semester" enroll affordance
 * (see SHARED_CONTEXT's standard enrollment interaction pattern) -- keyed
 * by course id in FlowchartComponent.enrollState below, one entry only
 * while that card's decision flow is in flight or awaiting a choice; an
 * idle card (nothing enrolling) simply has no entry in the map. */
interface EnrollCardState {
  phase: 'checking' | 'applying' | 'finding' | 'decision' | 'alt-found' | 'alt-none';
  /** Set on 'decision' -- estimated 1-based waitlist rank if the student
   * joins the waitlist for the original course. */
  waitlistPosition?: number;
  /** Set on 'alt-found' -- the sibling course found to have an open seat. */
  altCode?: string;
  altName?: string;
}

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
  private readonly supabase = inject(SupabaseService);
  private readonly enrollment = inject(CourseEnrollmentService);
  private readonly toast = inject(ToastService);

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

  // ── Recommended card: full-description expand ────────────────────────
  // A course's description is line-clamped by default on the card (see
  // template); this lets a student expand any one card in place to read
  // it in full without leaving the grid or opening a modal.
  private expandedCourseIds = signal<Set<string>>(new Set());

  isExpanded(id: string): boolean {
    return this.expandedCourseIds().has(id);
  }

  /** Only worth offering the toggle when the description is actually long
   * enough that a 2-line clamp would cut it off -- a short one-liner
   * would show an identical "Show more" that reveals nothing new. */
  needsExpandToggle(description: string): boolean {
    return description.length > 110;
  }

  toggleExpanded(id: string) {
    this.expandedCourseIds.update((set) => {
      const next = new Set(set);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  // ── Recommended card: click-to-enroll ─────────────────────────────────
  // Standard enrollment decision pattern (see SHARED_CONTEXT) -- only
  // offered to a signed-in student; there's no persistent, contended seat
  // claim for an anonymous visitor (same gate CourseEnrollmentService and
  // the Weekly Schedule already use).
  readonly isSignedIn = computed(() => !!this.supabase.session());

  private enrollState = signal<Map<string, EnrollCardState>>(new Map());

  enrollStateFor(id: string): EnrollCardState | undefined {
    return this.enrollState().get(id);
  }

  private _setEnrollState(id: string, state: EnrollCardState | null) {
    this.enrollState.update((map) => {
      const next = new Map(map);
      if (state) next.set(id, state);
      else next.delete(id);
      return next;
    });
  }

  /** Entry point for the "Enroll" button. Checks real availability first --
   * an open seat enrolls immediately with no extra prompt; a full course
   * instead surfaces the waitlist/find-a-replacement decision below rather
   * than silently waitlisting the student. */
  async onEnrollClick(course: Course) {
    const id = course.id;
    if (!id) return;
    this._setEnrollState(id, { phase: 'checking' });
    try {
      const { seatAvailable, estimatedWaitlistPosition } = await this.enrollment.checkAvailability(id);
      if (seatAvailable) {
        await this._applyAndConfirm(id);
      } else {
        this._setEnrollState(id, { phase: 'decision', waitlistPosition: estimatedWaitlistPosition });
      }
    } catch (e) {
      this._setEnrollState(id, null);
      this.toast.show(e instanceof Error ? e.message : 'Could not check availability right now.', 'error');
    }
  }

  async onJoinWaitlist(course: Course) {
    const id = course.id;
    if (!id) return;
    this._setEnrollState(id, { phase: 'applying' });
    await this._applyAndConfirm(id);
  }

  /** "Find a replacement" -- tries this course's sibling requirement
   * options (Course.options) in the engine's own ranked order and offers
   * the first one with an open seat, or reports that every option is
   * also full so the student can fall back to the waitlist. */
  async onFindReplacement(course: Course) {
    const id = course.id;
    if (!id) return;
    this._setEnrollState(id, { phase: 'finding' });
    try {
      const altCode = await this.enrollment.findOpenAlternative(course.options ?? []);
      if (altCode) {
        this._setEnrollState(id, { phase: 'alt-found', altCode, altName: this._nameFor(altCode) });
      } else {
        this._setEnrollState(id, { phase: 'alt-none' });
      }
    } catch (e) {
      this._setEnrollState(id, null);
      this.toast.show(e instanceof Error ? e.message : 'Could not look for an alternative right now.', 'error');
    }
  }

  async onConfirmAlternative(course: Course, altCode: string) {
    const id = course.id;
    if (!id) return;
    this._setEnrollState(id, { phase: 'applying' });
    await this._applyAndConfirm(altCode, id);
  }

  onCancelDecision(courseId: string) {
    this._setEnrollState(courseId, null);
  }

  /** Shared tail for every path that ends in an actual claim_course_seat
   * call (direct enroll, join-waitlist, and confirming a replacement) --
   * always clears the card's decision state and always gives the student
   * feedback, success or failure, via the same toast every other surface
   * uses. `clearId` lets "enroll in the alternative instead" clear the
   * ORIGINAL course's card (where the decision UI lives) even though the
   * RPC call itself is for the alternative's code. */
  private async _applyAndConfirm(codeToApply: string, clearId?: string) {
    const id = clearId ?? codeToApply;
    try {
      const result = await this.enrollment.apply(codeToApply);
      this._setEnrollState(id, null);
      this.toast.show(
        result.status === 'enrolled'
          ? "You’re in — a seat is held for you."
          : `Full — you’re #${result.position} on the waitlist.`,
        'success',
      );
    } catch (e) {
      this._setEnrollState(id, null);
      this.toast.show(e instanceof Error ? e.message : 'Could not enroll right now.', 'error');
    }
  }

  private _nameFor(code: string): string | undefined {
    return (this.courses() ?? []).find(
      (c) => c.id?.trim().toUpperCase() === code.trim().toUpperCase()
    )?.name;
  }

  // ── Course search on the Unlock Map (GitHub issue #2) ────────────────────
  // Built from the rendered SVG's own node text, not a separate data source
  // -- guarantees the search list can never drift from what's actually
  // drawn, regardless of exactly how the backend formats a node's label.
  private unlockMapNodes = signal<{ text: string; el: SVGGraphicsElement }[]>([]);

  /** The unlock map is raw Mermaid SVG dropped in via innerHTML (see
   * _renderInto below) -- no text alternative existed for it at all. This
   * reuses the same node list the search box already builds (course name
   * -> its SVG element) to give a screen-reader user the actual course
   * names on the diagram, via role="img" + this summary, plus the sr-only
   * list rendered right after the diagram host in the template. */
  unlockMapAltText = computed(() => {
    const nodes = this.unlockMapNodes();
    if (!nodes.length) return 'Course unlock map';
    return `Course unlock map diagram showing ${nodes.length} courses and their prerequisite relationships.`;
  });

  /** Plain course-name text list, for the sr-only fallback rendered right
   * after the diagram host -- the aria-label above gives a count/summary,
   * this gives the actual course names since the diagram itself has none
   * in the accessibility tree (it's opaque SVG via innerHTML). */
  unlockMapNodeTexts = computed(() => this.unlockMapNodes().map((n) => n.text));
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

  /** Bound to (focusout) on the search field's wrapping container (not
   * (blur) on the input itself) -- a keyboard user Tabbing FROM the input
   * INTO its own results list still fires this, and closing unconditionally
   * 150ms later would unmount the very result they just tabbed onto,
   * dropping focus to <body>. relatedTarget is where focus is actually
   * going; skip the close if that's still inside the container (the mouse
   * path is separately protected by (mousedown) preventDefault on each
   * result button). Same check as planner-setup.component.ts's
   * _focusStayedWithin. */
  onUnlockSearchFocusOut(event: FocusEvent) {
    const related = event.relatedTarget as Node | null;
    const container = event.currentTarget as HTMLElement;
    if (related && container.contains(related)) return;
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
