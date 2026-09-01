import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { StatusBadgeComponent } from '../../components/ui/status-badge/status-badge.component';
import { StudentSessionService } from '../../services/student-session.service';
import { SupabaseService } from '../../services/supabase.service';

/**
 * A real but entirely OPTIONAL account, purely so a plan survives a
 * refresh -- mirrors AdvisorLoginPageComponent's structure (same
 * mode/email/password/loading/error/info signal shape), minus the display
 * name (students have no name shown anywhere in the product), and routes
 * to /your-plan instead of an advisor dashboard on success. No route guard
 * on this page or anywhere else in the student app -- unlike /advisor/*,
 * every existing route must keep working with no session at all.
 */
@Component({
  selector: 'app-student-login-page',
  standalone: true,
  templateUrl: './student-login-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, StatusBadgeComponent],
})
export class StudentLoginPageComponent {
  private readonly supabase = inject(SupabaseService);
  private readonly studentSession = inject(StudentSessionService);
  private readonly router = inject(Router);

  mode = signal<'signin' | 'signup'>('signin');
  email = signal('');
  password = signal('');
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
      this.error.set(e?.message ?? 'Something went wrong. Try again.');
    } finally {
      this.resettingPassword.set(false);
    }
  }

  async submit() {
    this.error.set(null);
    this.info.set(null);
    this.loading.set(true);
    try {
      const isNewAccount = this.mode() === 'signup';
      if (isNewAccount) {
        const { needsEmailConfirmation } = await this.supabase.signUpStudent(this.email(), this.password());
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
      this.error.set(e?.message ?? 'Something went wrong. Try again.');
    } finally {
      this.loading.set(false);
    }
  }
}
