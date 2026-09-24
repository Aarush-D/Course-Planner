import { Injectable, inject, signal } from '@angular/core';
import type { RealtimeChannel, RealtimePostgresChangesPayload } from '@supabase/supabase-js';
import { SupabaseService } from './supabase.service';

/** Only ever 'enrolled' since migration 0023: a full course refuses the
 * claim outright (see CourseFullError) instead of waitlisting. Kept as a
 * named type so the shape reads the same at every call site. */
export type EnrollmentStatus = 'enrolled';

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
}

/** The one sentence every surface shows for a full course -- the chat
 * panel, Flowchart cards, Recommendations, and the Weekly Schedule modal
 * all say exactly this, so a student never sees two wordings for one
 * situation. */
export function courseFullMessage(courseCode: string): string {
  return `${courseCode} is full — its seats can’t be registered.`;
}

/** Thrown by apply() when the course has no seat left. Deliberately its
 * own class, not a generic Error with a recognisable message: callers
 * branch on it (`instanceof CourseFullError`) to show courseFullMessage
 * and, where the course has sibling options, go look for an open
 * alternative -- something they must not do for a network failure that
 * merely *looks* like "couldn't enroll". */
export class CourseFullError extends Error {
  constructor(readonly courseCode: string) {
    super(courseFullMessage(courseCode));
    this.name = 'CourseFullError';
  }
}

/** The exact token claim_course_seat raises (migration 0023). Matched
 * verbatim -- change both places or neither. */
const COURSE_FULL_TOKEN = 'course_full';

function isCourseFullSqlError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'message' in error &&
    String((error as { message: unknown }).message).includes(COURSE_FULL_TOKEN)
  );
}

interface SeatPoolRow {
  course_code: string;
  capacity: number;
  seats_taken: number;
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
 * Seat counts are LIVE. Every pool this service has ever read sits in the
 * `pools` signal, and a single Supabase Realtime subscription on
 * course_seat_pools (publication added in migration 0023) pushes every
 * insert/update/delete into that map the moment another student's claim
 * or drop commits. Surfaces read `pools`/`livePool()` and re-render on
 * their own; nothing polls. The subscription is opened lazily by the first
 * pool read, so a visitor who never looks at a course never holds a
 * websocket for one.
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

  private readonly _pools = signal<Map<string, SeatPoolInfo>>(new Map());
  /** Every pool this session has read, kept current by Realtime. A course
   * absent from the map has simply never been asked about -- callers fall
   * back to UNCLAIMED_SEAT_POOL or hide the count, never guess "full". */
  readonly pools = this._pools.asReadonly();

  private liveChannel: RealtimeChannel | null = null;

  /** Current pool for one course, or null if it has never been fetched.
   * Reactive when read inside a computed/effect/template. */
  livePool(courseCode: string): SeatPoolInfo | null {
    return this._pools().get(courseCode) ?? null;
  }

  /** True only when a pool has been read AND it is at capacity. Unknown
   * (never fetched) is `false` -- a course must never render as Full
   * before a real row has been seen (the old dummy hash did exactly that). */
  isFull(courseCode: string): boolean {
    const pool = this.livePool(courseCode);
    return !!pool && !seatStatusFrom(pool).seatAvailable;
  }

  /** Public, non-identifying aggregate counts -- safe to show before the
   * student has applied, or to a student who never applies at all.
   * Returns a not-yet-applied-for course as "full capacity, zero taken"
   * (the pool row only starts existing once someone actually applies --
   * see claim_course_seat's own upsert). Also lands in `pools`. */
  async getSeatPool(courseCode: string): Promise<SeatPoolInfo> {
    this._ensureLive();
    const { data, error } = await this.client
      .from('course_seat_pools')
      .select('capacity, seats_taken')
      .eq('course_code', courseCode)
      .maybeSingle();
    if (error) throw error;
    const pool = data ? { capacity: data.capacity, seatsTaken: data.seats_taken } : { ...UNCLAIMED_SEAT_POOL };
    this._merge([[courseCode, pool]]);
    return pool;
  }

  /** Same pool, batched for a whole list of courses at once -- one round
   * trip, never N+1 (same pattern as CourseRatingService.getSummaries()).
   * Every requested code comes back in the map, missing rows included,
   * with UNCLAIMED_SEAT_POOL filled in -- a caller should never need a
   * second `?? someDefault` of its own. Also lands in `pools`. */
  async getSeatPools(courseCodes: string[]): Promise<Map<string, SeatPoolInfo>> {
    const codes = [...new Set(courseCodes.filter(Boolean))];
    const map = new Map<string, SeatPoolInfo>();
    for (const code of codes) map.set(code, { ...UNCLAIMED_SEAT_POOL });
    if (!codes.length) return map;
    this._ensureLive();
    const { data, error } = await this.client
      .from('course_seat_pools')
      .select('course_code, capacity, seats_taken')
      .in('course_code', codes);
    if (error) throw error;
    for (const row of (data as SeatPoolRow[]) ?? []) {
      map.set(row.course_code, { capacity: row.capacity, seatsTaken: row.seats_taken });
    }
    this._merge([...map.entries()]);
    return map;
  }

