import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { animateModalIn, animateModalOut } from '../../animations/modal-fade';
import { ModalFocusTrapDirective } from '../../directives/modal-focus-trap.directive';
import { Course } from '../../models/course-plan.model';
import { CourseEnrollmentService, MyEnrollment, SeatPoolInfo } from '../../services/course-enrollment.service';
import { CourseGroupSummary, CourseGroupService } from '../../services/course-group.service';
import { StudentProfileService } from '../../services/student-profile.service';
import { SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import {
  DAY_LABELS, ScheduleSlot, SeatAvailability, WEEKDAY_CODES,
  dummySeatAvailabilityFor, dummySlotFor, formatClockTime,
} from '../../utils/dummy-schedule.util';

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
  imports: [ModalFocusTrapDirective],
})
export class WeeklyScheduleComponent {
  private readonly injector = inject(Injector);
  private readonly supabase = inject(SupabaseService);
  private readonly enrollment = inject(CourseEnrollmentService);
  private readonly groups = inject(CourseGroupService);
  private readonly profiles = inject(StudentProfileService);
  private readonly toast = inject(ToastService);

  courses = input<Course[]>([]);
  scheduledCourseIds = input<string[]>([]);

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

  seatsFor(course: Course): SeatAvailability {
    return dummySeatAvailabilityFor(course.id || course.name);
  }

  /** Short label for the block itself (room's tight -- the modal shows the
   * full sentence via seatsSummary below). */
  seatsShortLabel(seats: SeatAvailability): string {
    if (seats.status === 'open') return `${seats.seatsLeft} left`;
    if (seats.status === 'waitlist') return 'Waitlist';
    return 'Full';
  }

  seatsSummary(seats: SeatAvailability): string {
    if (seats.status === 'open') {
      return `${seats.seatsLeft} of ${seats.capacity} seats open`;
    }
    if (seats.status === 'waitlist') {
      return `Full — waitlist open (${seats.waitlistCount} waiting)`;
    }
    return 'Full — no waitlist available';
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
    afterNextRender(() => this._animateIn(), { injector: this.injector });
    if (course.id) this._loadRealCourseState(course.id);
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
      const { inviteCode } = await this.groups.createGroup(courseCode);
      this.justCreatedInviteCode.set(inviteCode);
      this.groupStatus.set(await this.groups.findMyGroup(courseCode));
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
      await this.groups.joinGroup(code);
      this.joinCodeInput.set('');
      this.groupStatus.set(await this.groups.findMyGroup(courseCode));
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
