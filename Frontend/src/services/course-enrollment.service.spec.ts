import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';
import {
  CourseEnrollmentService, SeatPoolInfo, UNCLAIMED_SEAT_POOL, seatStatusFrom,
} from './course-enrollment.service';
import { SupabaseService } from './supabase.service';

/** Bare-bones fake of the one Supabase table this service touches --
 * course_seat_pools -- supporting exactly the two query shapes
 * getSeatPool()/getSeatPools() issue: `.eq(...).maybeSingle()` and
 * `.in(...)`. Rows are keyed by course_code, same as the real table's
 * primary key. */
function fakeSupabase(rows: Record<string, { capacity: number; seats_taken: number }>) {
  return {
    client: {
      from: (table: string) => {
        if (table !== 'course_seat_pools') throw new Error(`unexpected table: ${table}`);
        return {
          select: (_cols: string) => ({
            eq: (_col: string, code: string) => ({
              maybeSingle: async () => ({
                data: rows[code] ? { capacity: rows[code].capacity, seats_taken: rows[code].seats_taken } : null,
                error: null,
              }),
            }),
            in: (_col: string, codes: string[]) => Promise.resolve({
              data: codes
                .filter((c) => rows[c])
                .map((c) => ({ course_code: c, capacity: rows[c].capacity, seats_taken: rows[c].seats_taken })),
              error: null,
            }),
          }),
        };
      },
    },
  };
}

function setup(rows: Record<string, { capacity: number; seats_taken: number }> = {}) {
  TestBed.configureTestingModule({
    providers: [{ provide: SupabaseService, useValue: fakeSupabase(rows) }],
  });
  return TestBed.inject(CourseEnrollmentService);
}

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
    const service = setup();
    const pool = await service.getSeatPool('CMPSC 315');
    expect(pool).toEqual(UNCLAIMED_SEAT_POOL);
  });

  it('getSeatPools fills in UNCLAIMED_SEAT_POOL for every requested course with no row, and real numbers for the rest', async () => {
    const service = setup({ 'CMPSC 132': { capacity: 40, seats_taken: 40 } });
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
    const service = setup(); // CMPSC 315 has no row -- nobody has applied
    const single: SeatPoolInfo = await service.getSeatPool('CMPSC 315');
    const batched = (await service.getSeatPools(['CMPSC 315'])).get('CMPSC 315')!;
    expect(single).toEqual(batched);
    expect(seatStatusFrom(single).seatAvailable).toBe(true);
    expect(seatStatusFrom(single).seatsLeft).toBe(50);
  });

  it('checkAvailability uses the same seatStatusFrom math as everything else', async () => {
    const service = setup({ 'CMPSC 315': { capacity: 70, seats_taken: 70 } });
    const { seatAvailable } = await service.checkAvailability('CMPSC 315');
    expect(seatAvailable).toBe(false);
  });
});
