import { Injectable, inject } from '@angular/core';
import { SupabaseService } from './supabase.service';

export type EnrollmentStatus = 'enrolled' | 'waitlisted';

export interface SeatPoolInfo {
  capacity: number;
  seatsTaken: number;
}

/** What an unclaimed course's pool looks like -- the course_seat_pools row
 * only starts existing once someone actually applies (claim_course_seat's
 * own upsert), so "no row yet" and "full capacity, zero taken" have to be
 * treated as literally the same state everywhere a pool is read. Shared
 * (not three separate `?? 50`/`?? 0` literals) specifically so
 * getSeatPool, getSeatPools, and any caller's own "not loaded yet"
 * fallback can never drift into disagreeing about what "no data" means. */
export const UNCLAIMED_SEAT_POOL: SeatPoolInfo = { capacity: 50, seatsTaken: 0 };

/** The one comparison that decides "open" vs. "full" -- shared by every
 * surface that shows seat availability for a course (Weekly Schedule's
 * grid-block dot/label AND its modal's "Registration status" section) so
 * they read the same SeatPoolInfo through the same math and can never
 * independently arrive at contradicting answers (e.g. one side saying
 * "full" while the other reports open seats for the very same pool). */
export function seatStatusFrom(pool: SeatPoolInfo): { seatAvailable: boolean; seatsLeft: number } {
  return {
    seatAvailable: pool.seatsTaken < pool.capacity,
    seatsLeft: Math.max(pool.capacity - pool.seatsTaken, 0),
  };
}

export interface MyEnrollment {
  status: EnrollmentStatus;
  /** Only meaningful when status === 'waitlisted' -- 1-based rank among
   * other waitlisted students for this course, oldest request first. */
  position: number | null;
}

/** Real, shared, race-safe seat accounting for a course -- replaces the
 * purely-cosmetic dummySeatAvailabilityFor() the Weekly Schedule used to
 * show (see dummy-schedule.util.ts): that was a hash of the course code,
 * computed client-side, never persisted, never contended. This talks to
 * course_seat_pools/course_enrollments (supabase/migrations, seat-pool
 * migration) and the claim_course_seat/drop_course_seat RPCs, which are
 * what actually make "only the first 50 of 200 applicants get in" true
 * under concurrent requests -- the decision is made atomically in
 * Postgres, not here.
 *
 * Requires a signed-in student account (same constraint as
 * StudentPlanService) -- there is no way to track a persistent, contended,
 * cross-session seat claim for an anonymous, no-account visitor. Callers
 * must check StudentSessionService's session state before offering Apply. */
@Injectable({ providedIn: 'root' })
export class CourseEnrollmentService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  /** Public, non-identifying aggregate counts -- safe to show before the
   * student has applied, or to a student who never applies at all.
   * Returns a not-yet-applied-for course as "full capacity, zero taken"
   * (the pool row only starts existing once someone actually applies --
   * see claim_course_seat's own upsert). */
  async getSeatPool(courseCode: string): Promise<SeatPoolInfo> {
    const { data, error } = await this.client
      .from('course_seat_pools')
      .select('capacity, seats_taken')
      .eq('course_code', courseCode)
      .maybeSingle();
    if (error) throw error;
    return data ? { capacity: data.capacity, seatsTaken: data.seats_taken } : { ...UNCLAIMED_SEAT_POOL };
  }

  /** Same pool, batched for a whole list of courses at once -- one round
   * trip, never N+1 (same pattern as CourseRatingService.getSummaries()).
   * This is what the Weekly Schedule grid uses for every block's dot/short
   * label: before this existed, those blocks fell back to
   * dummySeatAvailabilityFor's client-side hash instead of a real query,
   * which is exactly how a block could show "Full" for a course the real
   * pool (read moments later by the modal, via getSeatPool above) reports
   * as wide open -- two unrelated numbers for the same course, not a
   * live-vs-stale gap. Every requested code comes back in the map, missing
   * rows included, with UNCLAIMED_SEAT_POOL filled in -- a caller should
   * never need a second `?? someDefault` of its own. */
  async getSeatPools(courseCodes: string[]): Promise<Map<string, SeatPoolInfo>> {
    const codes = [...new Set(courseCodes.filter(Boolean))];
    const map = new Map<string, SeatPoolInfo>();
    for (const code of codes) map.set(code, { ...UNCLAIMED_SEAT_POOL });
    if (!codes.length) return map;
    const { data, error } = await this.client
      .from('course_seat_pools')
      .select('course_code, capacity, seats_taken')
      .in('course_code', codes);
    if (error) throw error;
    for (const row of (data as { course_code: string; capacity: number; seats_taken: number }[]) ?? []) {
      map.set(row.course_code, { capacity: row.capacity, seatsTaken: row.seats_taken });
    }
    return map;
  }

  /** The caller's own status only -- routed through get_my_enrollment
   * (SECURITY DEFINER), not a plain table select: a waitlisted student's
   * rank requires counting OTHER students' rows, which
   * course_enrollments' RLS (select-own-row-only) would silently reduce
   * to zero for a direct client-side query. */
  async getMyEnrollment(courseCode: string): Promise<MyEnrollment | null> {
    const { data, error } = await this.client
      .rpc('get_my_enrollment', { p_course_code: courseCode })
      .maybeSingle();
    if (error) throw error;
    if (!data) return null;
    const row = data as { status: EnrollmentStatus; seat_position: number | null };
    return { status: row.status, position: row.seat_position };
  }

  /** Applies (or, if already applied, just returns the existing status --
   * idempotent). The actual enrolled-vs-waitlisted decision happens
   * server-side inside claim_course_seat; this is only ever reporting what
   * the database already decided, atomically, under any amount of
   * concurrent traffic. */
  async apply(courseCode: string): Promise<MyEnrollment> {
    const { data, error } = await this.client
      .rpc('claim_course_seat', { p_course_code: courseCode })
      .single();
    if (error) throw error;
    const row = data as { status: EnrollmentStatus; seat_position: number | null };
    return { status: row.status, position: row.seat_position };
  }

  /** Drops the caller's own seat (or waitlist spot). If they were
   * 'enrolled', drop_course_seat also atomically promotes the
   * longest-waiting waitlisted student into the freed seat server-side --
   * nothing further to do here. */
  async drop(courseCode: string): Promise<void> {
    const { error } = await this.client.rpc('drop_course_seat', { p_course_code: courseCode });
    if (error) throw error;
  }

  /** Whether applying right now would land a real seat or just a waitlist
   * spot -- checked BEFORE calling apply() so a caller can ask the student
   * first instead of silently waitlisting them. Every enroll surface
   * (chatbot panel, Flowchart, Recommendations, Weekly Schedule) should
   * gate on this the same way. */
  async checkAvailability(courseCode: string): Promise<{ seatAvailable: boolean; estimatedWaitlistPosition: number }> {
    const pool = await this.getSeatPool(courseCode);
    const { seatAvailable } = seatStatusFrom(pool);
    return {
      seatAvailable,
      estimatedWaitlistPosition: Math.max(1, pool.seatsTaken - pool.capacity + 1),
    };
  }

  /** Given a full course's sibling requirement-options (Course.options),
   * finds the first one that currently has an open seat -- checked in the
   * order the planning engine already ranked them. Returns null if every
   * alternative is also full, so the caller can fall back to offering the
   * waitlist instead. */
  async findOpenAlternative(optionCodes: string[]): Promise<string | null> {
    for (const code of optionCodes) {
      const { seatAvailable } = await this.checkAvailability(code);
      if (seatAvailable) return code;
    }
    return null;
  }
}
