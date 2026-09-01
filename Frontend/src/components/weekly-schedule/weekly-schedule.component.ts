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
import { Course } from '../../models/course-plan.model';
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
})
export class WeeklyScheduleComponent {
  private readonly injector = inject(Injector);

  courses = input<Course[]>([]);
  scheduledCourseIds = input<string[]>([]);

  toggleScheduled = output<string>();

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
    return blocks;
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
    afterNextRender(() => this._animateIn(), { injector: this.injector });
  }

  async closeCourse() {
    await this._animateOut();
    this.selectedCourse.set(null);
  }

  onToggleScheduled(course: Course) {
    if (course.id) this.toggleScheduled.emit(course.id);
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
