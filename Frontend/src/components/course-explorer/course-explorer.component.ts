import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { CourseGraphEntry } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import { linkQueryParam } from '../../utils/url-state';

/** Course prerequisite/unlock explorer — search any course within the
 * student's own major and see what it requires and what it leads to next,
 * independent of the student's own completed courses (unlike the Course
 * Unlock Map above it, which is a live snapshot relative to what's already
 * done). Fetches the whole major's course graph once per major and does
 * every search/lookup client-side from there — no round-trip per keystroke. */
@Component({
  selector: 'app-course-explorer',
  standalone: true,
  templateUrl: './course-explorer.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CourseExplorerComponent {
  private readonly backend = inject(BackendService);

  major = input.required<string>();
  catalogYear = input<number | undefined>();

  private readonly courses = signal<CourseGraphEntry[]>([]);
  loading = signal(false);

  query = signal('');
  open = signal(false);

  /** The URL, not a component signal, is the source of truth for which
   * course is open -- that's what makes "here's what CMPSC 465 unlocks"
   * a link a student can actually send someone. Held as a bare code
   * rather than the resolved entry because the code is what survives a
   * reload: the catalog it resolves against is fetched async, so at the
   * moment a pasted URL is read there is nothing yet to resolve against. */
  readonly selectedCode = signal<string | null>(null);
  readonly selected = computed<CourseGraphEntry | null>(() => {
    const code = this.selectedCode();
    return code ? (this.byCode().get(code) ?? null) : null;
  });

  private readonly byCode = computed(() => new Map(this.courses().map((c) => [c.code, c])));

  filtered = computed(() => {
    const q = this.query().trim().toLowerCase();
    if (!q) return [];
    return this.courses()
      .filter((c) => c.code.toLowerCase().includes(q) || c.name.toLowerCase().includes(q))
      .slice(0, 20);
  });

  constructor() {
    // 'replace', not 'push': looking up a second course is refining one
    // view, not navigating somewhere new, so Back should leave the
    // Flowchart page rather than walk back through every code the student
    // happened to check along the way.
    linkQueryParam({
      key: 'course',
      signal: this.selectedCode,
      toParam: (code) => code,
      fromParam: (param) => param,
      history: 'replace',
    });

    effect(() => {
      const major = this.major();
      const year = this.catalogYear();
      if (!major) {
        this.courses.set([]);
        return;
      }
      this.loading.set(true);
      this.backend.courseGraph(major, year).then((list) => {
        this.courses.set(list);
        this.loading.set(false);
        // A major switch mid-session shouldn't leave a now-foreign course
        // pinned as "selected" — clear it if it's not in the new catalog.
        // Clears the code (not the derived `selected`), so the stale
        // ?course= comes out of the URL with it.
        const code = this.selectedCode();
        if (code && !list.some((c) => c.code === code)) this.selectedCode.set(null);
      });
    });
  }

  onFocus() {
    this.open.set(true);
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
  onFocusOut(event: FocusEvent) {
    const related = event.relatedTarget as Node | null;
    const container = event.currentTarget as HTMLElement;
    if (related && container.contains(related)) return;
    setTimeout(() => this.open.set(false), 150);
  }

  select(course: CourseGraphEntry) {
    this.selectedCode.set(course.code);
    this.query.set('');
    this.open.set(false);
  }

  /** Clicking an "unlocks" chip jumps to that course. Every unlock is
   * guaranteed to be a real entry in this same major's catalog (the
   * backend only ever records the reverse edge between two courses that
   * are both already in it), so this always finds a real one, not a stub. */
  selectByCode(code: string) {
    const course = this.byCode().get(code);
    if (course) this.select(course);
  }

  clearSelection() {
    this.selectedCode.set(null);
  }

  /** A course named in a prereq/unlock list that isn't itself in this
   * major's catalog (e.g. a Gen Ed or another department's course) still
   * needs a readable label -- falls back to the bare code. */
  courseName(code: string): string {
    return this.byCode().get(code)?.name ?? code;
  }
}
