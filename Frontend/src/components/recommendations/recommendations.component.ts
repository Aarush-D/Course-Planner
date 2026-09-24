import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { StarRatingComponent } from '../ui/star-rating/star-rating.component';
import { LowCostMinor, NextSemester, Recommendation } from '../../models/course-plan.model';
import {
  CourseEnrollmentService, CourseFullError, MyEnrollment, courseFullMessage,
} from '../../services/course-enrollment.service';
import { CourseRatingService } from '../../services/course-rating.service';
import { CourseRatingSummaryRow, SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { normalizeCourseCode } from '../../utils/course-code.util';

/** Inline follow-up shown on a card whose Enroll found the course full. A
 * full course is refused outright (there is no waitlist -- see
 * CourseEnrollmentService.CourseFullError), so the only question left is
 * whether to take an open sibling option instead: 'finding-alternative'
 * while that search runs, 'alternative-found' with the discovered course
 * awaiting confirmation. Mirrors the chatbot panel's shape for a
 * consistent feel across surfaces. */
interface EnrollmentDecision {
  code: string;
  stage: 'finding-alternative' | 'alternative-found';
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
  private readonly sessionUserId = computed(() => this.supabase.session()?.user.id ?? null);

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
  /** Set while a full course's Enroll is looking for (or offering) an open
   * alternative -- see EnrollmentDecision above. */
  readonly decision = signal<EnrollmentDecision | null>(null);

  /** One "how is this calculated" info box for the whole scoring system,
   * not per card -- see the template near the page heading. */
  showScoreInfo = signal(false);

  constructor() {
    // Keeps the live seat store warm for every enrollable recommendation,
    // so a card's Enroll button reads "Full" the moment that becomes true
    // -- whether this student or any other took the last seat.
    effect(() => {
      const codes = (this.recommendations() ?? [])
        .map((r) => this.enrollableCode(r))
        .filter((c): c is string => !!c);
      if (codes.length) this.enrollment.getSeatPools(codes).catch(() => {});
    });

    effect(() => {
      const codes = (this.recommendations() ?? []).map((r) => r.name).filter(Boolean);
      if (!codes.length) return;
      // See the matching comment in flowchart.component.ts -- ratings are
      // an enhancement, a failed fetch should never surface as an error.
      this.ratings.getSummaries(codes).then((map) => this.ratingSummaries.set(map)).catch(() => {});
    });

    // Loads each enrollable recommendation's real status once there's
    // something to show (signed in + at least one resolvable course code) --
    // keyed by the session user id AND the code list, so a new set of
    // recommendations reloads instead of showing stale statuses for courses
    // no longer even listed, and a different student signing in on the same
    // recommendations never inherits the previous one's "Enrolled" labels.
    // Signing out clears everything outright: this is per-account data.
    effect(() => {
      const userId = this.sessionUserId();
      if (!userId) {
        this.enrollmentStatuses.set(new Map());
        this.statusesLoadedFor.set(null);
        this.swappedCourses.set(new Map());
        this.decision.set(null);
        return;
      }
      const codes = (this.recommendations() ?? [])
        .map((r) => this.enrollableCode(r))
        .filter((c): c is string => !!c);
      if (!codes.length) return;
      const key = `${userId}|${codes.join(',')}`;
      if (this.statusesLoadedFor() === key) return;
      this.statusesLoadedFor.set(key);
      Promise.all(
        codes.map(
          async (code) => [code, await this.enrollment.getMyEnrollment(code).catch(() => null)] as const,
        ),
      ).then((entries) => {
        // The user or list changed while this batch was in flight -- discard it.
        if (this.statusesLoadedFor() !== key) return;
        this.enrollmentStatuses.set(new Map(entries));
      });
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

  /** Live: true once the shared pool for this course is known to be at
   * capacity. Unknown reads as not full -- never "Full" before a real row. */
  isFull(code: string): boolean {
    return this.enrollment.isFull(code);
  }

  /** An open seat enrolls immediately; a full course is refused -- the
   * student is told (courseFullMessage) and nothing is claimed -- and, when
   * the recommendation has sibling options, _onCourseFull() offers the
   * first one with an open seat. Checked against the live pool first, then
   * enforced again by the server (apply() throws CourseFullError if the
   * last seat went in between). */
  async enroll(rec: Recommendation) {
    const code = this.enrollableCode(rec);
    if (!code || this.applyingCode()) return;
    this.applyingCode.set(code);
    try {
      const { seatAvailable } = await this.enrollment.checkAvailability(code);
      if (!seatAvailable) {
        await this._onCourseFull(code);
        return;
      }
      const result = await this.enrollment.apply(code);
      this.enrollmentStatuses.update((m) => new Map(m).set(code, result));
      this.toast.show("You’re in — a seat is held for you.", 'success');
    } catch (e) {
      if (e instanceof CourseFullError) await this._onCourseFull(code);
      else this.toast.show(e instanceof Error ? e.message : `Could not check ${code} right now.`, 'error');
    } finally {
      this.applyingCode.set(null);
    }
  }

  /** The one place "this course is full" is handled: say so, and -- only
   * when there are sibling options -- look for an open one. The toast
   * waits for that search so the student reads one message, not two. */
  private async _onCourseFull(code: string): Promise<void> {
    const options = this.optionsFor(code);
    if (!options.length) {
      this.toast.show(courseFullMessage(code), 'error');
      return;
    }
    this.decision.set({ code, stage: 'finding-alternative' });
    try {
      const altCode = await this.enrollment.findOpenAlternative(options);
      if (altCode) {
        this.decision.set({ code, stage: 'alternative-found', alternativeCode: altCode });
        this.toast.show(courseFullMessage(code), 'error');
      } else {
        this.decision.set(null);
        this.toast.show(`${courseFullMessage(code)} Every alternative for this requirement is full too.`, 'error');
      }
    } catch {
      this.decision.set(null);
      this.toast.show(courseFullMessage(code), 'error');
    }
  }

  async enrollInAlternative() {
    const d = this.decision();
    const altCode = d?.alternativeCode;
    if (!d || !altCode || this.applyingCode()) return;
    this.applyingCode.set(altCode);
    try {
      await this.enrollment.apply(altCode);
      this.swappedCourses.update((m) => new Map(m).set(d.code, altCode));
      this.toast.show(`You’re in ${altCode} — a seat is held for you.`, 'success');
      this.decision.set(null);
    } catch (e) {
      if (e instanceof CourseFullError) {
        this.decision.set(null);
        this.toast.show(`${altCode} just filled up too — its seats can’t be registered.`, 'error');
      } else {
        this.toast.show(e instanceof Error ? e.message : `Could not enroll in ${altCode} right now.`, 'error');
      }
    } finally {
      this.applyingCode.set(null);
    }
  }

  cancelDecision() {
    this.decision.set(null);
  }
}
