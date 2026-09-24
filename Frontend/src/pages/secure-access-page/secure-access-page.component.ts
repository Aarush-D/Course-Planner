import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { environment } from '../../environments/environment';
import { SupabaseService } from '../../services/supabase.service';

interface EnrollingFactor {
  factorId: string;
  qrCode: string;
  secret: string;
}

/** A staging page for stronger sign-in: "Continue with Microsoft" and
 * authenticator-app (TOTP) two-step verification. Deliberately separate
 * from the existing /login and /advisor/login flows -- nothing else in
 * the app links here or requires it, so the main experience (where every
 * route has to keep working with no session) is unchanged until this is
 * promoted. The Microsoft button stays disabled until
 * environment.microsoftLoginEnabled is turned on; two-step verification
 * works today for any signed-in account. */
@Component({
  selector: 'app-secure-access-page',
  standalone: true,
  templateUrl: './secure-access-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
})
export class SecureAccessPageComponent {
  private readonly supabase = inject(SupabaseService);

  readonly microsoftEnabled = environment.microsoftLoginEnabled;
  readonly session = this.supabase.session;
  readonly email = computed(() => this.session()?.user.email ?? null);

  factors = signal<{ id: string; friendlyName: string | null }[]>([]);
  aal = signal<{ current: string | null; next: string | null } | null>(null);
  enrolling = signal<EnrollingFactor | null>(null);
  code = signal('');
  busy = signal(false);
  error = signal<string | null>(null);
  info = signal<string | null>(null);

  /** Signed in with a verified factor but this session hasn't proven
   * possession of it yet. */
  needsSecondStep = computed(() => {
    const a = this.aal();
    return !!a && a.current === 'aal1' && a.next === 'aal2';
  });
  secured = computed(() => this.aal()?.current === 'aal2');

  constructor() {
    effect(() => {
      if (this.session()) this._refresh();
    });
  }

  async continueWithMicrosoft() {
    if (!this.microsoftEnabled) return;
    this.error.set(null);
    try {
      await this.supabase.signInWithMicrosoft();
    } catch (e: any) {
      this.error.set(e?.message ?? 'Couldn’t start Microsoft sign-in. Try again in a moment.');
    }
  }

  async startEnroll() {
    this.busy.set(true);
    this.error.set(null);
    this.info.set(null);
    try {
      this.enrolling.set(await this.supabase.enrollTotp(`Authenticator ${new Date().toISOString().slice(0, 10)}`));
    } catch (e: any) {
      this.error.set(e?.message ?? 'Couldn’t start setup. Try again in a moment.');
    } finally {
      this.busy.set(false);
    }
  }

  async verify(factorId: string) {
    const code = this.code().replace(/\s+/g, '');
    if (!/^\d{6}$/.test(code)) {
      this.error.set('Enter the 6-digit code from your authenticator app.');
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.supabase.verifyTotp(factorId, code);
      this.code.set('');
      this.enrolling.set(null);
      this.info.set('Verified. Two-step verification is on for this account.');
      await this._refresh();
    } catch (e: any) {
      this.error.set('That code didn’t work. Codes change every 30 seconds, so try the current one.');
    } finally {
      this.busy.set(false);
    }
  }

  async cancelEnroll() {
    const pending = this.enrolling();
    this.enrolling.set(null);
    this.code.set('');
    if (pending) {
      try {
        await this.supabase.removeTotp(pending.factorId);
      } catch {
        // An abandoned unverified factor is harmless -- Supabase discards it.
      }
    }
  }

  async remove(factorId: string) {
    if (!window.confirm('Turn off two-step verification for this account?')) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.supabase.removeTotp(factorId);
      this.info.set('Two-step verification turned off.');
      await this._refresh();
    } catch (e: any) {
      this.error.set(
        e?.message ?? 'Couldn’t turn that off. You may need to verify a code first, then try again.',
      );
    } finally {
      this.busy.set(false);
    }
  }

  private async _refresh() {
    try {
      const [factors, aal] = await Promise.all([this.supabase.listTotpFactors(), this.supabase.assuranceLevel()]);
      this.factors.set(factors);
      this.aal.set(aal);
    } catch {
      this.error.set('Couldn’t load your security settings. Try again in a moment.');
    }
  }
}
