import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import {
  GenEdDeptChip,
  GenEdDeptChipsComponent,
} from '../../components/gen-ed-dept-chips/gen-ed-dept-chips.component';
import {
  GenEdSearchableCourse,
  GenEdSlotSearchComponent,
} from '../../components/gen-ed-slot-search/gen-ed-slot-search.component';
import { AmbiguousGenEdCourse, GenEdSlot } from '../../models/course-plan.model';
import {
  BackendService,
  GenEdAutofillContext,
  GenEdAutofillResult,
  GenEdDomainInfo,
} from '../../services/backend.service';
import { PlannerStateService } from '../../services/planner-state.service';
import { ToastService } from '../../services/toast.service';

/** PSU's own real top-level Gen Ed groupings (confirmed live against
 * genedplan.psu.edu), keyed to which domain CODES belong under each --
 * never a source of truth for credit/slot counts, just membership. Every
 * done/doneItems figure is still derived live from this plan's own
 * genEdDetail.slots in groupedSlots() below -- only totalCredits (the
 * denominator) also considers DOMAIN_CREDIT_MINIMUM's own real PSU floor,
 * so a domain this specific plan has no open slot for (or fewer credits'
 * worth than the real minimum) still shows PSU's actual requirement
 * instead of understating it as whatever this one plan happens to declare.
 * Order here is the display order. */
interface GenEdGroupDef {
  key: string;
  label: string;
  domains: string[];
  /** Decorative accent only (this group's own progress-bar fill) -- reuses
   * the exact indigo/sky/emerald/violet/slate tone pairs
   * progress-page.component.ts's own CATEGORY_COLORS already picked and
   * verified against the WCAG 3:1 non-text contrast minimum, rather than
   * inventing new untested shades for this page. */
  color: string;
  /** The same accent as a section-card left border -- spelled out as its
   * own full literal class string (not derived from `color` at runtime via
   * string surgery) because Tailwind's JIT scanner only picks up class
   * names it can find verbatim in source text; a runtime-built
   * "border-l-" + "indigo-500" concatenation would silently never get its
   * CSS generated. */
  borderColor: string;
}

const GEN_ED_GROUPS: GenEdGroupDef[] = [
  {
    key: 'foundations',
    label: 'Foundations',
    domains: ['GWS', 'GQ'],
    color: 'bg-indigo-500 dark:bg-indigo-400',
    borderColor: 'border-l-indigo-500 dark:border-l-indigo-400',
  },
  {
    // Knowledge Domain Breadth, Integrative Studies, and Exploration are
    // one merged group at Aarush's explicit request -- PSU's bulletin
    // gives them separate credit lines (15 + 6 + 9), but the SAME pool of
    // domains satisfies all three (Exploration's own real degree-plan
    // representation is a multi-domain choice slot spanning exactly
    // {GA,GH,GN,GS,INTER-D} -- see groupKeyForSlot below), so merging them
    // here means that choice slot's domains all map to this ONE group key
    // and it renders correctly under it instead of falling to
    // OTHER_GROUP as a "spans more than one group" catch-all.
    key: 'knowledge_domains',
    label: 'Knowledge Domain & Integrative Studies',
    domains: ['INTER-D', 'GA', 'GHW', 'GH', 'GN', 'GS'],
    color: 'bg-sky-600 dark:bg-sky-400',
    borderColor: 'border-l-sky-600 dark:border-l-sky-400',
  },
  {
    key: 'cultural_diversity',
    label: 'Cultural Diversity',
    domains: ['IL', 'US'],
    color: 'bg-violet-500 dark:bg-violet-400',
    borderColor: 'border-l-violet-500 dark:border-l-violet-400',
  },
];

/** Catch-all for a slot whose domain(s) aren't in GEN_ED_GROUPS at all, OR
 * a multi-domain choice slot whose domains still span MORE THAN ONE group
 * (e.g. a slot mixing a Foundations domain with a Cultural Diversity one --
 * not seen in real data today, but the fallback stays honest rather than
 * silently misfiling it into either group). Now that Knowledge Domain
 * Breadth, Integrative Studies, and Exploration share one group above,
 * the real "GA/GH/GN/GS/INTER-D" Exploration choice slot resolves there
 * directly instead of landing here. */
