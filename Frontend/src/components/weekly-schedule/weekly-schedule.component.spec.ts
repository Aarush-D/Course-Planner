import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Course } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import { CourseEnrollmentService, SeatPoolInfo, UNCLAIMED_SEAT_POOL } from '../../services/course-enrollment.service';
import { CourseGroupService } from '../../services/course-group.service';
import { CourseRatingService } from '../../services/course-rating.service';
import { StudentProfileService } from '../../services/student-profile.service';
import { SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { WEEKDAY_CODES } from '../../utils/dummy-schedule.util';
import { WeeklyScheduleComponent } from './weekly-schedule.component';

function makeCourse(id: string): Course {
  return { id, name: `${id} course`, description: '', prerequisites: [] };
}

/** Builds the component with every dependency faked, and a scriptable
 * course_seat_pools -- keyed by course code, same shape
 * CourseEnrollmentService.getSeatPool/getSeatPools themselves return. */
function setup(seatPools: Record<string, SeatPoolInfo>) {
  const getSeatPools = vi.fn(async (codes: string[]) => {
    const map = new Map<string, SeatPoolInfo>();
    for (const code of codes) map.set(code, seatPools[code] ?? { ...UNCLAIMED_SEAT_POOL });
    return map;
  });
  const getSeatPool = vi.fn(async (code: string) => seatPools[code] ?? { ...UNCLAIMED_SEAT_POOL });

  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      { provide: BackendService, useValue: { courseGraph: vi.fn().mockResolvedValue([]) } },
      {
        provide: CourseEnrollmentService,
        useValue: { getSeatPools, getSeatPool, getMyEnrollment: vi.fn().mockResolvedValue(null) },
      },
      { provide: CourseGroupService, useValue: { findMyGroup: vi.fn().mockResolvedValue(null) } },
      { provide: StudentProfileService, useValue: {} },
      { provide: SupabaseService, useValue: { session: () => null } },
      { provide: ToastService, useValue: { show: vi.fn() } },
      { provide: CourseRatingService, useValue: { getSummaries: vi.fn().mockResolvedValue(new Map()) } },
    ],
  });

  const fixture = TestBed.createComponent(WeeklyScheduleComponent);
  return { fixture, component: fixture.componentInstance, getSeatPools, getSeatPool };
}

/** Every block for every day, since a course's dummy meeting slot lands on
 * an M/W/F or T/R pattern deterministically hashed from its code -- tests
 * here care about seat status, not which day it landed on. */
function allBlocks(component: WeeklyScheduleComponent) {
  return WEEKDAY_CODES.flatMap((day) => component.blocksForDay(day));
}

describe('WeeklyScheduleComponent seat availability -- block vs. modal agreement', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it('never shows a block as Full for a course whose real seat pool is wide open (the reported CMPSC 315 bug)', async () => {
    // CMPSC 315 has no course_seat_pools row -- nobody has applied yet --
    // so the real pool is capacity 50, 0 taken, wide open. Before this
    // fix, the grid block read a completely separate client-side hash
    // (dummySeatAvailabilityFor('CMPSC 315')) for its dot/label, which for
    // this exact course code happened to land on 'full' -- while the modal
    // opened from that very block, built on the real pool, said "Open --
    // 50 of 50 seats left". Same course, contradicting statuses.
    const { fixture, component } = setup({});
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315')]);
    fixture.detectChanges();
    await fixture.whenStable();

    const block = allBlocks(component).find((b) => b.course.id === 'CMPSC 315');
    expect(block).toBeTruthy();
    expect(block!.seats.status).toBe('open');
    expect(block!.seats.seatsLeft).toBe(50);
    expect(component.seatsShortLabel(block!.seats)).toBe('50 left');

    // The exact same real pool is what the modal's Registration status
    // renders from (registrationStatusFor) -- same numbers, same
    // conclusion, by construction (both go through seatStatusFrom on the
    // same SeatPoolInfo).
    const modalStatus = component.registrationStatusFor({ capacity: 50, seatsTaken: 0 });
    expect(modalStatus.seatAvailable).toBe(true);
    expect(modalStatus.label).toBe('Open — 50 of 50 seats left');
  });

  it('shows a block as Full only when the real pool is actually at capacity, and the modal agrees', async () => {
    const { fixture, component } = setup({ 'CMPSC 315': { capacity: 70, seatsTaken: 70 } });
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315')]);
    fixture.detectChanges();
    await fixture.whenStable();

    const block = allBlocks(component).find((b) => b.course.id === 'CMPSC 315');
    expect(block!.seats.status).toBe('full');
    expect(block!.seats.seatsLeft).toBe(0);
    expect(component.seatsShortLabel(block!.seats)).toBe('Full');

    const modalStatus = component.registrationStatusFor({ capacity: 70, seatsTaken: 70 });
    expect(modalStatus.seatAvailable).toBe(false);
    expect(modalStatus.label).toBe('Full — 70 of 70 taken');
  });

  it('never renders a course as Full before its real seat pool has finished loading', () => {
    // getSeatPools() is async -- the very first synchronous read of
    // blocksForDay(), before that promise resolves, must not default to
    // "full" (which is exactly what the old per-block dummy hash could
    // produce with no loading state at all).
    const { fixture, component } = setup({ 'CMPSC 315': { capacity: 70, seatsTaken: 70 } });
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315')]);
    fixture.detectChanges(); // effects scheduled, but the getSeatPools() promise hasn't settled yet

    const block = allBlocks(component).find((b) => b.course.id === 'CMPSC 315');
    expect(block!.seats.status).toBe('open');
  });

  it('keeps every course in a multi-course schedule internally consistent, mixed open and full', async () => {
    const { fixture, component } = setup({
      'CMPSC 315': { capacity: 70, seatsTaken: 70 }, // full
      'CMPSC 132': { capacity: 40, seatsTaken: 10 }, // open
    });
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315'), makeCourse('CMPSC 132')]);
    fixture.detectChanges();
    await fixture.whenStable();

    const blocks = allBlocks(component);
    const full = blocks.find((b) => b.course.id === 'CMPSC 315')!;
    const open = blocks.find((b) => b.course.id === 'CMPSC 132')!;

    expect(full.seats.status).toBe('full');
    expect(open.seats.status).toBe('open');
    expect(open.seats.seatsLeft).toBe(30);
  });
});
