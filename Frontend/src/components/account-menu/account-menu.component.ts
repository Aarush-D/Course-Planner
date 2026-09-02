import { ChangeDetectionStrategy, Component, ElementRef, HostListener, computed, inject, signal, viewChild } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { StudentProfileService } from '../../services/student-profile.service';
import { StudentSessionService } from '../../services/student-session.service';
import { SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';

/** Top-right auth control -- a "Sign in" pill when signed out, or an
 * avatar badge (student's initial, same rounded-full sizing as the
 * theme/help buttons beside it) with a small dropdown when signed in.
 * Self-contained like preferences-panel.component (same outside-click
 * pattern) so app.component doesn't need its own SupabaseService wiring. */
@Component({
  selector: 'app-account-menu',
  standalone: true,
  templateUrl: './account-menu.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
})
export class AccountMenuComponent {
  readonly supabase = inject(SupabaseService);
  private readonly studentSession = inject(StudentSessionService);
  private readonly router = inject(Router);
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly toast = inject(ToastService);
  private readonly profiles = inject(StudentProfileService);

  open = signal(false);
  deleting = signal(false);

  private readonly toggleButton = viewChild<ElementRef<HTMLButtonElement>>('toggleButton');

  readonly email = computed(() => this.supabase.session()?.user.email ?? null);
  readonly initial = computed(() => (this.email()?.[0] ?? '?').toUpperCase());

  /** Loaded lazily the first time the menu opens (not on every app
   * load) -- this popover is the one place a student edits it, so there's
   * no reason to fetch it before they've ever looked. */
  linkedinUrl = signal('');
  linkedinPublic = signal(false);
  private profileLoaded = false;
  savingProfile = signal(false);

  async toggleOpen() {
    this.open.update((v) => !v);
    if (this.open() && !this.profileLoaded) {
      this.profileLoaded = true;
      try {
        const profile = await this.profiles.getMyProfile();
        this.linkedinUrl.set(profile.linkedinUrl ?? '');
        this.linkedinPublic.set(profile.isLinkedinPublic);
      } catch {
        this.profileLoaded = false; // allow a retry next time the menu opens
      }
    }
  }

  async saveLinkedin() {
    this.savingProfile.set(true);
    try {
      await this.profiles.updateProfile(this.linkedinUrl().trim() || null, this.linkedinPublic());
      this.toast.show('Saved.', 'success');
    } catch {
      this.toast.show("Couldn't save — check the URL and try again.", 'error');
    } finally {
      this.savingProfile.set(false);
    }
  }

  @HostListener('document:keydown.escape')
  onEscape() {
    if (!this.open()) return;
    this.open.set(false);
    this.toggleButton()?.nativeElement.focus();
  }

  async signOut() {
    this.open.set(false);
    this.studentSession.stopAutosave();
    await this.supabase.signOutStudent();
    this.router.navigate(['/']);
  }

  async deleteAccount() {
    if (this.deleting()) return;
    const proceed = window.confirm(
      'Permanently delete your account and every plan saved to it? This cannot be undone.'
    );
    if (!proceed) return;
    this.deleting.set(true);
    try {
      this.studentSession.stopAutosave();
      await this.supabase.deleteMyAccount();
      this.open.set(false);
      this.router.navigate(['/']);
    } catch {
      this.toast.show("Couldn't delete your account — try again in a moment.", 'error');
    } finally {
      this.deleting.set(false);
    }
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent) {
    if (this.open() && !this.host.nativeElement.contains(event.target as Node)) {
      this.open.set(false);
    }
  }
}