const OTHER_GROUP: GenEdGroupDef = {
  key: 'other',
  label: 'Other Requirements',
  domains: [],
  color: 'bg-slate-500 dark:bg-slate-500',
  borderColor: 'border-l-slate-500 dark:border-l-slate-500',
};

const DOMAIN_TO_GROUP: Map<string, string> = new Map(
  GEN_ED_GROUPS.flatMap((g) => g.domains.map((d) => [d, g.key] as const)),
);

/** PSU's real per-domain credit MINIMUM (Aarush's own breakdown, matching
 * the bulletin's Foundations 15 = GWS 9 + GQ 6, Knowledge Domain &
 * Integrative 30 = INTER-D 6 + GA/GHW/GH/GN/GS 3 each + Exploration 9,
 * Cultural Diversity 6 = IL 3 + US 3). Used as a FLOOR, never a cap: a
 * card's real total is `Math.max(thisPlanOwnDeclaredTotal, thisFloor)`, so
 * a major whose plan genuinely needs more than the minimum in some domain
 * (e.g. an extra domain-specific course covering Exploration instead of a
 * generic multi-domain choice slot) still shows its own larger real total,
 * and a domain with NO open slot at all (already covered by a fixed
 * major-required course, so this plan's own derived total is 0) shows the
 * real PSU minimum instead of a bare zero. */
const DOMAIN_CREDIT_MINIMUM: Record<string, number> = {
  GWS: 9,
  GQ: 6,
  'INTER-D': 6,
  GA: 3,
  GHW: 3,
  GH: 3,
  GN: 3,
  GS: 3,
  IL: 3,
  US: 3,
};

/** Exploration's own real degree-plan representation is a multi-domain
 * CHOICE slot spanning exactly these five domains (see groupKeyForSlot) --
 * it has no domain code of its own, so its 9-credit minimum is keyed by
 * this exact domain-set signature rather than DOMAIN_CREDIT_MINIMUM's
 * per-domain entries (summing those five's own minimums would double-count
 * against Knowledge Domain Breadth's already-separate 15). Any OTHER
 * multi-domain choice combination (e.g. a plain {GA,GH} slot) has no
 * recognized canonical minimum of its own, so it falls back to this
 * plan's own declared total untouched -- see creditFloorFor below. */
const EXPLORATION_DOMAIN_SET = ['GA', 'GH', 'GN', 'GS', 'INTER-D'].sort().join('|');
const EXPLORATION_CREDIT_MINIMUM = 9;

function creditFloorFor(domains: string[]): number {
  if (domains.length === 1) return DOMAIN_CREDIT_MINIMUM[domains[0]] ?? 0;
  return [...domains].sort().join('|') === EXPLORATION_DOMAIN_SET ? EXPLORATION_CREDIT_MINIMUM : 0;
}

/** One domain (or, for a multi-domain choice slot that stays within a
 * single group, one domain-SET) actually present in this plan, inside one
 * top-level group -- what GenEdPageComponent renders as a sub-card. Two
 * slots that both require exactly domain-set {GHW} (e.g. two separate
 * "GEN ED (GHW)" plan items) merge into ONE card here, each still listed
 * as its own row underneath -- see groupedSlots()'s doc comment for why
 * this merges by exact domain-set rather than forcing every domain onto
 * its own card regardless of OR semantics. */
interface GenEdDomainCard {
  /** domains, sorted and joined -- stable identity for this card, reused
   * as the activeDeptFilter lookup key. */
  key: string;
  /** domains in the slot's own declared order (not sorted) -- what title
   * building reads, so "GA/GH" reads in the plan's own order. */
  domains: string[];
  title: string;
  /** Only set for a multi-domain (choice) card -- names the real options,
   * since the title alone ("GA/GH") doesn't spell those out. */
  subtitle: string | null;
  slots: GenEdSlot[];
  doneItems: number;
  totalItems: number;
  creditsDone: number;
  totalCredits: number;
  percent: number;
  /** Union of every domain in this card's own approved-course lists,
   * deduped by code -- the full (unfiltered) pool this card's chips and
   * course search/browse both ultimately narrow. */
  courses: GenEdSearchableCourse[];
  /** Department-prefix filter chips for `courses` above, precomputed once
   * per groupedSlots() recompute rather than re-derived on every hover. */
  deptChips: GenEdDeptChip[];
}

interface GenEdGroupView {
  key: string;
  label: string;
  color: string;
  borderColor: string;
  cards: GenEdDomainCard[];
  doneItems: number;
  totalItems: number;
  creditsDone: number;
  totalCredits: number;
  percent: number;
}

