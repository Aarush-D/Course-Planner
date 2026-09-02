import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { StarRatingComponent } from '../ui/star-rating/star-rating.component';
import { LowCostMinor, NextSemester, Recommendation } from '../../models/course-plan.model';
import { CourseEnrollmentService, MyEnrollment } from '../../services/course-enrollment.service';
import { CourseRatingService } from '../../services/course-rating.service';
import { CourseRatingSummaryRow, SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { normalizeCourseCode } from '../../utils/course-code.util';

/** Tracks the single in-progress "this course is full, now what?" prompt for
 * a recommendation card -- only one card's decision is ever open at a time.
 * 'choosing' is the initial waitlist-vs-find-a-replacement fork;
 * 'finding-alternative' is the async gap while findOpenAlternative() runs;
 * 'alternative-found' offers the discovered sibling course for
 * confirmation; 'no-alternative' means every sibling was also full, so it
 * falls back to just offering the waitlist. Mirrors the same shape the
 * chatbot panel uses for its own enroll decision, for a consistent feel
 * across surfaces. */
interface EnrollmentDecision {
  code: string;
  estimatedWaitlistPosition: number;
  stage: 'choosing' | 'finding-alternative' | 'alternative-found' | 'no-alternative';
  alternativeCode?: string;
}

@Component({
  selector: 'app-recommendations',
  standalone: true,
  templateUrl: './recommendations.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block h-full min-h-0' },
  imports: [StarRatingComponent],
})
export class RecommendationsComponent {
  isLoading = input.required<boolean>();
  recommendations = input<Recommendation[] | null>(null);
  nextSemester = input<NextSemester | null>(null);
  tips = input<string[] | null>(null);
  rawText = input<string | null>(null);
  lowCostMinors = input<LowCostMinor[] | null>(null);

  minorAdded = output<string>();

  private readonly ratings = inject(CourseRatingService);
  private readonly supabase = inject(SupabaseService);
  private readonly enrollment = inject(CourseEnrollmentService);
  private readonly toast = inject(ToastService);
  private ratingSummaries = signal<Map<string, CourseRatingSummaryRow>>(new Map());

  /** Enroll actions require a signed-in student account -- same constraint
   * CourseEnrollmentService itself documents. */
  readonly isSignedIn = computed(() => !!this.supabase.session());

  /** Real Course objects (id + sibling options) for whatever the deterministic
   * planner already picked for next semester -- the only place this
   * component has actual Course records to cross-reference against, since
   * Recommendation itself carries no .options. Keyed by normalized code so a
   * recommendation can look up its sibling options when it happens to also
   * be a next-semester pick. */
  private readonly nextSemesterCourseByCode = computed(() => {
    const map = new Map<string, { id: string; options?: string[] }>();
    for (const c of this.nextSemester()?.courses ?? []) {
      if (c.id) map.set(normalizeCourseCode(c.id), c);
    }
    return map;
  });

  private readonly enrollmentStatuses = signal<Map<string, MyEnrollment | null>>(new Map());
  private readonly statusesLoadedFor = signal<string | null>(null);
  /** Course a recommendation's card ended up enrolled into via "Find a
   * replacement", keyed by the ORIGINAL recommendation's code -- kept
   * separate from enrollmentStatuses since the claim landed on a different
   * course code. */
  private readonly swappedCourses = signal<Map<string, string>>(new Map());

  applyingCode = signal<string | null>(null);
  /** Set when a full course's Enroll was clicked and the student now needs
   * to choose waitlist vs. a replacement -- see EnrollmentDecision above. */
  readonly decision = signal<EnrollmentDecision | null>(null);

  /** One "how is this calculated" info box for the whole scoring system,
   * not per card -- see the template near the page heading. */
  showScoreInfo = signal(false);

  constructor() {
    effect(() => {
      const codes = (this.recommendations() ?? []).map((r) => r.name).filter(Boolean);
      if (!codes.length) return;
      // See the matching comment in flowchart.component.ts -- ratings are
      // an enhancement, a failed fetch should never surface as an error.
      this.ratings.getSummaries(codes).then((map) => this.ratingSummaries.set(map)).catch(() => {});
    });

    // Loads each enrollable recommendation's real status once there's
    // something to show (signed in + at least one resolvable course code) --
    // keyed by the code list so a new set of recommendations reloads
    // instead of showing stale statuses for courses no longer even listed.
    effect(() => {
      if (!this.isSignedIn()) return;
      const codes = (this.recommendations() ?? [])
        .map((r) => this.enrollableCode(r))
        .filter((c): c is string => !!c);
      if (!codes.length) return;
      const key = codes.join(',');
      if (this.statusesLoadedFor() === key) return;
      this.statusesLoadedFor.set(key);
      Promise.all(
        codes.map(
          async (code) => [code, await this.enrollment.getMyEnrollment(code).catch(() => null)] as const,
        ),
      ).then((entries) => this.enrollmentStatuses.set(new Map(entries)));
    });
  }

