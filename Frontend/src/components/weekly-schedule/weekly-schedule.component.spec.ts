import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Course } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import {
  CourseEnrollmentService, CourseFullError, SeatPoolInfo, UNCLAIMED_SEAT_POOL, courseFullMessage, seatStatusFrom,
} from '../../services/course-enrollment.service';
import { CourseGroupService } from '../../services/course-group.service';
import { CourseRatingService } from '../../services/course-rating.service';
import { StudentProfileService } from '../../services/student-profile.service';
import { SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { WEEKDAY_CODES } from '../../utils/dummy-schedule.util';
import { WeeklyScheduleComponent } from './weekly-schedule.component';

// jsdom has no matchMedia; the modal's open animation consults it for
// prefers-reduced-motion (see animations/reduced-motion.ts). Without this
// stub every test that renders the open modal logs a caught TypeError.
if (typeof window !== 'undefined' && !window.matchMedia) {
  vi.stubGlobal('matchMedia', () => ({
    matches: false, media: '', onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
  }));
}

function makeCourse(id: string): Course {
  return { id, name: `${id} course`, description: '', prerequisites: [] };
}

/** A promise whose settlement the test controls -- lets a test hold one
 * course's seat-pool response open while a later course's resolves first,
 * which is the exact race the modal has to survive. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Lets queued microtasks (a just-resolved deferred's .then chain) run. */
const flushMicrotasks = () => new Promise<void>((r) => setTimeout(r, 0));

/** Mirrors the real CourseEnrollmentService's live store: a `pools`
 * signal every read lands in, plus `livePool`/`isFull` over it. Reads
 * resolve from `seatPools` (keyed by course code, same shape the real
 * getSeatPool/getSeatPools return) one microtask later -- async, like the
 * real network, so a test can observe "before the pool has loaded".
 * `push()` stands in for a Supabase Realtime change event: it updates the
 * store with no read involved. */
function fakeEnrollment(seatPools: Record<string, SeatPoolInfo>) {
  const pools = signal<Map<string, SeatPoolInfo>>(new Map());
  const merge = (entries: [string, SeatPoolInfo][]) =>
    pools.update((m) => {
      const next = new Map(m);
      for (const [code, pool] of entries) next.set(code, pool);
      return next;
    });
  const getSeatPools = vi.fn(async (codes: string[]) => {
    await Promise.resolve();
    const map = new Map<string, SeatPoolInfo>();
    for (const code of codes) map.set(code, seatPools[code] ?? { ...UNCLAIMED_SEAT_POOL });
    merge([...map.entries()]);
    return map;
  });
  const getSeatPool = vi.fn(async (code: string) => {
    await Promise.resolve();
    const pool = seatPools[code] ?? { ...UNCLAIMED_SEAT_POOL };
    merge([[code, pool]]);
    return pool;
  });
  const apply = vi.fn(async (code: string) => ({ status: 'enrolled' as const, code }));
  return {
    service: {
      pools: pools.asReadonly(),
      livePool: (code: string) => pools().get(code) ?? null,
      isFull: (code: string) => {
        const pool = pools().get(code);
        return !!pool && !seatStatusFrom(pool).seatAvailable;
      },
      getSeatPools,
      getSeatPool,
      getMyEnrollment: vi.fn().mockResolvedValue(null),
      apply,
      drop: vi.fn().mockResolvedValue(undefined),
    },
    merge,
    push: (code: string, pool: SeatPoolInfo) => merge([[code, pool]]),
    getSeatPools,
    getSeatPool,
    apply,
  };
}

function providersFor(enrollment: object, extra: { session?: () => unknown; toast?: { show: unknown } } = {}) {
  return [
    provideRouter([]),
    { provide: BackendService, useValue: { courseGraph: vi.fn().mockResolvedValue([]) } },
    { provide: CourseEnrollmentService, useValue: enrollment },
    { provide: CourseGroupService, useValue: { findMyGroup: vi.fn().mockResolvedValue(null) } },
    { provide: StudentProfileService, useValue: { getClassmateLinkedins: vi.fn().mockResolvedValue([]) } },
    { provide: SupabaseService, useValue: { session: extra.session ?? (() => null) } },
    { provide: ToastService, useValue: extra.toast ?? { show: vi.fn() } },
    { provide: CourseRatingService, useValue: { getSummaries: vi.fn().mockResolvedValue(new Map()) } },
  ];
}

/** Builds the component with every dependency faked and a scriptable
 * course_seat_pools. */
function setup(seatPools: Record<string, SeatPoolInfo>, extra: Parameters<typeof providersFor>[1] = {}) {
  const fake = fakeEnrollment(seatPools);
  TestBed.configureTestingModule({ providers: providersFor(fake.service, extra) });
  const fixture = TestBed.createComponent(WeeklyScheduleComponent);
  return { fixture, component: fixture.componentInstance, ...fake };
}

/** Like setup() above, but every per-course fetch the modal makes on open
 * (getSeatPool, getSummaries) returns a deferred the test settles by hand. */
function setupDeferred() {
  const fake = fakeEnrollment({});
  // The grid's own batched read never answers here, so the only way a
  // course's pool can reach the store is through the modal's per-course
  // read below -- which is what these tests are about.
  fake.service.getSeatPools = vi.fn(() => new Promise<Map<string, SeatPoolInfo>>(() => {}));
  const seatPoolRequests = new Map<string, ReturnType<typeof deferred<SeatPoolInfo>>>();
  fake.service.getSeatPool = vi.fn((code: string) => {
    const d = deferred<SeatPoolInfo>();
    seatPoolRequests.set(code, d);
    return d.promise.then((pool) => {
      fake.merge([[code, pool]]);
      return pool;
    });
  });
  const summaryRequests = new Map<string, ReturnType<typeof deferred<Map<string, unknown>>>>();
  const getSummaries = vi.fn((codes: string[]) => {
    const d = deferred<Map<string, unknown>>();
    summaryRequests.set(codes[0], d);
    return d.promise;
  });

  TestBed.configureTestingModule({
    providers: [
      ...providersFor(fake.service).filter((p) => !('provide' in p && p.provide === CourseRatingService)),
      { provide: CourseRatingService, useValue: { getSummaries } },
    ],
  });

  const fixture = TestBed.createComponent(WeeklyScheduleComponent);
  return { fixture, component: fixture.componentInstance, seatPoolRequests, summaryRequests };
}

describe('WeeklyScheduleComponent modal -- stale responses from a previously opened course', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it('never shows course A’s seat pool in course B’s modal, even when A’s response lands after B was opened (the reported open-close-open race)', async () => {
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
    // (It lands in the live store under A's own key, which is the point:
    // the modal reads the OPEN course's key, so there is nothing to
    // misfile.)
    seatPoolRequests.get('CMPSC 315')!.resolve({ capacity: 70, seatsTaken: 70 });
    await flushMicrotasks();
    expect(component.seatPool()).toBeNull();

    // B's own response is the one that gets through.
    seatPoolRequests.get('CMPSC 132')!.resolve({ capacity: 40, seatsTaken: 10 });
    await flushMicrotasks();
    expect(component.seatPool()).toEqual({ capacity: 40, seatsTaken: 10 });
  });

  it('shows nothing for a response that resolves after the modal was closed entirely', async () => {
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
  it('a late getSeatPools() response for a course list that has since been replaced cannot affect the courses now on the grid', async () => {
    const fake = fakeEnrollment({});
    const requests: ReturnType<typeof deferred<Map<string, SeatPoolInfo>>>[] = [];
    fake.service.getSeatPools = vi.fn((_codes: string[]) => {
      const d = deferred<Map<string, SeatPoolInfo>>();
      requests.push(d);
      return d.promise.then((map) => {
        fake.merge([...map.entries()]);
        return map;
      });
    });
    TestBed.configureTestingModule({ providers: providersFor(fake.service) });
    const fixture = TestBed.createComponent(WeeklyScheduleComponent);
    const component = fixture.componentInstance;

    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 315')]);
    fixture.detectChanges();
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 132')]);
    fixture.detectChanges();
    expect(requests).toHaveLength(2);

    // The FIRST list's response arrives last. It lands under its own
    // course's key; the grid only reads the codes it currently shows.
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
    await flushMicrotasks();

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
    await flushMicrotasks();

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
    await flushMicrotasks();

    const blocks = allBlocks(component);
    const full = blocks.find((b) => b.course.id === 'CMPSC 315')!;
    const open = blocks.find((b) => b.course.id === 'CMPSC 132')!;

    expect(full.seats.status).toBe('full');
    expect(open.seats.status).toBe('open');
    expect(open.seats.seatsLeft).toBe(30);
  });
});

/** Seat counts are live: a change pushed into the store (what a Supabase
 * Realtime event does in production) moves the grid block, the open
 * modal's status, and the Apply button together, with no refetch. */
describe('WeeklyScheduleComponent -- live seat counts', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it('a pushed change flips a block from open to Full and back, with no new reads', async () => {
    const { fixture, component, push, getSeatPools, getSeatPool } = setup({ 'STAT 318': { capacity: 50, seatsTaken: 49 } });
    fixture.componentRef.setInput('courses', [makeCourse('STAT 318')]);
    fixture.detectChanges();
    await fixture.whenStable();
    await flushMicrotasks();
    const readsBefore = getSeatPools.mock.calls.length + getSeatPool.mock.calls.length;

    let block = allBlocks(component).find((b) => b.course.id === 'STAT 318')!;
    expect(component.seatsShortLabel(block.seats)).toBe('1 left');

    push('STAT 318', { capacity: 50, seatsTaken: 50 }); // someone else took the last seat
    block = allBlocks(component).find((b) => b.course.id === 'STAT 318')!;
    expect(component.seatsShortLabel(block.seats)).toBe('Full');

    push('STAT 318', { capacity: 50, seatsTaken: 49 }); // ...and dropped it
    block = allBlocks(component).find((b) => b.course.id === 'STAT 318')!;
    expect(component.seatsShortLabel(block.seats)).toBe('1 left');

    expect(getSeatPools.mock.calls.length + getSeatPool.mock.calls.length).toBe(readsBefore);
  });

  it('the open modal follows the same change: status, count, and the Apply button’s Full label', async () => {
    const { fixture, component, push } = setup({ 'STAT 318': { capacity: 50, seatsTaken: 49 } });
    fixture.componentRef.setInput('courses', [makeCourse('STAT 318')]);
    fixture.detectChanges();
    component.selectedCourseCode.set('STAT 318');
    fixture.detectChanges();
    await fixture.whenStable();
    await flushMicrotasks();

    expect(component.seatPool()).toEqual({ capacity: 50, seatsTaken: 49 });
    expect(component.openCourseIsFull()).toBe(false);
    expect(component.registrationStatusFor(component.seatPool()!).label).toBe('Open — 1 of 50 seats left');

    push('STAT 318', { capacity: 50, seatsTaken: 50 });
    expect(component.seatPool()).toEqual({ capacity: 50, seatsTaken: 50 });
    expect(component.openCourseIsFull()).toBe(true);
    expect(component.registrationStatusFor(component.seatPool()!).label).toBe('Full — 50 of 50 taken');
  });
});

/** A full course can't be registered. The modal says so and claims
 * nothing -- whether the live pool already shows Full, or the server
 * refuses because the last seat went between the read and the click. */
describe('WeeklyScheduleComponent.applyForSeat -- full courses are refused', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it('when the live pool is already Full: tells the student and never calls the RPC', async () => {
    const show = vi.fn();
    const { fixture, component, apply } = setup(
      { 'CMPSC 465': { capacity: 50, seatsTaken: 50 } },
      { session: () => ({ user: { id: 'u1' } }), toast: { show } },
    );
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 465')]);
    fixture.detectChanges();
    component.selectedCourseCode.set('CMPSC 465');
    fixture.detectChanges();
    await fixture.whenStable();
    await flushMicrotasks();
    expect(component.openCourseIsFull()).toBe(true);

    await component.applyForSeat('CMPSC 465');
    expect(apply).not.toHaveBeenCalled();
    expect(show).toHaveBeenCalledWith(courseFullMessage('CMPSC 465'), 'error');
    expect(component.myEnrollment()).toBeNull();
  });

  it("when the server refuses (CourseFullError): same message, nothing held", async () => {
    const show = vi.fn();
    const { fixture, component, apply } = setup(
      { 'CMPSC 465': { capacity: 50, seatsTaken: 49 } },
      { session: () => ({ user: { id: 'u1' } }), toast: { show } },
    );
    apply.mockRejectedValueOnce(new CourseFullError('CMPSC 465'));
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 465')]);
    fixture.detectChanges();
    component.selectedCourseCode.set('CMPSC 465');
    fixture.detectChanges();
    await fixture.whenStable();
    await flushMicrotasks();
    expect(component.openCourseIsFull()).toBe(false); // the screen still said 1 left

    await component.applyForSeat('CMPSC 465');
    expect(apply).toHaveBeenCalledWith('CMPSC 465');
    expect(show).toHaveBeenCalledWith(courseFullMessage('CMPSC 465'), 'error');
    expect(component.myEnrollment()).toBeNull();
    expect(component.applyBusy()).toBe(false);
  });

  it('an open seat is claimed and reported as held', async () => {
    const show = vi.fn();
    const { fixture, component, apply } = setup(
      { 'CMPSC 121': { capacity: 50, seatsTaken: 1 } },
      { session: () => ({ user: { id: 'u1' } }), toast: { show } },
    );
    fixture.componentRef.setInput('courses', [makeCourse('CMPSC 121')]);
    fixture.detectChanges();
    component.selectedCourseCode.set('CMPSC 121');
    fixture.detectChanges();
    await fixture.whenStable();
    await flushMicrotasks();

    await component.applyForSeat('CMPSC 121');
    expect(apply).toHaveBeenCalledWith('CMPSC 121');
    expect(component.myEnrollment()).toEqual(expect.objectContaining({ status: 'enrolled' }));
    expect(show).toHaveBeenCalledWith("You’re in — a seat is held for you.", 'success');
  });
});