/** A course code is always "<DEPT PREFIX> <NUMBER><optional letter>" --
 * e.g. "AA 130N" -> "AA", "ENGL 15" -> "ENGL", and the one real hyphenated
 * exception in data/gen_ed_courses.json, "A-I 100" -> "A-I". Parsed from
 * each course's own code via regex rather than any fixed department list,
 * since PSU's real prefix set spans the whole university and isn't
 * something this codebase should hardcode. */
const DEPT_PREFIX_RE = /^([A-Za-z][A-Za-z-]*)(?=\s)/;

function departmentPrefix(code: string): string {
  const match = code.trim().match(DEPT_PREFIX_RE);
  return (match ? match[1] : code.trim()).toUpperCase();
}

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
  imports: [GenEdSlotSearchComponent, GenEdDeptChipsComponent],
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

  /** Which top-level GEN_ED_GROUPS entry (or 'other') a slot belongs under
   * -- a single-domain slot follows its one domain; a multi-domain choice
   * slot follows its domains' shared group ONLY if every one of them maps
   * to the SAME group (e.g. IL/US both Cultural Diversity); any domain not
   * in the map at all, or domains spanning more than one group, falls to
   * 'other' rather than being forced into a group that would misstate
   * what the requirement actually is. */
  private groupKeyForSlot(slot: GenEdSlot): string {
    const keys = new Set(slot.domains.map((d) => DOMAIN_TO_GROUP.get(d) ?? null));
    if (keys.size === 1) {
      const only = [...keys][0];
      if (only !== null) return only;
    }
    return 'other';
  }

  private cardTitle(domains: string[]): string {
    if (domains.length === 1) return this.domainLabel(domains[0]);
    return domains.join(' / ');
  }

  private cardSubtitle(domains: string[]): string {
    const names = domains.map((d) => this.domainInfo(d)?.name ?? d);
    return `Choose one: ${names.join(', ')}`;
  }

  private buildDeptChips(courses: GenEdSearchableCourse[]): GenEdDeptChip[] {
    const byPrefix = new Map<string, GenEdSearchableCourse[]>();
    for (const c of courses) {
      const prefix = departmentPrefix(c.code);
      const list = byPrefix.get(prefix);
      if (list) list.push(c);
      else byPrefix.set(prefix, [c]);
    }
    return [...byPrefix.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([prefix, list]) => ({
        prefix,
        count: list.length,
        previewTitles: list.slice(0, 6).map((c) => c.title),
        previewMore: Math.max(0, list.length - 6),
      }));
  }

  /** Regroups this plan's own genEdDetail.slots into PSU's real top-level
   * structure (Foundations / Knowledge Domain & Integrative Studies /
   * Cultural Diversity / Other Requirements), and within each
   * group, into one card per distinct domain-SET actually present -- not
   * per raw slot. Two slots that each require exactly {GHW} merge into one
   * GHW card (each still listed as its own row underneath, with its own
   * done/satisfiedBy/search/autofill -- ONLY the visual grouping and the
   * card's own aggregate bar are merged, never the underlying
   * requirement-satisfaction math). A choice slot whose domains are e.g.
   * {GA, GH} is its own card too, distinct from a plain {GH} card --
   * collapsing those together would misrepresent an "either GA or GH"
   * requirement as needing both.
   *
   * Every doneItems/totalItems/creditsDone/totalCredits figure (card AND
   * group level) is summed here from the plan's own real slots, live --
   * never a hardcoded count. percent mirrors the exact
   * round(100 * creditsDone / totalCredits) the backend already uses for
   * genEd()'s overall bar, so a card/group bar and the top bar read the
   * same way.
   *
   * Foundations, Knowledge Domain & Integrative Studies, and Cultural
   * Diversity ALWAYS render, for every student and every major -- per
   * Aarush's explicit ask, a domain with no OPEN slot in this specific
   * plan (already satisfied by a fixed major-required course, or simply
   * not one of this major's flexible picks) still gets its own card here,
   * browsable and searchable exactly like a real one, just with
   * totalItems 0 and no progress bar to show for it (see the template's
   * own `card.totalItems` check). 'other' is the one exception -- it's
   * not a fixed PSU concept, only a fallback for a slot spanning more
   * than one group (see groupKeyForSlot), so it stays conditional on
   * actually having real content. */
  groupedSlots = computed<GenEdGroupView[]>(() => {
    type CardAccum = { domains: string[]; slots: GenEdSlot[] };
    type GroupAccum = { cardOrder: string[]; cards: Map<string, CardAccum> };

    const groups = new Map<string, GroupAccum>();
    const getGroup = (key: string): GroupAccum => {
      let group = groups.get(key);
      if (!group) {
        group = { cardOrder: [], cards: new Map() };
        groups.set(key, group);
      }
      return group;
    };

    for (const slot of this.slots()) {
      const group = getGroup(this.groupKeyForSlot(slot));
      const cardKey = [...slot.domains].sort().join('|');
      let card = group.cards.get(cardKey);
      if (!card) {
        card = { domains: slot.domains, slots: [] };
        group.cards.set(cardKey, card);
        group.cardOrder.push(cardKey);
      }
      card.slots.push(slot);
    }

    // Backfill every domain this group statically covers with a card, if
    // real slots above didn't already create one -- a virtual, browse-
    // only card (empty `slots`) rather than skipping the domain entirely.
    // Re-orders each group to the canonical domain order declared in
    // GEN_ED_GROUPS (single-domain cards, real or virtual, in that fixed
    // order), with any real multi-domain CHOICE card this group already
    // had (e.g. Exploration's {GA,GH,GN,GS,INTER-D}) appended after --
    // it doesn't correspond to one static domain, so it has no natural
    // place among the single-domain cards.
    for (const def of GEN_ED_GROUPS) {
      const group = getGroup(def.key);
      const singleDomainKeys = new Set<string>(def.domains);
      const choiceCardKeys = group.cardOrder.filter((k) => !singleDomainKeys.has(k));
      const order: string[] = [];
      for (const domain of def.domains) {
        if (!group.cards.has(domain)) {
          group.cards.set(domain, { domains: [domain], slots: [] });
        }
        order.push(domain);
      }
      group.cardOrder = [...order, ...choiceCardKeys];
    }

    // Exploration has no domain code of its own -- it only gets a card
    // above when THIS major's plan happens to include a real multi-domain
    // choice slot spanning exactly {GA,GH,GN,GS,INTER-D}. A major whose
    // plan satisfies Exploration some other way (e.g. an extra domain-
    // specific course instead of a generic choice slot) would otherwise
    // never show its own 9-credit minimum anywhere, silently underselling
    // this group's real 30-credit total by exactly that much. Backfills a
    // virtual Exploration card, same principle as the per-domain backfill
    // just above, only when this group doesn't already have a real one.
    const knowledgeGroup = groups.get('knowledge_domains');
    if (knowledgeGroup && !knowledgeGroup.cards.has(EXPLORATION_DOMAIN_SET)) {
      knowledgeGroup.cards.set(EXPLORATION_DOMAIN_SET, {
        domains: ['GA', 'GH', 'GN', 'GS', 'INTER-D'],
        slots: [],
      });
      knowledgeGroup.cardOrder.push(EXPLORATION_DOMAIN_SET);
    }

    // Fixed display order (Foundations first, Other last) -- the three
    // named groups always render; 'other' only when a real slot landed
    // there.
    const orderedKeys = [...GEN_ED_GROUPS.map((g) => g.key), OTHER_GROUP.key].filter(
      (k) => k === OTHER_GROUP.key ? groups.has(k) : true,
    );

    return orderedKeys.map((groupKey) => {
      const def = GEN_ED_GROUPS.find((g) => g.key === groupKey) ?? OTHER_GROUP;
      const groupAccum = groups.get(groupKey)!;

      const cards: GenEdDomainCard[] = groupAccum.cardOrder.map((cardKey) => {
        const c = groupAccum.cards.get(cardKey)!;
        const courses = this.unionCourses(c.domains);
        let doneItems = 0;
        let totalItems = 0;
        let creditsDone = 0;
        let totalCredits = 0;
        for (const s of c.slots) {
          totalItems++;
          totalCredits += s.credits;
          if (s.done) {
            doneItems++;
            creditsDone += s.credits;
          }
        }
        // PSU's real minimum as a FLOOR, never a cap -- see
        // DOMAIN_CREDIT_MINIMUM's own doc comment. creditsDone can never
        // exceed totalCredits after this: it's summed from this same
        // plan's own slots, which totalCredits already includes before the
        // floor is applied.
        totalCredits = Math.max(totalCredits, creditFloorFor(c.domains));
        return {
          key: cardKey,
          domains: c.domains,
          title: this.cardTitle(c.domains),
          subtitle: c.domains.length > 1 ? this.cardSubtitle(c.domains) : null,
          slots: c.slots,
          doneItems,
          totalItems,
          creditsDone,
          totalCredits,
          percent: totalCredits ? Math.round((100 * creditsDone) / totalCredits) : 0,
          courses,
          deptChips: this.buildDeptChips(courses),
        };
      });

      let doneItems = 0;
      let totalItems = 0;
      let creditsDone = 0;
      let totalCredits = 0;
      for (const card of cards) {
        doneItems += card.doneItems;
        totalItems += card.totalItems;
        creditsDone += card.creditsDone;
        totalCredits += card.totalCredits;
      }

      return {
        key: groupKey,
        label: def.label,
        color: def.color,
        borderColor: def.borderColor,
        cards,
        doneItems,
        totalItems,
        creditsDone,
        totalCredits,
        percent: totalCredits ? Math.round((100 * creditsDone) / totalCredits) : 0,
      };
    });
  });

  /** Active department-prefix filter per domain card, keyed by the card's
   * own `key` (its sorted domain-set signature) -- absent/null means "show
   * this card's full course pool", the same as before this feature
   * existed. Lives here (not inside GenEdDeptChipsComponent) because it
   * has to reach both that card's "browse approved courses" list AND
   * every <app-gen-ed-slot-search> nested under it -- one filter, shared
   * across everywhere this card's courses show up, per the ask that this
   * work "everywhere, for consistency". */
  activeDeptFilter = signal<Record<string, string | null>>({});

  /** A chip's own click handler passes the RAW prefix it represents; this
   * decides whether that's a new filter or a toggle-OFF of the filter
   * already active (clicking the same chip twice) -- the chip component
   * itself stays a dumb emitter with no notion of "toggle". */
  setDeptFilter(cardKey: string, prefix: string) {
    this.activeDeptFilter.update((m) => {
      const current = m[cardKey] ?? null;
      return { ...m, [cardKey]: current === prefix ? null : prefix };
    });
  }

  /** Every card's filtered pool, computed once per (cards, filters) change
   * rather than per call.
   *
   * This has to be memoized because coursesForCard below is called FOUR
   * times per card from the template -- the search input's [courses], the
   * disclosure's count, the empty-vs-list check, and the @for that renders
   * it -- and Angular re-evaluates every one of those on each change
   * detection pass. Unmemoized, that was four full .filter() sweeps per
   * card, per pass, over pools that reach 800+ courses (GH alone), across
   * every domain on the page now that all of them render whether or not
   * the student has an open slot in them. The signal graph collapses that
   * to one sweep per card, only when the cards or the active filters
   * actually change. */
  private readonly filteredCoursesByCard = computed(() => {
    const filters = this.activeDeptFilter();
    const byCard = new Map<string, GenEdSearchableCourse[]>();
    for (const group of this.groupedSlots()) {
      for (const card of group.cards) {
        const active = filters[card.key];
        byCard.set(
          card.key,
          active ? card.courses.filter((c) => departmentPrefix(c.code) === active) : card.courses,
        );
      }
    }
    return byCard;
  });

  /** A card's course pool narrowed to its active department filter, if
   * any -- what both that card's "browse approved courses" list and every
   * <app-gen-ed-slot-search> nested under it actually render/search. Now a
   * map lookup; the filtering itself happens in filteredCoursesByCard
   * above. Falls back to the unfiltered pool if a card somehow isn't in
   * the map, so a lookup miss degrades to "show everything" rather than
   * to an empty list. */
  coursesForCard(card: GenEdDomainCard): GenEdSearchableCourse[] {
    return this.filteredCoursesByCard().get(card.key) ?? card.courses;
  }

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

  /** Union of every domain in `domains`' approved-course lists, deduped by
   * code -- what a choice slot (or a choice card's chips/browse list)
   * should offer, and just that one domain's list for a single-domain
   * case. Reads genEdCourseMap() the same way domainInfo() does; a domain
   * the static fetch hasn't resolved yet (or failed) contributes nothing
   * rather than blocking the others. Shared by slotCourses() below and by
   * groupedSlots()'s own card-building, since a card's `domains` is always
   * exactly some slot's `domains` -- same union, computed once either way. */
  private unionCourses(domains: string[]): GenEdSearchableCourse[] {
    const seen = new Set<string>();
    const out: GenEdSearchableCourse[] = [];
    for (const domain of domains) {
      for (const c of this.domainInfo(domain)?.courses ?? []) {
        if (seen.has(c.code)) continue;
        seen.add(c.code);
        out.push(c);
      }
    }
    return out;
  }

  slotCourses(slot: GenEdSlot): GenEdSearchableCourse[] {
    return this.unionCourses(slot.domains);
  }

  /** Which not-done slot (by id), if any, currently "owns" each of the
   * student's wantedCourses for the "Planned: CODE" badge -- mirrors
   * plan_progress's own single-domain Gen Ed absorption order exactly:
   * walk slots in the same plan/semester order genEdDetail.slots already
   * carries, and once a wanted course is claimed by the first not-done
   * slot it matches, remove it so a LATER slot sharing that same domain
   * (e.g. two separate "GEN ED (GHW)" slots merged into one card by the
   * PSU-style layout) can't also claim it. Without this, two same-domain
   * slots would both show the badge for one course that could only ever
   * resolve one of them -- see plan_progress's gen_ed_slots absorption
   * loop, which likewise walks slots in plan order and removes a leftover
   * from the pool the moment one slot claims it. */
  private plannedCourseBySlotId = computed<Map<number, string>>(() => {
    const remaining = [...this.planner.state().wantedCourses];
    const claimed = new Map<number, string>();
    if (!remaining.length) return claimed;
    for (const slot of this.slots()) {
      if (slot.done) continue;
      const idx = remaining.findIndex((code) =>
        slot.domains.some((d) => this.domainInfo(d)?.courses?.some((c) => c.code === code)),
      );
      if (idx === -1) continue;
      claimed.set(slot.id, remaining[idx]);
      remaining.splice(idx, 1);
    }
    return claimed;
  });

  /** The nice-to-have "Planned: CODE" badge on a not-done slot -- true when
   * one of the student's current wantedCourses is approved for one of this
   * slot's domains (cross-referenced client-side against the same static
   * course lists slotCourses() above reads) AND this is the specific slot
   * that course would actually resolve, per plannedCourseBySlotId() above. */
  plannedCourseForSlot(slot: GenEdSlot): string | null {
    return this.plannedCourseBySlotId().get(slot.id) ?? null;
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
      let found: GenEdAutofillResult | null = null;
      let everyDomainFailed = slot.domains.length > 0;
      for (const domain of slot.domains) {
        try {
          found = await this.backend.genEdAutofill(domain, context);
          everyDomainFailed = false;
        } catch {
          // Per-domain rather than around the whole loop: a choice slot
          // spans several domains, and one of them erroring shouldn't
          // abandon the others when a later domain might still have an
          // eligible course. Only if EVERY domain threw do we report a
          // failure rather than a genuine "nothing eligible".
          continue;
        }
        if (found) break;
      }
      if (found) {
        await this.planner.addWantedCourse(found.code);
        // bonusDomain: this course is ALSO approved for a still-open
        // Cultural Diversity domain elsewhere in the plan -- surfaced here
        // since it's the one place a student sees the auto-fill result
        // immediately, matching the same preference _pick_gen_ed_course
        // itself already applied when picking this course over another.
        this.toast.show(
          found.bonusDomain
            ? `Added ${found.code} — ${found.name} (also covers your ${this.domainLabel(found.bonusDomain)} requirement!)`
            : `Added ${found.code} — ${found.name}`,
        );
      } else if (everyDomainFailed) {
        this.toast.show(
          `Couldn’t reach the course service for ${slot.label} — check your connection and try again.`,
          'error',
        );
      } else {
        this.toast.show(`No eligible course found for ${slot.label}.`, 'error');
      }
    } catch (e) {
      // Was a bare try/finally: the spinner stopped and the student saw
      // NOTHING on failure, making a network blip indistinguishable from
      // "the button did nothing" -- on a button whose entire job is to do
      // something. Catches whatever the loop above didn't, notably
      // addWantedCourse. Same shape as weekly-schedule's applyForSeat.
      this.toast.show(
        e instanceof Error ? e.message : 'Could not auto-fill right now — check your connection and try again.',
        'error',
      );
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
