import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

/** The bare shape a search result needs -- deliberately just the fields
 * BackendService.GenEdDomainInfo.courses already carries (code/title/
 * credits), so the Gen Ed page can pass a plain union of one or more
 * domains' course lists straight through without any extra mapping. */
export interface GenEdSearchableCourse {
  code: string;
  title: string;
  credits: string;
}

/**
 * Compact, collapsed-by-default "add a specific course" search for one
 * still-open Gen Ed slot. Same search-typeahead structure as
 * CourseExplorerComponent (query/open signals, case-insensitive substring
 * filter capped at 20 results, mousedown+preventDefault on each result so
 * the input's blur doesn't close the dropdown before the click registers,
 * a (focusout) handler on the wrapping div that checks relatedTarget so a
 * keyboard Tab into the results list doesn't prematurely close it) --
 * scoped to one slot's own approved-course list instead of a whole major's
 * catalog, and with no "selected course" detail view since there's nothing
 * to show beyond picking one.
 *
 * Starts collapsed behind a small toggle: most students will use the
 * slot's Auto-fill button instead of ever opening this, so it shouldn't
 * visually compete with the requirement list above it.
 */
@Component({
  selector: 'app-gen-ed-slot-search',
  standalone: true,
  templateUrl: './gen-ed-slot-search.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GenEdSlotSearchComponent {
  /** The union of every domain this slot accepts -- for a multi-domain
   * choice slot, the caller merges those domains' course lists (deduped)
   * before passing them in here; this component just searches whatever
   * list it's given. */
  courses = input.required<GenEdSearchableCourse[]>();

  /** Emits the chosen course's code -- the parent (GenEdPageComponent)
   * owns actually calling PlannerStateService.addWantedCourse with it, so
   * this component stays a plain search control with no planner
   * dependency of its own. */
  courseSelected = output<string>();

  expanded = signal(false);
  query = signal('');
  open = signal(false);

  filtered = computed(() => {
    const q = this.query().trim().toLowerCase();
    if (!q) return [];
    return this.courses()
      .filter((c) => c.code.toLowerCase().includes(q) || c.title.toLowerCase().includes(q))
      .slice(0, 20);
  });

  toggle() {
    const next = !this.expanded();
    this.expanded.set(next);
    if (!next) {
      // Collapsing should feel like a fresh open next time, not resume
      // mid-search with a stale dropdown.
      this.query.set('');
      this.open.set(false);
    }
  }

  onFocus() {
    this.open.set(true);
  }

  /** Same relatedTarget-aware close as CourseExplorerComponent.onFocusOut --
   * see that component's doc comment for why (blank) on the input alone
   * isn't used here. */
  onFocusOut(event: FocusEvent) {
    const related = event.relatedTarget as Node | null;
    const container = event.currentTarget as HTMLElement;
    if (related && container.contains(related)) return;
    setTimeout(() => this.open.set(false), 150);
  }

  select(course: GenEdSearchableCourse) {
    this.courseSelected.emit(course.code);
    this.query.set('');
    this.open.set(false);
  }
}
