/** Canonical course code: uppercase, single space, no leading zeros
 * (ENGL 015 -> ENGL 15). Mirrors Backend/planner_engine.py's norm_code
 * exactly, so a free-typed "cmpsc131" and a card-sourced "CMPSC 131" land
 * in the same course_ratings bucket. */
export function normalizeCourseCode(code: string): string {
  const s = (code || '')
    .replace(/ /g, ' ')
    .trim()
    .toUpperCase()
    .replace(/\s+/g, ' ');
  const m = s.match(/^([A-Z]+)\s*0*(\d+[A-Z]*)$/);
  return m ? `${m[1]} ${m[2]}` : s;
}
