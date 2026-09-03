/**
 * One place for what this app tells students about passwords, and for
 * turning Supabase Auth's raw error strings into something a person can act
 * on.
 *
 * Why it exists: the three screens that set or check a password
 * (student sign-up, advisor sign-up, and the reset-password page) each had
 * their own idea of the rules -- reset-password hard-coded a 6-character
 * check, the two login pages enforced nothing at all, and all three
 * surfaced `e.message` verbatim. So a student could type a 4-character
 * password, wait for a round-trip, and get a raw server string back with no
 * hint what the actual requirement was.
 *
 * MIN_LENGTH is deliberately stricter than Supabase's own default (6). The
 * server is the real gate -- this is a client-side courtesy that fails fast
 * and, more importantly, states the rule BEFORE the student commits to a
 * password. Raising the project's minimum in the Supabase dashboard
 * (Authentication -> Providers -> Email) to match is the other half; until
 * then the server simply accepts everything this allows.
 */
export const MIN_PASSWORD_LENGTH = 8;

/** Shown under the field on any screen that CREATES a password. Phrased as
 * the requirement, not an error, so it reads as guidance up front. */
export const PASSWORD_HINT = `At least ${MIN_PASSWORD_LENGTH} characters.`;

/** Validates a password the student is about to SET. Returns an error
 * message, or null when it passes.
 *
 * Deliberately not used on sign-IN: an existing account may predate this
 * rule, and refusing to even attempt the sign-in would lock that student
 * out of their own plan over a client-side check the server never made. */
export function validateNewPassword(password: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  return null;
}

/** Supabase Auth error -> a sentence that says what to do next.
 *
 * The weak-password case matters most and is the least obvious: if the
 * project's password policy is later tightened, Supabase starts rejecting
 * the EXISTING passwords of already-registered students at sign-in time
 * (documented under "How will strengthened password requirements affect
 * current users?"). Left unmapped, that student sees a raw `WeakPasswordError`
 * on a password that worked yesterday and has no idea a reset is the fix.
 */
export function describeAuthError(error: unknown, action: 'signin' | 'signup' | 'reset'): string {
  const raw = (error instanceof Error ? error.message : String(error ?? '')).toLowerCase();

  // Supabase surfaces this as WeakPasswordError, and also as a plain message
  // ("password should be at least N characters") depending on the path --
  // match on both rather than on the class, which isn't always preserved.
  const weak =
    raw.includes('weak') || raw.includes('should be at least') || raw.includes('password is too short');
  if (weak) {
    return action === 'signin'
      ? 'Your password no longer meets this site’s requirements. Use “Forgot password?” to set a new one.'
      : `That password is too weak. ${PASSWORD_HINT}`;
  }

  // A leaked-password rejection (HaveIBeenPwned check, if the project has it
  // enabled) reads as a scary generic failure otherwise.
  if (raw.includes('pwned') || raw.includes('leaked') || raw.includes('compromised')) {
    return 'That password has appeared in a known data breach. Pick a different one.';
  }

  if (raw.includes('invalid login credentials')) {
    return 'That email and password don’t match an account. Check both, or create an account instead.';
  }

  if (raw.includes('already registered') || raw.includes('already been registered')) {
    return 'An account with that email already exists — sign in instead.';
  }

  if (raw.includes('email not confirmed')) {
    return 'Check your email and confirm your account first, then sign in.';
  }

  if (raw.includes('rate limit') || raw.includes('too many')) {
    return 'Too many attempts. Wait a minute and try again.';
  }

  // Anything unrecognized keeps its original wording -- a vague catch-all
  // would hide real, actionable server messages behind "something went wrong".
  return error instanceof Error && error.message ? error.message : 'Something went wrong. Try again.';
}
