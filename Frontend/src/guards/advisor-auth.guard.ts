import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { SupabaseService } from '../services/supabase.service';

/** Protects /advisor/dashboard and /advisor/review/:id. Awaits the real
 * session check directly (rather than trusting SupabaseService.session(),
 * which populates asynchronously) so a fresh page load doesn't race a
 * signal that hasn't resolved yet and wrongly bounce a logged-in advisor.
 *
 * Checks isAdvisor(), not just "has a session" -- a signed-in STUDENT
 * satisfied the old check too, since every account (student or advisor)
 * comes from the same Supabase Auth pool. That was a client-side-only gap
 * (the actual Supabase reads these pages make are already RLS-gated by
 * is_advisor(), so it was never a live data leak), but it's exactly the
 * "every protected route must verify server-side" pattern worth closing
 * here too, not just relying on the RLS backstop. */
export const advisorAuthGuard: CanActivateFn = async () => {
  const supabase = inject(SupabaseService);
  const router = inject(Router);
  const { data } = await supabase.client.auth.getSession();
  if (data.session && (await supabase.isAdvisor())) return true;
  return router.createUrlTree(['/advisor/login']);
};
