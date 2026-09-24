import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';
import {
  CourseEnrollmentService, CourseFullError, SeatPoolInfo, UNCLAIMED_SEAT_POOL, courseFullMessage, seatStatusFrom,
} from './course-enrollment.service';
import { SupabaseService } from './supabase.service';

type Row = { capacity: number; seats_taken: number };
type ChangeHandler = (payload: unknown) => void;
type StatusHandler = (status: string) => void;

/** Bare-bones fake of the Supabase surface this service touches:
 *  - the course_seat_pools table, for exactly the two query shapes
 *    getSeatPool()/getSeatPools() issue (`.eq(...).maybeSingle()` and
 *    `.in(...)`), rows keyed by course_code like the real primary key;
 *  - `rpc(...)`, scriptable per call via `rpcResult`;
 *  - one Realtime channel, whose change handler and subscribe-status
 *    callback the test can fire by hand to stand in for the server. */
function fakeSupabase(rows: Record<string, Row>) {
  const calls = { single: 0, batched: 0 };
  const live: { onChange: ChangeHandler | null; onStatus: StatusHandler | null; channels: number } = {
    onChange: null, onStatus: null, channels: 0,
  };
  let rpcResult: { data: unknown; error: unknown } = { data: { status: 'enrolled', seat_position: null }, error: null };
  const channel = {
    on: (_event: string, _filter: unknown, cb: ChangeHandler) => {
      live.onChange = cb;
      return channel;
    },
    subscribe: (cb: StatusHandler) => {
      live.onStatus = cb;
      return channel;
    },
  };
  const client = {
    from: (table: string) => {
      if (table !== 'course_seat_pools') throw new Error(`unexpected table: ${table}`);
      return {
        select: (_cols: string) => ({
          eq: (_col: string, code: string) => ({
            maybeSingle: async () => {
              calls.single++;
              return {
                data: rows[code] ? { capacity: rows[code].capacity, seats_taken: rows[code].seats_taken } : null,
                error: null,
              };
            },
          }),
          in: async (_col: string, codes: string[]) => {
            calls.batched++;
            return {
              data: codes
                .filter((c) => rows[c])
                .map((c) => ({ course_code: c, capacity: rows[c].capacity, seats_taken: rows[c].seats_taken })),
              error: null,
            };
          },
        }),
      };
    },
    rpc: (_name: string, _args: unknown) => ({ single: async () => rpcResult }),
    channel: (_name: string) => {
      live.channels++;
      return channel;
    },
  };
  return {
    supabase: { client },
    calls,
    live,
    setRpcResult: (r: { data: unknown; error: unknown }) => { rpcResult = r; },
  };
}

function setup(rows: Record<string, Row> = {}) {
  const fake = fakeSupabase(rows);
  TestBed.configureTestingModule({
    providers: [{ provide: SupabaseService, useValue: fake.supabase }],
  });
  return { ...fake, service: TestBed.inject(CourseEnrollmentService) };
}

const flush = () => new Promise<void>((r) => setTimeout(r, 0));

describe('seatStatusFrom', () => {
  it('reports available with the full capacity open when nobody has taken a seat', () => {
    expect(seatStatusFrom({ capacity: 50, seatsTaken: 0 })).toEqual({ seatAvailable: true, seatsLeft: 50 });
  });

  it('reports unavailable, zero left, exactly at capacity', () => {
    expect(seatStatusFrom({ capacity: 70, seatsTaken: 70 })).toEqual({ seatAvailable: false, seatsLeft: 0 });
  });

  it('never reports a negative seatsLeft if seatsTaken somehow exceeds capacity', () => {
    expect(seatStatusFrom({ capacity: 50, seatsTaken: 55 })).toEqual({ seatAvailable: false, seatsLeft: 0 });
  });

  it('a course can never simultaneously read as full AND have seats left, for any pool', () => {
    // Property check across a spread of pools, not just the two boundary
    // cases above -- the exact contradiction this whole bug was about:
    // "full" and "seats left > 0" must be mutually exclusive, always.
    for (let capacity = 1; capacity <= 100; capacity += 7) {
      for (let seatsTaken = 0; seatsTaken <= capacity + 10; seatsTaken += 5) {
        const { seatAvailable, seatsLeft } = seatStatusFrom({ capacity, seatsTaken });
        if (!seatAvailable) {
          expect(seatsLeft).toBe(0);
        } else {
          expect(seatsLeft).toBeGreaterThan(0);
        }
      }
    }
  });
});

