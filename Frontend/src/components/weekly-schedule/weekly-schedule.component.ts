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
  viewChild,
} from '@angular/core';
import { animateModalIn, animateModalOut } from '../../animations/modal-fade';
import { ModalFocusTrapDirective } from '../../directives/modal-focus-trap.directive';
import { Course, CourseGraphEntry } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import { CourseEnrollmentService, MyEnrollment, SeatPoolInfo } from '../../services/course-enrollment.service';
import { CourseGroupSummary, CourseGroupService } from '../../services/course-group.service';
import { CourseRatingService } from '../../services/course-rating.service';
import { StudentProfileService } from '../../services/student-profile.service';
import { CourseRatingSummaryRow, SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { normalizeCourseCode } from '../../utils/course-code.util';
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

  selectedCourse = signal<Course | null>(null);
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

  openCourse(course: Course) {
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

  async closeCourse() {
    await this._animateOut();
    this.selectedCourse.set(null);
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
        result.status === 'enrolled' ? "You're in — a seat is held for you." : `Full — you're #${result.position} on the waitlist.`,
        'success',
      );
      this._refreshSeatPool(courseCode);
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : 'Could not apply right now.', 'error');
    } finally {
      this.applyBusy.set(false);
    }
  }

  async dropSeat(courseCode: string) {
    this.applyBusy.set(true);
    try {
      await this.enrollment.drop(courseCode);
      this.myEnrollment.set(null);
      this.toast.show('Dropped.', 'success');
      this._refreshSeatPool(courseCode);
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : 'Could not drop right now.', 'error');
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
      this.toast.show("That invite code didn't work.", 'error');
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
