import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { AmbiguousGenEdCourse, GenEdSlot } from '../../models/course-plan.model';
import { BackendService, GenEdDomainInfo } from '../../services/backend.service';
import { PlannerStateService } from '../../services/planner-state.service';

/**
 * General education requirements, browsable by domain, for the CURRENT
 * plan. Replaces the old "coming soon" placeholder. Three pieces of data
 * drive this page:
 *  - planner.coursePlan()?.progress?.byCategory?.['gen_ed'] -- the same
 *    flat done/total bucket the Progress page's own bars already use, for
 *    the overall bar at the top.
 *  - planner.coursePlan()?.genEdDetail -- the per-slot breakdown (single
 *    vs. choice domains, done status, which real course satisfied each
 *    one) and any completed courses ambiguous between this plan's own
 *    open slots. Backend-computed; this page never re-derives it.
 *  - backend.genEdCourses() -- PSU's static approved-course list per
 *    domain (data/gen_ed_courses.json), fetched once and cached, for the
 *    "browse this domain's courses" disclosures.
 */
@Component({
  selector: 'app-gen-ed-page',
  standalone: true,
  templateUrl: './gen-ed-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GenEdPageComponent {
  readonly planner = inject(PlannerStateService);
  private readonly backend = inject(BackendService);

  // Static per-domain course lists -- fetched once (BackendService caches
  // the promise itself, so this constructor firing again on route
  // re-entry costs nothing beyond a resolved-promise read).
  genEdCourseMap = signal<Record<string, GenEdDomainInfo>>({});

  constructor() {
    this.backend
      .genEdCourses()
      .then((map) => this.genEdCourseMap.set(map))
      .catch(() => {
        // A failed fetch just leaves domain-name/course-list lookups
        // falling back to the bare domain code (see domainInfo/domainLabel
        // below) -- the slot list and progress bar above it don't depend
        // on this at all, so the page stays useful either way.
      });
  }

  genEd = computed(() => this.planner.coursePlan()?.progress?.byCategory?.['gen_ed']);

  slots = computed<GenEdSlot[]>(() => this.planner.coursePlan()?.genEdDetail?.slots ?? []);

  ambiguousCourses = computed<AmbiguousGenEdCourse[]>(
    () => this.planner.coursePlan()?.genEdDetail?.ambiguousCourses ?? [],
  );

  /** Every domain code any slot references, in first-seen order -- drives
   * the "browse this domain's courses" section below the slot list.
   * Deliberately not "every domain in gen_ed_courses.json": a domain this
   * plan's own requirements never touch (e.g. IL for a major with no
   * Integrative-Learning requirement) has nothing relevant to browse here. */
  referencedDomains = computed<string[]>(() => {
    const seen = new Set<string>();
    const domains: string[] = [];
    for (const slot of this.slots()) {
      for (const d of slot.domains) {
        if (!seen.has(d)) {
          seen.add(d);
          domains.push(d);
        }
      }
    }
    return domains;
  });

  domainInfo(domain: string): GenEdDomainInfo | undefined {
    return this.genEdCourseMap()[domain];
  }

  /** "Arts (GA)" when the static course list has loaded and knows this
   * domain's real name; falls back to the bare code otherwise (still
   * correct, just less friendly) so the page never blocks on that fetch. */
  domainLabel(domain: string): string {
    const name = this.domainInfo(domain)?.name;
    return name ? `${name} (${domain})` : domain;
  }

  /** The slot's own requirement sentence -- get the logical relationship
   * right: a single-domain slot is one required domain; a choice slot
   * (isChoice, domains.length > 1) is an OR of its domains, joined with
   * "or" and NEVER "and" -- that's real degree-requirement semantics, not
   * a cosmetic word choice. */
  slotRequirementText(slot: GenEdSlot): string {
    const names = slot.domains.map((d) => this.domainLabel(d));
    if (slot.isChoice) return names.join(' or ');
    return `${names[0] ?? ''} required`;
  }

  async onOverrideChanged(courseCode: string, domain: string) {
    await this.planner.setGenEdOverride(courseCode, domain);
  }
}
