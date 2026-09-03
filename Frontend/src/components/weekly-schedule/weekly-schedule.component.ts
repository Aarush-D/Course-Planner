import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { Location } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { animateModalIn, animateModalOut } from '../../animations/modal-fade';
import { ModalFocusTrapDirective } from '../../directives/modal-focus-trap.directive';
import { Course, CourseGraphEntry } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import {
  CourseEnrollmentService,
  EnrollmentStatus,
  MyEnrollment,
  SeatPoolInfo,
} from '../../services/course-enrollment.service';
import { CourseGroupSummary, CourseGroupService } from '../../services/course-group.service';
import { CourseRatingService } from '../../services/course-rating.service';
import { StudentProfileService } from '../../services/student-profile.service';
import { CourseRatingSummaryRow, SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { normalizeCourseCode } from '../../utils/course-code.util';
import { linkQueryParam } from '../../utils/url-state';
import {
  DAY_LABELS, Modality, ScheduleSlot, SeatAvailability, WEEKDAY_CODES,
  dummyBuildingFor, dummyModalityFor, dummyProfessorFor, dummySeatAvailabilityFor, dummySlotFor, formatClockTime,
} from '../../utils/dummy-schedule.util';
import { CourseReviewsModalComponent } from '../course-reviews-modal/course-reviews-modal.component';
import { StarRatingComponent } from '../ui/star-rating/star-rating.component';

const GRID_START_MINUTES = 8 * 60; // 8:00 AM
const GRID_END_MINUTES = 17 * 60; // 5:00 PM
const PX_PER_MINUTE = 1;

interface PlacedBlock {
  course: Course;
  slot: ScheduleSlot;
  seats: SeatAvailability;
  top: number;
  height: number;
}

/** A Mon–Fri weekly grid of the student's "Recommended Next Semester"
 * courses, at MADE-UP times (see dummy-schedule.util.ts for why real ones
 * don't exist yet) -- lets a student see what a real term could look like
 * shaped out, click a block for the course's full info, and mark it as
 * planned. Intentionally scoped to recommended (not completed) courses --
 * this is about what to take next, not a record of what's already done. */
@Component({
  selector: 'app-weekly-schedule',
  standalone: true,
  templateUrl: './weekly-schedule.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ModalFocusTrapDirective, CourseReviewsModalComponent, StarRatingComponent],
})
export class WeeklyScheduleComponent {
  private readonly injector = inject(Injector);
  private readonly supabase = inject(SupabaseService);
  private readonly enrollment = inject(CourseEnrollmentService);
  private readonly groups = inject(CourseGroupService);
  private readonly profiles = inject(StudentProfileService);
  private readonly toast = inject(ToastService);
  private readonly backend = inject(BackendService);
  private readonly ratings = inject(CourseRatingService);
  private readonly _route = inject(ActivatedRoute);
  private readonly _location = inject(Location);

  courses = input<Course[]>([]);
  scheduledCourseIds = input<string[]>([]);
  /** For the modal's real "This course also unlocks" section -- same
   * major/catalog-year scoping BackendService.courseGraph() needs
   * elsewhere (see course-explorer.component.ts). Optional/nullable since
   * an undecided student never reaches this component at all (Home page
   * only renders it once a real plan exists), but there's no reason to
   * force a value the caller might not have handy. */
  major = input<string | null>();
  catalogYear = input<number | undefined>();

  toggleScheduled = output<string>();

  readonly isSignedIn = computed(() => !!this.supabase.session());

  /** Real, shared seat/waitlist/group/networking state for whichever
   * course the modal currently has open -- loaded fresh each time
   * openCourse() runs (see below), separate from the sample/dummy data
   * above them in the modal, which stays exactly as illustrative-only as
   * before. Kept simple as plain component signals rather than
   * per-course caching: this modal only ever shows one course at a time,
   * so there's nothing to keep in sync across courses. */
  seatPool = signal<SeatPoolInfo | null>(null);
  myEnrollment = signal<MyEnrollment | null>(null);
  groupStatus = signal<CourseGroupSummary | null>(null);
  classmateLinkedins = signal<string[]>([]);
  applyBusy = signal(false);
  groupBusy = signal(false);
  joinCodeInput = signal('');
  justCreatedInviteCode = signal<string | null>(null);

  /** Real course_rating_summary for whichever course the modal has open --
   * unlike everything above, ratings need no signed-in session at all (see
   * CourseRatingService), so this loads for every visitor, not gated by
   * isSignedIn(). */
  courseRatingSummary = signal<CourseRatingSummaryRow | null>(null);
  reviewsModalOpen = signal(false);

  /** The current major's full prereq/unlock graph, loaded once (and
   * reloaded on a major/catalog-year change) exactly like
   * course-explorer.component.ts's own courseGraph fetch -- gives the
   * modal's "This course also unlocks" section real course codes/names
   * instead of the flowchart card's plain unlocks *count*. */
  private readonly courseGraph = signal<CourseGraphEntry[]>([]);
  private readonly courseGraphByCode = computed(
    () => new Map(this.courseGraph().map((c) => [c.code, c])),
  );

  readonly days = WEEKDAY_CODES;
  readonly dayLabels = DAY_LABELS;
  readonly gridHeight = (GRID_END_MINUTES - GRID_START_MINUTES) * PX_PER_MINUTE;
  readonly hourMarks = Array.from(
    { length: GRID_END_MINUTES / 60 - GRID_START_MINUTES / 60 + 1 },
    (_, i) => GRID_START_MINUTES / 60 + i,
  );

  /** course_code -> status for every seat this student already holds.
   * Backs the "claim the seats for everything I scheduled" prompt below;
   * refreshed after any apply/drop so the prompt can't offer a course the
   * student just claimed. Empty for a signed-out visitor. */
  private readonly myEnrollments = signal<Map<string, EnrollmentStatus>>(new Map());

  /** Scheduled courses with no seat and no waitlist spot yet.
   *
   * The gap this closes: "Add to Schedule" works signed-OUT (it's just
   * planner state), but holding a real seat never can -- course_enrollments
   * is FK'd to auth.users, and claim_course_seat rejects a null auth.uid().
   * So a student can line up a whole term's worth of courses, sign in, and
   * still hold nothing, with the only route to a seat being to reopen each
   * course modal and hit Apply one at a time. */
  readonly unclaimedScheduledCourses = computed(() => {
    if (!this.isSignedIn()) return [];
    const held = this.myEnrollments();
    return this.scheduledCourseIds().filter(
      (code) => !held.has(code) && !held.has(code.toUpperCase()),
    );
  });

  claimAllBusy = signal(false);

  /** What the modal renders. Downstream of selectedCourseCode below --
   * never written directly except by the effect that resolves one into
   * the other. */
  selectedCourse = signal<Course | null>(null);

  /** Which course the URL says is open. A bare code rather than the
   * resolved Course, because that's what has to survive a reload: on a
   * pasted link this is read before courses() has arrived, so there is
   * nothing yet to resolve it against. */
  readonly selectedCourseCode = signal<string | null>(null);

  /** True while a history entry WE pushed is the current one. Decides
   * whether closeCourse pops that entry or just drops the param. */
  private _pushedHistoryEntry = false;

  /** Applies for every scheduled course the student doesn't already hold.
   *
   * Sequential rather than Promise.all -- but NOT for correctness, and
   * that distinction is worth stating so nobody later cites this comment
   * to justify something it doesn't actually support. Parallel would be
   * safe: claim_course_seat's advisory lock is keyed on (course_code,
   * student_id) (migration 0011:126), so two different courses never
   * contend with each other, and waitlist rank is computed strictly
   * within a single course_code (0011:183-185) -- there is no ordering
   * relationship between two different courses for one student that
   * parallelism could scramble.
   *
   * It is sequential for two plainer reasons: it doesn't burst N
   * simultaneous RPCs for what is a convenience action, and one at a time
   * is what keeps the per-course reporting below honest when some
   * succeed and others fail.
   *
   * Reports per-course truthfully rather than claiming success: a full
   * course can only ever return a waitlist spot, and saying "4 seats
   * claimed" when one of them is 12th in line would be a lie the student
   * discovers at registration. */
  async claimAllScheduledSeats() {
    const codes = this.unclaimedScheduledCourses();
    if (!codes.length || this.claimAllBusy()) return;
    this.claimAllBusy.set(true);
    let enrolled = 0;
    let waitlisted = 0;
    const failed: string[] = [];
    try {
      for (const code of codes) {
        try {
          const result = await this.enrollment.apply(code);
          if (result.status === 'enrolled') enrolled++;
          else waitlisted++;
        } catch {
          // One course failing must not abandon the rest -- a single bad
          // code shouldn't cost the student the seats they could have had.
          failed.push(code);
        }
      }
      await this._refreshMyEnrollments();
      const parts: string[] = [];
      if (enrolled) parts.push(`${enrolled} ${enrolled === 1 ? 'seat' : 'seats'} held`);
      if (waitlisted) parts.push(`${waitlisted} waitlisted`);
      if (failed.length) parts.push(`${failed.length} couldn’t be applied for`);
      // No `|| 'Nothing to apply for'` fallback: the early return above
      // guarantees at least one course was attempted, so `parts` is never
      // empty here and a fallback would just be unreachable code implying
      // a state that can't happen.
      this.toast.show(parts.join(', '), failed.length ? 'error' : 'success');
    } finally {
      this.claimAllBusy.set(false);
    }
  }

  private async _refreshMyEnrollments(): Promise<void> {
    try {
      this.myEnrollments.set(await this.enrollment.getMyEnrollments());
    } catch {
      // Leave the last known map in place -- a failed refresh should not
      // make the prompt re-offer courses the student already claimed.
    }
  }

  private readonly modalBackdrop = viewChild<ElementRef<HTMLElement>>('modalBackdrop');
  private readonly modalPanel = viewChild<ElementRef<HTMLElement>>('modalPanel');

  /** courseId -> its dummy slot, computed once per course list so every
   * block on every day column reads from the same stable value per render. */
  private readonly slotsByCourse = computed(() => {
    const map = new Map<string, ScheduleSlot>();
    for (const c of this.courses()) {
      if (c.id) map.set(c.id, dummySlotFor(c.id));
    }
    return map;
  });

  constructor() {
    // Loads (and reloads on a major/catalog-year change) the whole major's
    // course graph once, same pattern as course-explorer.component.ts --
    // every open of the modal then just looks its course up client-side in
    // courseGraphByCode rather than round-tripping per open.
    effect(() => {
      const major = this.major();
      const year = this.catalogYear();
      if (!major) {
        this.courseGraph.set([]);
        return;
      }
      this.backend.courseGraph(major, year).then((list) => this.courseGraph.set(list));
    });

    // 'push', unlike every other param in this app: this modal covers the
    // screen, and both a phone's back gesture and the desktop back button
    // are expected to close a thing like that rather than leave the page.
    linkQueryParam({
      key: 'course',
      signal: this.selectedCourseCode,
      toParam: (code) => code,
      fromParam: (param) => param,
      history: 'push',
    });

    // Keeps the claim-all prompt honest across a sign-in that happens
    // without a page load (the common path -- the login page is a route,
    // not a document navigation), so the prompt appears the moment a
    // student signs in rather than only on the next full boot.
    effect(() => {
      if (!this.isSignedIn()) {
        this.myEnrollments.set(new Map());
        return;
      }
      this._refreshMyEnrollments();
    });

    // Resolves the URL's course code into the actual Course to render.
    // Depends on courses() as well as the code, which is what makes a
    // pasted link work: on first load the code arrives long before the
    // plan does, this finds nothing, and it simply runs again -- with no
    // retry logic of its own -- the moment courses() populates.
    effect(() => {
      const code = this.selectedCourseCode();
      const courses = this.courses();
      const current = untracked(() => this.selectedCourse());
      if (!code) {
        // Covers the back button and the forward-into-nothing case. No
        // exit animation on purpose: a browser-driven navigation should
        // feel immediate, not wait on a fade.
        if (current) this.selectedCourse.set(null);
        return;
      }
      if (current?.id === code) return;
      const course = courses.find((c) => c.id === code);
      if (course) this._showCourse(course);
    });
  }

  blocksForDay(day: string): PlacedBlock[] {
    const blocks: PlacedBlock[] = [];
    for (const course of this.courses()) {
      if (!course.id) continue;
      const slot = this.slotsByCourse().get(course.id);
      if (!slot || !slot.days.includes(day)) continue;
      const top = (slot.startMinutes - GRID_START_MINUTES) * PX_PER_MINUTE;
      const height = (slot.endMinutes - slot.startMinutes) * PX_PER_MINUTE;
      blocks.push({ course, slot, seats: dummySeatAvailabilityFor(course.id), top, height });
    }
    // Sort by start time -- the block list otherwise follows whatever
    // order `courses()` happened to arrive in, which has no relation to
    // the slot's start time (that's hash-derived). Visually the blocks
    // are positioned by `top`, so an unsorted DOM order left keyboard/
    // screen-reader tab order out of sync with the visual top-to-bottom
    // order within a day column.
    return blocks.sort((a, b) => a.slot.startMinutes - b.slot.startMinutes);
  }

  formatTime(minutes: number): string {
    return formatClockTime(minutes);
  }

  /** Short label for the block itself. */
  seatsShortLabel(seats: SeatAvailability): string {
    if (seats.status === 'open') return `${seats.seatsLeft} left`;
    if (seats.status === 'waitlist') return 'Waitlist';
    return 'Full';
  }

  /** Sample meeting slot for the modal's course-info box -- reuses the
   * same per-course slot the grid blocks already computed (slotsByCourse)
   * instead of recomputing, falling back to a direct dummySlotFor() call
   * only for the edge case of a course that isn't in courses() at all
   * (shouldn't happen in practice: the modal only ever opens from a block
   * built out of that same list). */
  sampleSlotFor(course: Course): ScheduleSlot {
    const fromMap = course.id ? this.slotsByCourse().get(course.id) : undefined;
    return fromMap ?? dummySlotFor(course.id || course.name);
  }

  sampleDaysLabel(course: Course): string {
    return this.sampleSlotFor(course).days.map((d) => this.dayLabels[d]).join('/');
  }

  professorFor(course: Course): string {
    return dummyProfessorFor(course.id || course.name);
  }

  buildingFor(course: Course): string {
    return dummyBuildingFor(course.id || course.name);
  }

  modalityFor(course: Course): Modality {
    return dummyModalityFor(course.id || course.name);
  }

  /** Real registration status, computed from the SAME seatPool() signal
   * the "Real seat, held for you" box below reads -- deliberately not a
   * second network round-trip through CourseEnrollmentService.checkAvailability()
   * (which would just re-fetch this exact row): reusing the one already-
   * loaded value is what actually guarantees this line and that box can
   * never show contradicting numbers, and the math here is identical to
   * checkAvailability()'s own (seatsTaken < capacity). */
  registrationStatusFor(pool: SeatPoolInfo): { seatAvailable: boolean; label: string } {
    const seatAvailable = pool.seatsTaken < pool.capacity;
    return {
      seatAvailable,
      label: seatAvailable
        ? `Open — ${pool.capacity - pool.seatsTaken} of ${pool.capacity} seats left`
        : `Full — ${pool.seatsTaken} of ${pool.capacity} taken`,
    };
  }

  /** Real unlocked-course codes/names for the modal's "This course also
   * unlocks" section, from the major's course graph (see courseGraph
   * above) -- Course.unlocks on the model itself is only ever a count. */
  unlocksFor(course: Course): { code: string; name: string | null }[] {
    if (!course.id) return [];
    const byCode = this.courseGraphByCode();
    const entry = byCode.get(course.id);
    if (!entry) return [];
    return entry.unlocks.map((code) => ({ code, name: byCode.get(code)?.name ?? null }));
  }

  openReviewsModal() {
    this.reviewsModalOpen.set(true);
  }

  closeReviewsModal() {
    this.reviewsModalOpen.set(false);
  }

  isScheduled(course: Course): boolean {
    return !!course.id && this.scheduledCourseIds().includes(course.id.toUpperCase());
  }

  /** Opening is expressed as a URL change, not a direct signal write: the
   * ?course= param is what makes a course modal linkable, and routing the
   * open through it keeps one code path for all three ways this modal can
   * appear (a click here, a pasted link, a forward button). The effect in
   * the constructor is what actually mounts it. */
  openCourse(course: Course) {
    if (!course.id) return; // only id-bearing courses are rendered as blocks
    // Whether WE are the ones adding the history entry decides how
    // closeCourse has to undo it -- see the comment there.
    this._pushedHistoryEntry = !this._route.snapshot.queryParamMap.get('course');
    this.selectedCourseCode.set(course.id);
  }

  async closeCourse() {
    await this._animateOut();
    if (this._pushedHistoryEntry) {
      // We pushed an entry to open this, so the honest undo is to pop it.
      // Clearing the signal instead would push a SECOND entry, and Back
      // would then walk the student back INTO the modal they just closed.
      this._pushedHistoryEntry = false;
      this._location.back();
    } else {
      // Arrived here by pasted link or reload -- there is no entry of ours
      // to pop, so drop the param directly. Back still leaves the page,
      // which is right: the modal was the whole reason they were here.
      this.selectedCourseCode.set(null);
    }
  }

  /** Everything openCourse used to do inline. Driven only by the effect
   * above it, so a deep-linked open and a clicked one are byte-identical. */
  private _showCourse(course: Course) {
    this.selectedCourse.set(course);
    this.seatPool.set(null);
    this.myEnrollment.set(null);
    this.groupStatus.set(null);
    this.classmateLinkedins.set([]);
    this.joinCodeInput.set('');
    this.justCreatedInviteCode.set(null);
    this.courseRatingSummary.set(null);
    this.reviewsModalOpen.set(false);
    afterNextRender(() => this._animateIn(), { injector: this.injector });
    if (course.id) {
      this._loadRealCourseState(course.id);
      this._loadRatingSummary(course.id);
    }
  }

  readonly closeCourseFn = () => this.closeCourse();

  onToggleScheduled(course: Course) {
    if (course.id) this.toggleScheduled.emit(course.id);
  }

  /** The RPC call itself is the ONLY thing gating the busy state / feedback
   * -- claim_course_seat already returns the definitive (status, position),
   * so there's no reason to make the student wait through a SECOND
   * sequential round-trip (re-fetching the pool's display counts) before
   * they see any result. That refresh still happens, just fire-and-forget
   * in the background, so the count updates a beat later instead of
   * blocking the "you're in" feedback that matters right now. */
  async applyForSeat(courseCode: string) {
    this.applyBusy.set(true);
    try {
      const result = await this.enrollment.apply(courseCode);
      this.myEnrollment.set(result);
      this.toast.show(
        result.status === 'enrolled' ? "You’re in — a seat is held for you." : `Full — you’re #${result.position} on the waitlist.`,
        'success',
      );
      this._refreshSeatPool(courseCode);
      this._refreshMyEnrollments();
    } catch (e) {
      this.toast.show(
        e instanceof Error ? e.message : 'Could not apply right now — check your connection and try again.',
        'error',
      );
    } finally {
      this.applyBusy.set(false);
    }
  }

  async dropSeat(courseCode: string) {
    // Confirmed rather than immediate: dropping hands the seat straight to
    // the next waitlisted student (release_freed_course_seat's promotion
    // trigger, migration 0011), so there is nothing to undo afterward --
    // re-applying puts this student at the BACK of the waitlist, behind
    // whoever just took the seat. It's the one irreversible action in this
    // modal, and it sat a single stray click away.
    const waitlisted = this.myEnrollment()?.status === 'waitlisted';
    const proceed = window.confirm(
      waitlisted
        ? `Leave the waitlist for ${courseCode}? Rejoining puts you at the back of the line.`
        : `Give up your seat in ${courseCode}? It goes to the next student on the waitlist immediately, and you can’t take it back.`,
    );
    if (!proceed) return;
    this.applyBusy.set(true);
    try {
      await this.enrollment.drop(courseCode);
      this.myEnrollment.set(null);
      this.toast.show(waitlisted ? 'Left the waitlist.' : 'Seat dropped.', 'success');
      this._refreshSeatPool(courseCode);
      this._refreshMyEnrollments();
    } catch (e) {
      this.toast.show(
        e instanceof Error ? e.message : 'Could not drop right now — check your connection and try again.',
        'error',
      );
    } finally {
      this.applyBusy.set(false);
    }
  }

  private _refreshSeatPool(courseCode: string): void {
    this.enrollment.getSeatPool(courseCode).then(
      (pool) => this.seatPool.set(pool),
      () => {}, // display-only refresh -- a failure here isn't worth surfacing
    );
  }

  async createGroup(courseCode: string) {
    this.groupBusy.set(true);
    try {
      const { groupId, inviteCode } = await this.groups.createGroup(courseCode);
      this.justCreatedInviteCode.set(inviteCode);
      this.groupStatus.set(await this.groups.getGroupStatus(groupId, inviteCode));
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : 'Could not create a group right now.', 'error');
    } finally {
      this.groupBusy.set(false);
    }
  }

  async joinGroup(courseCode: string) {
    const code = this.joinCodeInput().trim();
    if (!code) return;
    this.groupBusy.set(true);
    try {
      const { groupId, inviteCode } = await this.groups.joinGroup(code);
      this.joinCodeInput.set('');
      this.groupStatus.set(await this.groups.getGroupStatus(groupId, inviteCode));
      this.toast.show('Joined the group.', 'success');
    } catch {
      this.toast.show("That invite code didn’t work.", 'error');
    } finally {
      this.groupBusy.set(false);
    }
  }

  async leaveGroup(courseCode: string) {
    const group = this.groupStatus();
    if (!group) return;
    this.groupBusy.set(true);
    try {
      await this.groups.leaveGroup(group.groupId);
      this.groupStatus.set(null);
      this.justCreatedInviteCode.set(null);
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : 'Could not leave the group right now.', 'error');
    } finally {
      this.groupBusy.set(false);
    }
  }

  /** Best-effort, fire-and-forget from openCourse() -- a signed-out
   * visitor (the common case) or a network hiccup should never block the
   * modal from opening or degrade anything else in it; every piece here
   * fails silently into its own empty/null state instead of surfacing an
   * error for what is, for most visitors, an entirely optional add-on. */
  private async _loadRealCourseState(courseCode: string): Promise<void> {
    try {
      this.seatPool.set(await this.enrollment.getSeatPool(courseCode));
    } catch {
      // leave seatPool null -- section below just won't render
    }
    if (!this.isSignedIn()) return;
    try {
      this.myEnrollment.set(await this.enrollment.getMyEnrollment(courseCode));
    } catch {
      // leave myEnrollment null
    }
    try {
      this.groupStatus.set(await this.groups.findMyGroup(courseCode));
    } catch {
      // leave groupStatus null
    }
    if (this.myEnrollment()?.status === 'enrolled') {
      try {
        this.classmateLinkedins.set(await this.profiles.getClassmateLinkedins(courseCode));
      } catch {
        // leave classmateLinkedins empty
      }
    }
  }

  /** Real, anonymous course_rating_summary for the open course -- kept
   * separate from _loadRealCourseState above since ratings need no
   * account at all (see CourseRatingService), so unlike that whole flow
   * this always fires, signed in or not. Same batched getSummaries() call
   * flowchart.component.ts uses for its recommended-course cards, just
   * called with a single code here since the modal only ever shows one
   * course at a time. */
  private _loadRatingSummary(courseCode: string): void {
    this.ratings.getSummaries([courseCode]).then(
      (map) => this.courseRatingSummary.set(map.get(normalizeCourseCode(courseCode)) ?? null),
      () => {}, // reviews are a nice-to-have here too -- fail silently into "no summary"
    );
  }

  private _animateIn() {
    const b = this.modalBackdrop();
    const p = this.modalPanel();
    if (b && p) animateModalIn(b.nativeElement, p.nativeElement);
  }

  private async _animateOut(): Promise<void> {
    const b = this.modalBackdrop();
    const p = this.modalPanel();
    if (b && p) await animateModalOut(b.nativeElement, p.nativeElement);
  }
}
