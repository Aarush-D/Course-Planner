import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { CourseGraphEntry } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';

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
  selected = signal<CourseGraphEntry | null>(null);

  private readonly byCode = computed(() => new Map(this.courses().map((c) => [c.code, c])));

  filtered = computed(() => {
    const q = this.query().trim().toLowerCase();
    if (!q) return [];
    return this.courses()
      .filter((c) => c.code.toLowerCase().includes(q) || c.name.toLowerCase().includes(q))
      .slice(0, 20);
  });

  constructor() {
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
        const sel = this.selected();
        if (sel && !list.some((c) => c.code === sel.code)) this.selected.set(null);
      });
    });
  }

  onFocus() {
    this.open.set(true);
  }

  onBlur() {
    setTimeout(() => this.open.set(false), 150);
  }

  select(course: CourseGraphEntry) {
    this.selected.set(course);
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
    this.selected.set(null);
  }

  /** A course named in a prereq/unlock list that isn't itself in this
   * major's catalog (e.g. a Gen Ed or another department's course) still
   * needs a readable label -- falls back to the bare code. */
  courseName(code: string): string {
    return this.byCode().get(code)?.name ?? code;
  }
}