describe('CourseEnrollmentService.getSeatPool / getSeatPools', () => {
  it('returns UNCLAIMED_SEAT_POOL for a course nobody has ever applied to', async () => {
    const { service } = setup();
    const pool = await service.getSeatPool('CMPSC 315');
    expect(pool).toEqual(UNCLAIMED_SEAT_POOL);
  });

  it('getSeatPools fills in UNCLAIMED_SEAT_POOL for every requested course with no row, and real numbers for the rest', async () => {
    const { service } = setup({ 'CMPSC 132': { capacity: 40, seats_taken: 40 } });
    const map = await service.getSeatPools(['CMPSC 315', 'CMPSC 132']);
    expect(map.get('CMPSC 315')).toEqual(UNCLAIMED_SEAT_POOL);
    expect(map.get('CMPSC 132')).toEqual({ capacity: 40, seatsTaken: 40 });
  });

  it('getSeatPool and getSeatPools agree on the exact same numbers for the exact same course -- the concrete CMPSC 315 regression', async () => {
    // Reproduces the reported bug's data shape directly: a course with no
    // course_seat_pools row yet. Before this fix, the Weekly Schedule grid
    // block for a course like this read an entirely different, client-side
    // hash (dummySeatAvailabilityFor) instead of this real pool, and could
    // land on "full" for a course this service reports as wide open. Now
    // there is only one source (this table, through this one service), so
    // a single-fetch call and a batched call for the same course cannot
    // disagree.
    const { service } = setup(); // CMPSC 315 has no row -- nobody has applied
    const single: SeatPoolInfo = await service.getSeatPool('CMPSC 315');
    const batched = (await service.getSeatPools(['CMPSC 315'])).get('CMPSC 315')!;
    expect(single).toEqual(batched);
    expect(seatStatusFrom(single).seatAvailable).toBe(true);
    expect(seatStatusFrom(single).seatsLeft).toBe(50);
  });

  it('checkAvailability uses the same seatStatusFrom math as everything else', async () => {
    const { service } = setup({ 'CMPSC 315': { capacity: 70, seats_taken: 70 } });
    const { seatAvailable, seatsLeft } = await service.checkAvailability('CMPSC 315');
    expect(seatAvailable).toBe(false);
    expect(seatsLeft).toBe(0);
  });

  it('findOpenAlternative returns the first option with a seat, in the order given, and null when all are full', async () => {
    const { service } = setup({
      'MATH 140': { capacity: 10, seats_taken: 10 },
      'MATH 141': { capacity: 10, seats_taken: 10 },
      'MATH 110': { capacity: 10, seats_taken: 3 },
    });
    expect(await service.findOpenAlternative(['MATH 140', 'MATH 141', 'MATH 110'])).toBe('MATH 110');
    expect(await service.findOpenAlternative(['MATH 140', 'MATH 141'])).toBeNull();
    expect(await service.findOpenAlternative([])).toBeNull();
  });
});

/** The live store: every read lands in `pools`, and a Realtime change
 * event for a course updates that entry with nothing else asked to
 * refetch. This is what lets every open Weekly Schedule show a course
 * filling up the moment someone else takes the last seat. */
