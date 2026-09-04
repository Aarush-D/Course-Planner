import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { StatusBadgeComponent } from '../../components/ui/status-badge/status-badge.component';
import { StudentSessionService } from '../../services/student-session.service';
import { SupabaseService } from '../../services/supabase.service';
import { PASSWORD_HINT, describeAuthError, validateNewPassword } from '../../utils/password-policy';

/**
 * A real but entirely OPTIONAL account, purely so a plan survives a
 * refresh -- mirrors AdvisorLoginPageComponent's structure (same
 * mode/email/password/loading/error/info signal shape, plus a first/last
 * name pair collected on sign-up only, mirroring displayName there), and
 * routes to /your-plan instead of an advisor dashboard on success. No route
 * guard on this page or anywhere else in the student app -- unlike
 * /advisor/*, every existing route must keep working with no session at
 * all.
 */
@Component({
  selector: 'app-student-login-page',
  standalone: true,
  templateUrl: './student-login-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, StatusBadgeComponent],
})
export class StudentLoginPageComponent {
  readonly passwordHint = PASSWORD_HINT;
  private readonly supabase = inject(SupabaseService);
  private readonly studentSession = inject(StudentSessionService);
  private readonly router = inject(Router);

  mode = signal<'signin' | 'signup'>('signin');
  email = signal('');
  password = signal('');
  firstName = signal('');
  lastName = signal('');
  loading = signal(false);
  error = signal<string | null>(null);
  info = signal<string | null>(null);
  resettingPassword = signal(false);

  toggleMode() {
    this.mode.update((m) => (m === 'signin' ? 'signup' : 'signin'));
    this.error.set(null);
    this.info.set(null);
  }

  /** Reuses whatever's already typed in the email field above rather than
   * a separate "enter your email" step -- this only ever shows next to
   * that field, in signin mode. */
  async forgotPassword() {
    this.error.set(null);
    this.info.set(null);
    if (!this.email().trim()) {
      this.error.set('Enter your email above first.');
      return;
    }
    this.resettingPassword.set(true);
    try {
      await this.supabase.requestPasswordReset(this.email().trim());
      this.info.set('Check your email for a link to reset your password.');
    } catch (e: any) {
      this.error.set(describeAuthError(e, 'reset'));
    } finally {
      this.resettingPassword.set(false);
    }
  }

  async submit() {
    this.error.set(null);
    this.info.set(null);
    const isNewAccount = this.mode() === 'signup';
    // Fail fast, before a round-trip, and only when CREATING an account --
    // an existing account may predate the password rule (see
    // password-policy.ts) and never needs to re-enter its name at all.
    if (isNewAccount) {
      if (!this.firstName().trim()) {
        this.error.set('Enter your first name.');
        return;
      }
      if (!this.lastName().trim()) {
        this.error.set('Enter your last name.');
        return;
      }
      const problem = validateNewPassword(this.password());
      if (problem) {
        this.error.set(problem);
        return;
      }
    }
    this.loading.set(true);
    try {
      if (isNewAccount) {
        const { needsEmailConfirmation } = await this.supabase.signUpStudent(
          this.email(),
          this.password(),
          this.firstName().trim(),
          this.lastName().trim(),
        );
        if (needsEmailConfirmation) {
          this.info.set('Check your email to confirm your account, then sign in.');
          this.mode.set('signin');
          return;
        }
      } else {
        await this.supabase.signInStudent(this.email(), this.password());
      }
      const { data } = await this.supabase.client.auth.getUser();
      if (data.user) {
        await this.studentSession.onSignedIn(data.user.id, isNewAccount);
      }
      this.router.navigate(['/your-plan']);
    } catch (e: any) {
      this.error.set(describeAuthError(e, isNewAccount ? 'signup' : 'signin'));
    } finally {
      this.loading.set(false);
    }
  }
}
