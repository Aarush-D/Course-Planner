import { ChangeDetectionStrategy, Component, ElementRef, HostListener, computed, inject, signal, viewChild } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { AdvisorRosterService } from '../../services/advisor-roster.service';
import { PlannerStateService } from '../../services/planner-state.service';
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
  private readonly planner = inject(PlannerStateService);
  private readonly router = inject(Router);
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly toast = inject(ToastService);
  private readonly profiles = inject(StudentProfileService);
  private readonly roster = inject(AdvisorRosterService);

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
  /** The user id whose profile the fields above currently hold -- keyed on
   * the session user (not a bare boolean) so signing out and back in as a
   * DIFFERENT student always refetches instead of showing, and on "Save
   * LinkedIn" upserting, the previous student's URL onto the new row. */
  private profileLoadedFor: string | null = null;
  savingProfile = signal(false);

  /** Same lazy-load-on-open, keyed-on-user-id pattern as the LinkedIn
   * fields above -- this popover is the one place a student manages their
   * advisor relationships, so there's no reason to fetch before they've
   * ever opened it. */
  myAdvisors = signal<{ advisorId: string; displayName: string; joinedAt: string }[]>([]);
  private advisorsLoadedFor: string | null = null;
  leavingAdvisorId = signal<string | null>(null);
  joinCode = signal('');
  joinLabel = signal('');
  joining = signal(false);

  async toggleOpen() {
    this.open.update((v) => !v);
    const userId = this.supabase.session()?.user.id ?? null;
    if (this.open() && userId && this.profileLoadedFor !== userId) {
      // Clear whatever a previous user left behind BEFORE the fetch so a
      // slow response never leaves their values visible in the meantime.
      this._resetProfileFields();
      this.profileLoadedFor = userId;
      try {
        const profile = await this.profiles.getMyProfile();
        // Discard a response that resolved after the user changed underneath it.
        if (this.profileLoadedFor !== userId) return;
        this.linkedinUrl.set(profile.linkedinUrl ?? '');
        this.linkedinPublic.set(profile.isLinkedinPublic);
      } catch {
        if (this.profileLoadedFor === userId) this.profileLoadedFor = null; // allow a retry next time the menu opens
      }
    }
    if (this.open() && userId && this.advisorsLoadedFor !== userId) {
      this.myAdvisors.set([]);
      this.advisorsLoadedFor = userId;
      try {
        const advisors = await this.roster.listMyAdvisors();
        if (this.advisorsLoadedFor !== userId) return;
        this.myAdvisors.set(advisors);
      } catch {
        if (this.advisorsLoadedFor === userId) this.advisorsLoadedFor = null; // allow a retry next time the menu opens
      }
    }
  }

  async joinAdvisorRoster() {
    const code = this.joinCode().trim();
    if (!code || this.joining()) return;
    this.joining.set(true);
    try {
      const { advisorId, advisorDisplayName } = await this.roster.joinRoster(code, this.joinLabel().trim() || undefined);
      this.joinCode.set('');
      this.joinLabel.set('');
      if (!this.myAdvisors().some((a) => a.advisorId === advisorId)) {
        this.myAdvisors.update((advisors) => [
          ...advisors,
          { advisorId, displayName: advisorDisplayName, joinedAt: new Date().toISOString() },
        ]);
      }
      this.toast.show(`You're now connected with ${advisorDisplayName}.`, 'success');
    } catch (e: any) {
      this.toast.show(e?.message === 'invalid invite code' ? "That invite code didn't work." : "Couldn't join — try again in a moment.", 'error');
    } finally {
      this.joining.set(false);
    }
  }

  async leaveAdvisorRoster(advisorId: string) {
    if (this.leavingAdvisorId()) return;
    const proceed = window.confirm("Leave this advisor's roster? They'll no longer be able to see your plan.");
    if (!proceed) return;
    this.leavingAdvisorId.set(advisorId);
    try {
      await this.roster.leaveRoster(advisorId);
      this.myAdvisors.update((advisors) => advisors.filter((a) => a.advisorId !== advisorId));
    } catch {
      this.toast.show("Couldn’t leave — try again in a moment.", 'error');
    } finally {
      this.leavingAdvisorId.set(null);
    }
  }

  /** Drops every per-user field this menu caches. Called on sign-out and
   * after a successful account deletion -- the component itself outlives
   * the session (it's mounted in the always-present top-right row), so
   * nothing else would ever clear these. */
  private _resetProfileFields() {
    this.linkedinUrl.set('');
    this.linkedinPublic.set(false);
    this.profileLoadedFor = null;
    this.myAdvisors.set([]);
    this.advisorsLoadedFor = null;
    this.joinCode.set('');
    this.joinLabel.set('');
  }

  async saveLinkedin() {
    this.savingProfile.set(true);
    try {
      await this.profiles.updateProfile(this.linkedinUrl().trim() || null, this.linkedinPublic());
      this.toast.show('Saved.', 'success');
    } catch {
      this.toast.show("Couldn’t save — check the URL and try again.", 'error');
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
    // Sign-out has no failure path that needs the pre-sign-out state kept
    // around, so it's safe to reset unconditionally right here (unlike
    // deleteAccount() below, where the reset has to wait on the RPC).
    this.planner.resetToDefault();
    this._resetProfileFields();
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
      // Only reset the visible plan/chat state once the account is
      // actually gone -- if deleteMyAccount() throws (network error,
      // server rejection), the catch below leaves the pre-deletion state
      // on screen instead of blanking it out from under a still-existing
      // account.
      this.planner.resetToDefault();
      this._resetProfileFields();
      this.open.set(false);
      this.router.navigate(['/']);
    } catch {
      // stopAutosave() above already ran, but the account still exists and
      // the student is still signed in -- re-arm autosave (same resume path
      // a page reload uses) so their edits from here on keep being saved.
      this.studentSession.tryResumeSavedPlan().catch(() => {});
      this.toast.show("Couldn’t delete your account — try again in a moment.", 'error');
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
