import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import { StudentSessionService } from '../../services/student-session.service';
import { SupabaseService } from '../../services/supabase.service';

@Component({
  selector: 'app-nav',
  standalone: true,
  templateUrl: './nav.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
})
export class NavComponent {
  // Starts collapsed to an icon-only rail on phone-width screens, where the
  // full-width labeled sidebar otherwise crowds out the main content —
  // desktop keeps today's always-expanded look. Manually toggleable
  // afterward at any screen size via the button at the bottom of the nav.
  collapsed = signal(window.innerWidth < 768);

  readonly supabase = inject(SupabaseService);
  private readonly studentSession = inject(StudentSessionService);
  private readonly router = inject(Router);

  toggleCollapsed(): void {
    this.collapsed.update((v) => !v);
  }

  /** Stops autosave before actually ending the session -- otherwise a
   * change made right at the moment of signing out could race and save
   * after the session's already gone. Leaves the last-loaded plan in
   * memory (matches the app's ephemeral-by-default feel elsewhere). */
  async signOutStudent() {
    this.studentSession.stopAutosave();
    await this.supabase.signOutStudent();
    this.router.navigate(['/']);
  }
}
