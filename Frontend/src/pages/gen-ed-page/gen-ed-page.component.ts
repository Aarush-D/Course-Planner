import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import {
  GenEdSearchableCourse,
  GenEdSlotSearchComponent,
} from '../../components/gen-ed-slot-search/gen-ed-slot-search.component';
import { AmbiguousGenEdCourse, GenEdSlot } from '../../models/course-plan.model';
import { BackendService, GenEdAutofillContext, GenEdDomainInfo } from '../../services/backend.service';
import { PlannerStateService } from '../../services/planner-state.service';
import { ToastService } from '../../services/toast.service';

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
  imports: [GenEdSlotSearchComponent],
})
export class GenEdPageComponent {
  readonly planner = inject(PlannerStateService);
  private readonly backend = inject(BackendService);
  private readonly toast = inject(ToastService);

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

  /** Union of every domain a slot accepts' approved-course lists, deduped
   * by code -- what a choice slot's search should offer (any one of its
   * domains satisfies it), and just that one domain's list for a plain
   * single-domain slot. Reads genEdCourseMap() the same way domainInfo()
   * does; a domain the static fetch hasn't resolved yet (or failed)
   * contributes nothing rather than blocking the others. */
  slotCourses(slot: GenEdSlot): GenEdSearchableCourse[] {
    const seen = new Set<string>();
    const out: GenEdSearchableCourse[] = [];
    for (const domain of slot.domains) {
      for (const c of this.domainInfo(domain)?.courses ?? []) {
        if (seen.has(c.code)) continue;
        seen.add(c.code);
        out.push(c);
      }
    }
    return out;
  }

  /** The nice-to-have "Planned: CODE" badge on a not-done slot -- true when
   * one of the student's current wantedCourses is approved for one of this
   * slot's domains (cross-referenced client-side against the same static
   * course lists slotCourses() above reads). Returns the first match;
   * there's normally at most one wanted course per open slot, and this is
   * meant as a simple clarity hint, not an exhaustive list. */
  plannedCourseForSlot(slot: GenEdSlot): string | null {
    const wanted = this.planner.state().wantedCourses;
    if (!wanted.length) return null;
    for (const code of wanted) {
      if (slot.domains.some((d) => this.domainInfo(d)?.courses?.some((c) => c.code === code))) {
        return code;
      }
    }
    return null;
  }

  /** A slot search result's "Add to plan" button -- direct mutator, no
   * chat/LLM involved. The toast is this page's own confirmation for an
   * action outside the chat transcript, same as e.g. the Flowchart page's
   * onRemoveCompleted / toggleScheduled already do for their own direct
   * actions. */
  async onAddWanted(code: string) {
    await this.planner.addWantedCourse(code);
    this.toast.show(`${code} added to your plan`);
  }

  // Which not-done slot (by plan-item id) currently has an Auto-fill
  // request in flight -- null when none does. Guards the button against a
  // double-click re-triggering a second lookup for the same slot.
  autofillingSlotId = signal<number | null>(null);

  /** Auto-fill button for a not-done slot -- asks the backend to pick a
   * real, currently-eligible course for this requirement instead of the
   * student searching for one themselves. For a choice slot, tries each of
   * its domains in turn until one comes back non-null, mirroring the
   * backend's own multi-domain fallback (the first domain with a real
   * eligible course wins). A found course is added exactly like a manual
   * search pick; no eligible course anywhere is a legitimate outcome, not
   * an error, so it gets its own clear message rather than failing silently. */
  async onAutofill(slot: GenEdSlot) {
    if (this.autofillingSlotId() !== null) return;
    this.autofillingSlotId.set(slot.id);
    try {
      const context = this._autofillContext();
      let found: { code: string; name: string; credits: number } | null = null;
      for (const domain of slot.domains) {
        found = await this.backend.genEdAutofill(domain, context);
        if (found) break;
      }
      if (found) {
        await this.planner.addWantedCourse(found.code);
        this.toast.show(`Added ${found.code} — ${found.name}`);
      } else {
        this.toast.show(`No eligible course found for ${slot.label}.`, 'error');
      }
    } finally {
      this.autofillingSlotId.set(null);
    }
  }

  /** The same plan-context fields toPlannerRequest() sends on every normal
   * /api/plan call, trimmed to just what /api/gen-ed-autofill's contract
   * needs -- mirrors that mapping (second_major = first additional major,
   * additional_majors = the rest) so this stays byte-identical to how the
   * live plan itself was built, not a subtly different reconstruction. */
  private _autofillContext(): GenEdAutofillContext {
    const st = this.planner.state();
    return {
      major: st.major,
      catalog_year: st.catalogYear,
      start_year: st.startYear,
      second_major: st.additionalMajors[0],
      additional_majors: st.additionalMajors.slice(1),
      minors: st.minors,
      completed: st.completed,
      excluded_courses: st.excludedCourses,
      wanted_courses: st.wantedCourses,
    };
  }

  /** The discoverability link under the progress bar -- same mechanism
   * HomePageComponent.openChatForTranscript already uses (just opens the
   * persistent chat panel; its grey + button is the actual transcript
   * upload control). Reused verbatim rather than inventing a second
   * transcript-upload trigger. */
  openChatForTranscript() {
    this.planner.chatOpen.set(true);
  }
}
