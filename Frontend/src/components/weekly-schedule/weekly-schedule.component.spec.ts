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

/** A promise whose settlement the test controls -- lets a test hold one
 * course's seat-pool response open while a later course's resolves first,
 * which is the exact race the modal's generation token exists to survive. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Like setup() above, but every per-course fetch the modal makes on open
 * (getSeatPool, getSummaries) returns a deferred the test settles by hand. */
function setupDeferred() {
  const seatPoolRequests = new Map<string, ReturnType<typeof deferred<SeatPoolInfo>>>();
  const getSeatPool = vi.fn((code: string) => {
    const d = deferred<SeatPoolInfo>();
    seatPoolRequests.set(code, d);
    return d.promise;
  });
  const summaryRequests = new Map<string, ReturnType<typeof deferred<Map<string, unknown>>>>();
  const getSummaries = vi.fn((codes: string[]) => {
    const d = deferred<Map<string, unknown>>();
    summaryRequests.set(codes[0], d);
    return d.promise;
  });
  const getSeatPools = vi.fn(async () => new Map<string, SeatPoolInfo>());

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
      { provide: CourseRatingService, useValue: { getSummaries } },
    ],
  });

  const fixture = TestBed.createComponent(WeeklyScheduleComponent);
  return { fixture, component: fixture.componentInstance, seatPoolRequests, summaryRequests, getSeatPool };
}

/** Lets queued microtasks (a just-resolved deferred's .then chain) run. */
const flushMicrotasks = () => new Promise<void>((r) => setTimeout(r, 0));

describe('WeeklyScheduleComponent modal -- stale responses from a previously opened course', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it('drops course A’s seat-pool response when it resolves after course B was opened (the reported open-close-open race)', async () => {
    const { fixture, component, seatPoolRequests } = setupDeferred();
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315'), makeCourse('CMPSC 132')]);
    fixture.detectChanges();

    // Open A, then close it, then open B -- all before A's fetch settles.
    component.selectedCourseCode.set('CMPSC 315');
    fixture.detectChanges();
    expect(component.selectedCourse()?.id).toBe('CMPSC 315');
    component.selectedCourseCode.set(null);
    fixture.detectChanges();
    expect(component.selectedCourse()).toBeNull();
    component.selectedCourseCode.set('CMPSC 132');
    fixture.detectChanges();
    expect(component.selectedCourse()?.id).toBe('CMPSC 132');

    // A's slow response lands now -- it must NOT show up in B's modal.
    seatPoolRequests.get('CMPSC 315')!.resolve({ capacity: 70, seatsTaken: 70 });
    await flushMicrotasks();
    expect(component.seatPool()).toBeNull();

    // B's own response is the one that gets through.
    seatPoolRequests.get('CMPSC 132')!.resolve({ capacity: 40, seatsTaken: 10 });
    await flushMicrotasks();
    expect(component.seatPool()).toEqual({ capacity: 40, seatsTaken: 10 });
  });

  it('drops a response that resolves after the modal was closed entirely', async () => {
    const { fixture, component, seatPoolRequests } = setupDeferred();
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315')]);
    fixture.detectChanges();

    component.selectedCourseCode.set('CMPSC 315');
    fixture.detectChanges();
    component.selectedCourseCode.set(null);
    fixture.detectChanges();

    seatPoolRequests.get('CMPSC 315')!.resolve({ capacity: 70, seatsTaken: 70 });
    await flushMicrotasks();
    expect(component.seatPool()).toBeNull();
  });

  it('applies the same guard to the rating summary', async () => {
    const { fixture, component, summaryRequests } = setupDeferred();
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315'), makeCourse('CMPSC 132')]);
    fixture.detectChanges();

    component.selectedCourseCode.set('CMPSC 315');
    fixture.detectChanges();
    component.selectedCourseCode.set('CMPSC 132');
    fixture.detectChanges();

    const summaryA = { course_code: 'CMPSC 315', average_rating: 1, rating_count: 9 };
    const summaryB = { course_code: 'CMPSC 132', average_rating: 5, rating_count: 3 };
    summaryRequests.get('CMPSC 315')!.resolve(new Map([['CMPSC 315', summaryA]]));
    await flushMicrotasks();
    expect(component.courseRatingSummary()).toBeNull();

    summaryRequests.get('CMPSC 132')!.resolve(new Map([['CMPSC 132', summaryB]]));
    await flushMicrotasks();
    expect(component.courseRatingSummary()).toEqual(summaryB);
  });
});

describe('WeeklyScheduleComponent grid -- stale batched seat-pool responses', () => {
  it('ignores a getSeatPools() response for a course list that has since been replaced', async () => {
    const requests: ReturnType<typeof deferred<Map<string, SeatPoolInfo>>>[] = [];
    const getSeatPools = vi.fn(() => {
      const d = deferred<Map<string, SeatPoolInfo>>();
      requests.push(d);
      return d.promise;
    });
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: BackendService, useValue: { courseGraph: vi.fn().mockResolvedValue([]) } },
        { provide: CourseEnrollmentService, useValue: { getSeatPools, getSeatPool: vi.fn(), getMyEnrollment: vi.fn() } },
        { provide: CourseGroupService, useValue: {} },
        { provide: StudentProfileService, useValue: {} },
        { provide: SupabaseService, useValue: { session: () => null } },
        { provide: ToastService, useValue: { show: vi.fn() } },
        { provide: CourseRatingService, useValue: { getSummaries: vi.fn().mockResolvedValue(new Map()) } },
      ],
    });
    const fixture = TestBed.createComponent(WeeklyScheduleComponent);
    const component = fixture.componentInstance;

    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315')]);
    fixture.detectChanges();
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 132')]);
    fixture.detectChanges();
    expect(requests).toHaveLength(2);

    // The FIRST list's response arrives last: it must not overwrite the
    // second list's (still-pending) pools with a map for a course that is
    // no longer even on the grid.
    requests[1].resolve(new Map([['CMPSC 132', { capacity: 40, seatsTaken: 10 }]]));
    await flushMicrotasks();
    requests[0].resolve(new Map([['CMPSC 315', { capacity: 70, seatsTaken: 70 }]]));
    await flushMicrotasks();

    const block = allBlocks(component).find((b) => b.course.id === 'CMPSC 132')!;
    expect(block.seats.status).toBe('open');
    expect(block.seats.seatsLeft).toBe(30);
  });
});

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