describe('CourseEnrollmentService live seat pools', () => {
  it('starts empty, and a read lands in pools (unknown courses are not "full")', async () => {
    const { service } = setup({ 'CMPSC 465': { capacity: 50, seats_taken: 50 } });
    expect(service.pools().size).toBe(0);
    expect(service.livePool('CMPSC 465')).toBeNull();
    expect(service.isFull('CMPSC 465')).toBe(false); // never Full before a real row has been read

    await service.getSeatPool('CMPSC 465');
    expect(service.livePool('CMPSC 465')).toEqual({ capacity: 50, seatsTaken: 50 });
    expect(service.isFull('CMPSC 465')).toBe(true);
  });

  it('opens exactly one Realtime channel, lazily, no matter how many reads happen', async () => {
    const { service, live } = setup();
    expect(live.channels).toBe(0);
    await service.getSeatPool('A 1');
    await service.getSeatPools(['B 2', 'C 3']);
    await service.getSeatPool('A 1');
    expect(live.channels).toBe(1);
  });

  it('an UPDATE event moves the count without any refetch', async () => {
    const { service, live, calls } = setup({ 'STAT 318': { capacity: 50, seats_taken: 49 } });
    await service.getSeatPool('STAT 318');
    expect(service.isFull('STAT 318')).toBe(false);
    const readsBefore = calls.single + calls.batched;

    // Another student just took the last seat -- this is what the server pushes.
    live.onChange!({
      eventType: 'UPDATE',
      new: { course_code: 'STAT 318', capacity: 50, seats_taken: 50 },
      old: { course_code: 'STAT 318' },
    });
    expect(service.livePool('STAT 318')).toEqual({ capacity: 50, seatsTaken: 50 });
    expect(service.isFull('STAT 318')).toBe(true);
    expect(calls.single + calls.batched).toBe(readsBefore);

    // ...and dropped it again.
    live.onChange!({
      eventType: 'UPDATE',
      new: { course_code: 'STAT 318', capacity: 50, seats_taken: 49 },
      old: { course_code: 'STAT 318' },
    });
    expect(service.isFull('STAT 318')).toBe(false);
  });

  it('an INSERT event for a course never read before is kept too', async () => {
    const { service, live } = setup();
    await service.getSeatPool('A 1'); // opens the channel
    live.onChange!({ eventType: 'INSERT', new: { course_code: 'NEW 100', capacity: 50, seats_taken: 1 }, old: {} });
    expect(service.livePool('NEW 100')).toEqual({ capacity: 50, seatsTaken: 1 });
  });

  it('a DELETE event resets the course to the unclaimed pool', async () => {
    const { service, live } = setup({ 'STAT 318': { capacity: 50, seats_taken: 50 } });
    await service.getSeatPool('STAT 318');
    expect(service.isFull('STAT 318')).toBe(true);
    live.onChange!({ eventType: 'DELETE', new: {}, old: { course_code: 'STAT 318' } });
    expect(service.livePool('STAT 318')).toEqual(UNCLAIMED_SEAT_POOL);
  });

  it('re-reads every known pool when the channel (re)connects, to cover changes missed while disconnected', async () => {
    const { service, live, calls } = setup({ 'A 1': { capacity: 50, seats_taken: 1 } });
    await service.getSeatPool('A 1');
    await service.getSeatPool('B 2');
    const batchedBefore = calls.batched;
    live.onStatus!('SUBSCRIBED');
    await flush();
    expect(calls.batched).toBe(batchedBefore + 1);
    // A status that is not a (re)connection is ignored.
    live.onStatus!('CHANNEL_ERROR');
    await flush();
    expect(calls.batched).toBe(batchedBefore + 1);
  });
});

/** A full course can't be registered. claim_course_seat (migration 0023)
 * raises 'course_full' instead of waitlisting; this is where that becomes
 * the typed error every surface branches on. */
describe('CourseEnrollmentService.apply', () => {
  it('resolves enrolled when the claim succeeds, and refreshes the pool', async () => {
    const { service, calls } = setup({ 'CMPSC 121': { capacity: 50, seats_taken: 2 } });
    const result = await service.apply('CMPSC 121');
    expect(result).toEqual({ status: 'enrolled' });
    await flush();
    expect(calls.single).toBe(1);
    expect(service.livePool('CMPSC 121')).toEqual({ capacity: 50, seatsTaken: 2 });
  });

  it("throws CourseFullError, with the student-facing message, on the server's course_full refusal", async () => {
    const { service, setRpcResult } = setup({ 'CMPSC 465': { capacity: 50, seats_taken: 50 } });
    setRpcResult({ data: null, error: { code: 'P0001', message: 'course_full', hint: 'CMPSC 465 is full' } });
    const p = service.apply('CMPSC 465');
    await expect(p).rejects.toBeInstanceOf(CourseFullError);
    await expect(p).rejects.toMatchObject({ courseCode: 'CMPSC 465', message: courseFullMessage('CMPSC 465') });
    expect(courseFullMessage('CMPSC 465')).toBe('CMPSC 465 is full — its seats can’t be registered.');
  });

  it('refreshes the pool after a refusal too, so the screen catches up to "full" immediately', async () => {
    const { service, setRpcResult } = setup({ 'CMPSC 465': { capacity: 50, seats_taken: 50 } });
    setRpcResult({ data: null, error: { message: 'course_full' } });
    await service.apply('CMPSC 465').catch(() => {});
    await flush();
    expect(service.isFull('CMPSC 465')).toBe(true);
  });

  it('passes any other error through untouched -- a network failure is NOT "full"', async () => {
    const { service, setRpcResult } = setup();
    const boom = { message: 'FetchError: failed', code: undefined };
    setRpcResult({ data: null, error: boom });
    const p = service.apply('CMPSC 121');
    await expect(p).rejects.toBe(boom);
    await expect(p).rejects.not.toBeInstanceOf(CourseFullError);
  });

  it('drop refreshes the pool as well', async () => {
    const { service, setRpcResult } = setup({ 'CMPSC 121': { capacity: 50, seats_taken: 1 } });
    setRpcResult({ data: null, error: null });
    await service.drop('CMPSC 121');
    await flush();
    expect(service.livePool('CMPSC 121')).toEqual({ capacity: 50, seatsTaken: 1 });
  });
});
