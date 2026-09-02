import { Injectable, inject } from '@angular/core';
import { SupabaseService } from './supabase.service';

export type EnrollmentStatus = 'enrolled' | 'waitlisted';

export interface SeatPoolInfo {
  capacity: number;
  seatsTaken: number;
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
    return { capacity: data?.capacity ?? 50, seatsTaken: data?.seats_taken ?? 0 };
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
}