  /** The caller's own status only -- routed through get_my_enrollment
   * (SECURITY DEFINER) rather than a plain table select, matching how
   * every other per-student read in this app goes through an RPC. */
  async getMyEnrollment(courseCode: string): Promise<MyEnrollment | null> {
    const { data, error } = await this.client
      .rpc('get_my_enrollment', { p_course_code: courseCode })
      .maybeSingle();
    if (error) throw error;
    if (!data) return null;
    return { status: 'enrolled' };
  }

  /** Claims a seat (or, if already holding one, just returns that --
   * idempotent). The decision is made server-side inside claim_course_seat,
   * atomically, under any amount of concurrent traffic. A full course
   * REJECTS: this throws CourseFullError, never a waitlist spot. Any
   * outcome, success or full, also refreshes the pool so the caller's own
   * screen reflects the new count immediately rather than waiting a beat
   * for the Realtime echo of its own write. */
  async apply(courseCode: string): Promise<MyEnrollment> {
    const { error } = await this.client
      .rpc('claim_course_seat', { p_course_code: courseCode })
      .single();
    if (error) {
      if (isCourseFullSqlError(error)) {
        void this.getSeatPool(courseCode).catch(() => {});
        throw new CourseFullError(courseCode);
      }
      throw error;
    }
    void this.getSeatPool(courseCode).catch(() => {});
    return { status: 'enrolled' };
  }

  /** Drops the caller's own seat. release_freed_course_seat (a trigger)
   * decrements the shared count in the same transaction, so everyone
   * else's live count opens up by one at the same instant. */
  async drop(courseCode: string): Promise<void> {
    const { error } = await this.client.rpc('drop_course_seat', { p_course_code: courseCode });
    if (error) throw error;
    void this.getSeatPool(courseCode).catch(() => {});
  }

  /** Whether applying right now would land a seat -- checked BEFORE apply()
   * so a full course gets its message without a doomed round trip. The
   * server still has the final say (apply() can throw CourseFullError if
   * the last seat went between this read and the claim). */
  async checkAvailability(courseCode: string): Promise<{ seatAvailable: boolean; seatsLeft: number }> {
    return seatStatusFrom(await this.getSeatPool(courseCode));
  }

  /** Given a full course's sibling requirement-options (Course.options),
   * finds the first one that currently has an open seat -- checked in the
   * order the planning engine already ranked them. Returns null if every
   * alternative is also full. */
  async findOpenAlternative(optionCodes: string[]): Promise<string | null> {
    if (!optionCodes.length) return null;
    const pools = await this.getSeatPools(optionCodes);
    for (const code of optionCodes) {
      const pool = pools.get(code);
      if (pool && seatStatusFrom(pool).seatAvailable) return code;
    }
    return null;
  }

  // ── Live store ─────────────────────────────────────────────────────────

  private _merge(entries: [string, SeatPoolInfo][]): void {
    if (!entries.length) return;
    this._pools.update((current) => {
      const next = new Map(current);
      for (const [code, pool] of entries) next.set(code, pool);
      return next;
    });
  }

  /** Opens the one Realtime subscription, once. `subscribe`'s status
   * callback re-reads every known pool each time the channel (re)connects:
   * that closes the gap between "first fetch answered" and "subscription
   * live" on startup, and heals whatever was missed across a dropped
   * websocket -- Realtime does not replay changes from while you were
   * away. */
  private _ensureLive(): void {
    if (this.liveChannel) return;
    this.liveChannel = this.client
      .channel('course_seat_pools:live')
      .on<SeatPoolRow>(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'course_seat_pools' },
        (payload) => this._onLiveChange(payload),
      )
      .subscribe((status) => {
        if (status === 'SUBSCRIBED') void this._resyncKnownPools();
      });
  }

  private _onLiveChange(payload: RealtimePostgresChangesPayload<SeatPoolRow>): void {
    if (payload.eventType === 'DELETE') {
      // A deleted pool row means "nobody holds a seat here any more" --
      // exactly the state UNCLAIMED_SEAT_POOL describes. `old` carries the
      // primary key (course_code) even without REPLICA IDENTITY FULL.
      const code = (payload.old as Partial<SeatPoolRow>).course_code;
      if (code) this._merge([[code, { ...UNCLAIMED_SEAT_POOL }]]);
      return;
    }
    const row = payload.new as SeatPoolRow;
    if (!row?.course_code) return;
    this._merge([[row.course_code, { capacity: row.capacity, seatsTaken: row.seats_taken }]]);
  }

  private async _resyncKnownPools(): Promise<void> {
    const codes = [...this._pools().keys()];
    if (!codes.length) return;
    try {
      await this.getSeatPools(codes);
    } catch {
      // best-effort -- the next change event or read will catch it up
    }
  }
}
