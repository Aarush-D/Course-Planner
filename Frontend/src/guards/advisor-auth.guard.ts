import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { SupabaseService } from '../services/supabase.service';

/** Protects /advisor/dashboard and /advisor/review/:id. Awaits the real
 * session check directly (rather than trusting SupabaseService.session(),
 * which populates asynchronously) so a fresh page load doesn't race a
 * signal that hasn't resolved yet and wrongly bounce a logged-in advisor. */
export const advisorAuthGuard: CanActivateFn = async () => {
  const supabase = inject(SupabaseService);
  const router = inject(Router);
  const { data } = await supabase.client.auth.getSession();
  if (data.session) return true;
  return router.createUrlTree(['/advisor/login']);
};