  isFlowchartSource(rec: Recommendation): boolean {
    return (rec.source || '').toLowerCase().includes('flowchart');
  }

  ratingSummaryFor(courseCode: string): CourseRatingSummaryRow | undefined {
    return this.ratingSummaries().get(normalizeCourseCode(courseCode));
  }

  /** The real, claimable course code for this recommendation, or null if
   * there isn't one to enroll in. Recommendations from the deterministic
   * scoring engine are always sourced from the real course catalog, so
   * `rec.name` is already the same normalized course code Course.id uses
   * elsewhere (see Backend/app.py's recommendation serialization) -- the
   * `type === 'slot'` guard is just forward-compatible defense in case a
   * future generic-elective-slot recommendation shows up here. */
  enrollableCode(rec: Recommendation): string | null {
    if (rec.type === 'slot') return null;
    const code = (rec.name || '').trim();
    return code || null;
  }

  /** Sibling course codes for the same requirement slot, when this
   * recommendation also happens to be one of next semester's picks (the
   * only source of real Course.options this component has). Empty --
   * never undefined -- for a recommendation with no such match, so
   * findOpenAlternative() can still be called safely and just report no
   * open alternative. */
  private optionsFor(code: string): string[] {
    return this.nextSemesterCourseByCode().get(normalizeCourseCode(code))?.options ?? [];
  }

  statusFor(code: string): MyEnrollment | null {
    return this.enrollmentStatuses().get(code) ?? null;
  }

  swapFor(code: string): string | null {
    return this.swappedCourses().get(code) ?? null;
  }

  decisionFor(code: string): EnrollmentDecision | null {
    const d = this.decision();
    return d && d.code === code ? d : null;
  }

  async enroll(rec: Recommendation) {
    const code = this.enrollableCode(rec);
    if (!code || this.applyingCode()) return;
    this.applyingCode.set(code);
    try {
      const { seatAvailable, estimatedWaitlistPosition } = await this.enrollment.checkAvailability(code);
      if (seatAvailable) {
        const result = await this.enrollment.apply(code);
        this.enrollmentStatuses.update((m) => new Map(m).set(code, result));
        this.toast.show("You're in — a seat is held for you.", 'success');
      } else {
        this.decision.set({ code, estimatedWaitlistPosition, stage: 'choosing' });
      }
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : `Could not check ${code} right now.`, 'error');
    } finally {
      this.applyingCode.set(null);
    }
  }

  async joinWaitlist() {
    const d = this.decision();
    if (!d) return;
    this.applyingCode.set(d.code);
    try {
      const result = await this.enrollment.apply(d.code);
      this.enrollmentStatuses.update((m) => new Map(m).set(d.code, result));
      this.toast.show(
        result.status === 'enrolled'
          ? "You're in — a seat is held for you."
          : `You're #${result.position} on the waitlist.`,
        'success',
      );
      this.decision.set(null);
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : 'Could not join the waitlist right now.', 'error');
    } finally {
      this.applyingCode.set(null);
    }
  }

  async findReplacement() {
    const d = this.decision();
    if (!d) return;
    this.decision.set({ ...d, stage: 'finding-alternative' });
    try {
      const altCode = await this.enrollment.findOpenAlternative(this.optionsFor(d.code));
      this.decision.set(
        altCode ? { ...d, stage: 'alternative-found', alternativeCode: altCode } : { ...d, stage: 'no-alternative' },
      );
    } catch {
      this.decision.set({ ...d, stage: 'no-alternative' });
    }
  }

  async enrollInAlternative() {
    const d = this.decision();
    const altCode = d?.alternativeCode;
    if (!d || !altCode) return;
    this.applyingCode.set(altCode);
    try {
      const result = await this.enrollment.apply(altCode);
      this.swappedCourses.update((m) => new Map(m).set(d.code, altCode));
      this.toast.show(
        result.status === 'enrolled'
          ? `You're in ${altCode} — a seat is held for you.`
          : `${altCode} is full too — you're #${result.position} on its waitlist.`,
        'success',
      );
      this.decision.set(null);
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : `Could not enroll in ${altCode} right now.`, 'error');
    } finally {
      this.applyingCode.set(null);
    }
  }

  cancelDecision() {
    this.decision.set(null);
  }
}
