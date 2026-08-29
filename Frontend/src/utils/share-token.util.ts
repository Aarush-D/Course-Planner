import type { PlannerState } from '../services/planner-state.service';

/** Encodes/decodes a PlannerState into a URL-safe token for the read-only
 * share link. The backend is fully stateless -- /api/plan takes the whole
 * client state and returns everything needed to render the UI -- so the
 * entire state fits in the URL itself; no database or share-code lookup
 * needed. */

export function encodeShareToken(state: PlannerState): string {
  const bytes = new TextEncoder().encode(JSON.stringify(state));
  let binary = '';
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function decodeShareToken(token: string): PlannerState {
  let parsed: unknown;
  try {
    const base64 = token.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const bytes = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new Error('This link is broken or out of date.');
  }
  if (!isPlannerState(parsed)) {
    throw new Error('This link is broken or out of date.');
  }
  return parsed;
}

function isPlannerState(x: any): x is PlannerState {
  return (
    x &&
    typeof x.major === 'string' &&
    Array.isArray(x.completed) &&
    typeof x.startYear === 'number' &&
    typeof x.gradYears === 'number' &&
    typeof x.allowSummer === 'boolean' &&
    Array.isArray(x.summerUnavailable) &&
    Array.isArray(x.consumedSlotIds) &&
    Array.isArray(x.additionalMajors) &&
    Array.isArray(x.minors) &&
    typeof x.campus === 'string' &&
    typeof x.undecided === 'boolean'
  );
}
