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

export function formatClockTime(minutes: number): string {
  const h24 = Math.floor(minutes / 60);
  const m = minutes % 60;
  const period = h24 >= 12 ? 'PM' : 'AM';
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return `${h12}:${m.toString().padStart(2, '0')} ${period}`;
}

export const DAY_LABELS: Record<string, string> = { M: 'Mon', T: 'Tue', W: 'Wed', R: 'Thu', F: 'Fri' };
export const WEEKDAY_CODES = ['M', 'T', 'W', 'R', 'F'];
