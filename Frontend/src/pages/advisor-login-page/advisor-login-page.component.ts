import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { SupabaseService } from '../../services/supabase.service';

@Component({
  selector: 'app-advisor-login-page',
  standalone: true,
  templateUrl: './advisor-login-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
})
export class AdvisorLoginPageComponent {
  private readonly supabase = inject(SupabaseService);
  private readonly router = inject(Router);

  mode = signal<'signin' | 'signup'>('signin');
  email = signal('');
  password = signal('');
  displayName = signal('');
  loading = signal(false);
  error = signal<string | null>(null);
  info = signal<string | null>(null);

  toggleMode() {
    this.mode.update((m) => (m === 'signin' ? 'signup' : 'signin'));
    this.error.set(null);
    this.info.set(null);
  }

  async submit() {
    this.error.set(null);
    this.info.set(null);
    this.loading.set(true);
    try {
      if (this.mode() === 'signup') {
        if (!this.displayName().trim()) {
          this.error.set('Enter a name students will see on your comments.');
          return;
        }
        const { needsEmailConfirmation } = await this.supabase.signUpAdvisor(
          this.email(),
          this.password(),
          this.displayName().trim(),
        );
        if (needsEmailConfirmation) {
          this.info.set('Check your email to confirm your account, then sign in.');
          this.mode.set('signin');
          return;
        }
      } else {
        await this.supabase.signInAdvisor(this.email(), this.password());
      }
      this.router.navigate(['/advisor/dashboard']);
    } catch (e: any) {
      this.error.set(e?.message ?? 'Something went wrong. Try again.');
    } finally {
      this.loading.set(false);
    }
  }
}
