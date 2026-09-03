import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SupabaseService } from '../../services/supabase.service';
import { PASSWORD_HINT, describeAuthError, validateNewPassword } from '../../utils/password-policy';

/** Where SupabaseService.requestPasswordReset's emailed link lands.
 * Clicking that link establishes a short-lived "recovery" session before
 * this page even mounts (the supabase-js client parses the URL's token
 * during construction, app-wide, and fires onAuthStateChange) -- this
 * page just waits for supabase.session() to reflect that, then lets the
 * student set a new password with updateUser(). Shared by both student
 * and advisor accounts, same as requestPasswordReset itself -- password
 * reset isn't a role-specific operation. */
@Component({
  selector: 'app-reset-password-page',
  standalone: true,
  templateUrl: './reset-password-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
})
export class ResetPasswordPageComponent {
  readonly passwordHint = PASSWORD_HINT;
  private readonly supabase = inject(SupabaseService);

  status = signal<'checking' | 'ready' | 'invalid' | 'done'>('checking');
  password = signal('');
  confirmPassword = signal('');
  loading = signal(false);
  error = signal<string | null>(null);

  constructor() {
    const destroyRef = inject(DestroyRef);
    // A poll, not an effect watching supabase.session() -- this only ever
    // needs to fire once, on whichever tick the recovery session first
    // shows up, and a plain interval avoids pulling in an injection
    // context an inline effect() here doesn't otherwise need.
    const check = setInterval(() => {
      if (this.supabase.session()) {
        this.status.set('ready');
        clearInterval(check);
      }
    }, 200);
    const timeout = setTimeout(() => {
      clearInterval(check);
      if (this.status() === 'checking') this.status.set('invalid');
    }, 5000);
    destroyRef.onDestroy(() => {
      clearInterval(check);
      clearTimeout(timeout);
    });
  }

  async submit() {
    this.error.set(null);
    // Was a hard-coded 6 here, which disagreed with both login forms and
    // with whatever the Supabase project actually enforces. One source of
    // truth now -- see utils/password-policy.ts.
    const problem = validateNewPassword(this.password());
    if (problem) {
      this.error.set(problem);
      return;
    }
    if (this.password() !== this.confirmPassword()) {
      this.error.set("Passwords don’t match.");
      return;
    }
    this.loading.set(true);
    try {
      await this.supabase.updatePassword(this.password());
      this.status.set('done');
    } catch (e: any) {
      this.error.set(describeAuthError(e, 'reset'));
    } finally {
      this.loading.set(false);
    }
  }
}
