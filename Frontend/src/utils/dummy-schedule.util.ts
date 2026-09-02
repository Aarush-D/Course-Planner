/** Deterministic, made-up Mon–Fri meeting times for the weekly schedule
 * preview. PSU's public bulletin data (what Backend/planner_engine.py's
 * catalog is built from) has no real per-section meeting times at all —
 * that lives in LionPATH's semester-specific course search, a completely
 * different, constantly-changing dataset this app has never touched.
 * Rather than pretend otherwise, this generates a plausible-looking
 * placeholder slot per course, deterministically from its code (so the
 * same course always lands in the same slot across a session and reloads,
 * instead of jumping around) — clearly labeled as sample/illustrative in
 * the UI, never presented as a real registration time. */

export interface ScheduleSlot {
  /** 'M' | 'T' | 'W' | 'R' | 'F' — PSU's own single-letter day codes. */
  days: string[];
  startMinutes: number; // minutes since midnight
  endMinutes: number;
}

// Real PSU period patterns: MWF classes run 50 minutes on the hour+5 past;
// TTh classes run 75 minutes. A mix of both, spread across a normal
// class day, so the grid doesn't look like every course is back-to-back.
const TEMPLATES: ScheduleSlot[] = [
  { days: ['M', 'W', 'F'], startMinutes: 8 * 60, endMinutes: 8 * 60 + 50 },
  { days: ['M', 'W', 'F'], startMinutes: 9 * 60 + 5, endMinutes: 9 * 60 + 55 },
  { days: ['M', 'W', 'F'], startMinutes: 10 * 60 + 10, endMinutes: 11 * 60 },
  { days: ['M', 'W', 'F'], startMinutes: 11 * 60 + 15, endMinutes: 12 * 60 + 5 },
  { days: ['M', 'W', 'F'], startMinutes: 13 * 60 + 25, endMinutes: 14 * 60 + 15 },
  { days: ['M', 'W', 'F'], startMinutes: 14 * 60 + 30, endMinutes: 15 * 60 + 20 },
  { days: ['T', 'R'], startMinutes: 8 * 60, endMinutes: 9 * 60 + 15 },
  { days: ['T', 'R'], startMinutes: 9 * 60 + 45, endMinutes: 11 * 60 },
  { days: ['T', 'R'], startMinutes: 11 * 60 + 15, endMinutes: 12 * 60 + 30 },
  { days: ['T', 'R'], startMinutes: 13 * 60, endMinutes: 14 * 60 + 15 },
  { days: ['T', 'R'], startMinutes: 14 * 60 + 45, endMinutes: 16 * 60 },
];

/** Simple, deterministic string hash — no crypto needed, just needs to
 * spread course codes evenly across TEMPLATES. */
function hashCode(code: string): number {
  let hash = 0;
  for (let i = 0; i < code.length; i++) {
    hash = (hash * 31 + code.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

export function dummySlotFor(courseCode: string): ScheduleSlot {
  return TEMPLATES[hashCode(courseCode) % TEMPLATES.length];
}

/** Same honesty rule as the time slots above: PSU doesn't expose real
 * per-section seat/waitlist counts anywhere this app touches (that's
 * LionPATH registration data, refreshed by the second during add/drop —
 * nothing a static bulletin-derived catalog could show even if this app
 * wanted to), so this is illustrative only, clearly labeled wherever it's
 * shown. 'salted' with a suffix so a course's seat status doesn't move in
 * lockstep with its time slot (two independent, still-deterministic draws
 * from the same course code, not the same draw reused twice). */
export interface SeatAvailability {
  status: 'open' | 'waitlist' | 'full';
  seatsLeft: number; // 0 unless status === 'open'
  capacity: number;
  waitlistCount: number; // 0 unless status === 'waitlist'
}

// Weighted so most sections still have room, matching how a real add/drop
// period actually looks most of the time -- a handful of popular sections
// waitlisted or closed, not the whole schedule.
const SEAT_STATUSES: SeatAvailability['status'][] = [
  'open', 'open', 'open', 'open', 'open', 'open',
  'waitlist', 'waitlist',
  'full',
];

export function dummySeatAvailabilityFor(courseCode: string): SeatAvailability {
  const h = hashCode(`${courseCode}:seats`);
  const capacity = 20 + (h % 6) * 10; // 20..70, a plausible spread of real PSU section sizes
  const status = SEAT_STATUSES[h % SEAT_STATUSES.length];
  if (status === 'open') {
    const seatsLeft = 1 + (h % Math.floor(capacity * 0.4));
    return { status, seatsLeft, capacity, waitlistCount: 0 };
  }
  if (status === 'waitlist') {
    return { status, seatsLeft: 0, capacity, waitlistCount: 1 + (h % 15) };
  }
  return { status: 'full', seatsLeft: 0, capacity, waitlistCount: 0 };
}

// Same honesty rule as everything else in this file: PSU's public catalog
// carries no instructor, room, or delivery-mode assignment at all (that's
// section-level LionPATH data, built per-term, not per-course) -- these are
// illustrative placeholders only, deterministic per course code so they
// don't shuffle on every render, and always labeled sample/illustrative
// wherever the UI shows them.

const FIRST_NAMES = [
  'James', 'Maria', 'David', 'Linda', 'Robert', 'Susan', 'Michael', 'Karen',
  'John', 'Patricia', 'Daniel', 'Nancy', 'Kevin', 'Angela',
];

const LAST_NAMES = [
  'Nguyen', 'Smith', 'Patel', 'Garcia', 'Chen', 'Johnson', 'Kim', 'Brown',
  'Rossi', 'Williams', 'Singh', 'Davis', 'Park', 'Martin',
];

export function dummyProfessorFor(courseCode: string): string {
  const h = hashCode(`${courseCode}:professor`);
  const first = FIRST_NAMES[h % FIRST_NAMES.length];
  const last = LAST_NAMES[Math.floor(h / FIRST_NAMES.length) % LAST_NAMES.length];
  return `${first} ${last}`;
}

const BUILDINGS = [
  'Willard Building', 'Thomas Building', 'IST Building', 'Business Building',
  'Sackett Building', 'Chambers Building', 'Osmond Laboratory', 'Boucke Building',
  'Hammond Building', 'Forum Building',
];

export function dummyBuildingFor(courseCode: string): string {
  const h = hashCode(`${courseCode}:building`);
  return BUILDINGS[h % BUILDINGS.length];
}

export type Modality = 'In-person' | 'Online' | 'Hybrid';

// Weighted so in-person is the common case, matching how most PSU sections
// actually run -- same weighted-array trick as SEAT_STATUSES above.
const MODALITIES: Modality[] = [
  'In-person', 'In-person', 'In-person', 'In-person', 'In-person', 'In-person',
  'Hybrid', 'Hybrid',
  'Online',
];

export function dummyModalityFor(courseCode: string): Modality {
  const h = hashCode(`${courseCode}:modality`);
  return MODALITIES[h % MODALITIES.length];
}

export function formatClockTime(minutes: number): string {
  const h24 = Math.floor(minutes / 60);
  const m = minutes % 60;
  const period = h24 >= 12 ? 'PM' : 'AM';
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return `${h12}:${m.toString().padStart(2, '0')} ${period}`;
}

export const DAY_LABELS: Record<string, string> = { M: 'Mon', T: 'Tue', W: 'Wed', R: 'Thu', F: 'Fri' };
export const WEEKDAY_CODES = ['M', 'T', 'W', 'R', 'F'];
