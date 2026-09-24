import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { StatusBadgeComponent } from '../../components/ui/status-badge/status-badge.component';
import { AdvisorRosterService } from '../../services/advisor-roster.service';
import { PlannerState } from '../../services/planner-state.service';
import { ReviewRequestService } from '../../services/review-request.service';
import { SupabaseService } from '../../services/supabase.service';
import { AdvisorRosterRow, MeetingRequestRow, ReviewRequestRow } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';

@Component({
  selector: 'app-advisor-dashboard-page',
  standalone: true,
  templateUrl: './advisor-dashboard-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe, StatusBadgeComponent],
})
export class AdvisorDashboardPageComponent implements OnInit {
  private readonly reviewRequests = inject(ReviewRequestService);
  private readonly roster = inject(AdvisorRosterService);
  private readonly supabase = inject(SupabaseService);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);

  activeTab = signal<'roster' | 'requests'>('roster');

  // ── Roster tab (default, loaded eagerly on init) ──
  rosterRows = signal<AdvisorRosterRow[]>([]);
  rosterLoading = signal(true);
  rosterError = signal<string | null>(null);
  openMeetings = signal<MeetingRequestRow[]>([]);
  inviteCode = signal<string | null>(null);
  regenerating = signal(false);
  removingId = signal<string | null>(null);

  // ── Student Requests tab (loaded lazily, first time it's opened) ──
  requests = signal<ReviewRequestRow[]>([]);
  requestsLoading = signal(false);
  requestsError = signal<string | null>(null);
  private requestsLoadedOnce = false;

  deleting = signal(false);

  async ngOnInit() {
    await this._loadRoster();
  }

  async setTab(tab: 'roster' | 'requests') {
    this.activeTab.set(tab);
    if (tab === 'requests' && !this.requestsLoadedOnce) {
      this.requestsLoadedOnce = true;
      this.requestsLoading.set(true);
      try {
        this.requests.set(await this.reviewRequests.listPendingRequests());
      } catch (e: any) {
        this.requestsError.set(e?.message ?? 'Could not load review requests.');
      } finally {
        this.requestsLoading.set(false);
      }
    }
  }

  /** Label for a meeting request's owner -- falls back the same way the
   * roster cards do when the student never typed a name. */
  studentLabelFor(studentId: string): string {
    return this.rosterRows().find((r) => r.student_id === studentId)?.student_label || 'A student';
  }

  major(row: ReviewRequestRow): string {
    return (row.plan_state as PlannerState | null)?.major ?? '—';
  }

  async copyInviteCode() {
    const code = this.inviteCode();
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      this.toast.show('Invite code copied.', 'success');
    } catch {
      this.toast.show(code, 'success');
    }
  }

  async regenerateCode() {
    if (this.regenerating()) return;
    const proceed = window.confirm('Get a new invite link? The old one will stop working immediately.');
    if (!proceed) return;
    this.regenerating.set(true);
    try {
      this.inviteCode.set(await this.roster.regenerateInviteCode());
      this.toast.show('New invite link generated.', 'success');
    } catch {
      this.toast.show("Couldn’t generate a new link — try again in a moment.", 'error');
    } finally {
      this.regenerating.set(false);
    }
  }

  async removeAdvisee(row: AdvisorRosterRow) {
    if (this.removingId()) return;
    const proceed = window.confirm(
      `Remove ${row.student_label || 'this student'} from your roster? They'll no longer see you as their advisor, and you'll lose access to their plan.`,
    );
    if (!proceed) return;
    this.removingId.set(row.student_id);
    try {
      await this.roster.removeAdvisee(row.student_id);
      this.rosterRows.update((rows) => rows.filter((r) => r.student_id !== row.student_id));
    } catch {
      this.toast.show("Couldn’t remove that student — try again in a moment.", 'error');
    } finally {
      this.removingId.set(null);
    }
  }

  async signOut() {
    await this.supabase.signOutAdvisor();
    this.router.navigate(['/advisor/login']);
  }

  async deleteAccount() {
    if (this.deleting()) return;
    const proceed = window.confirm(
      'Permanently delete your advisor account? Comments you\'ve posted stay as part of students\' review ' +
        'request history, no longer tied to your identity. This cannot be undone.'
    );
    if (!proceed) return;
    this.deleting.set(true);
    try {
      await this.supabase.deleteMyAccount();
      this.router.navigate(['/']);
    } catch {
      this.toast.show("Couldn’t delete your account — try again in a moment.", 'error');
    } finally {
      this.deleting.set(false);
    }
  }

  private async _loadRoster() {
    this.rosterLoading.set(true);
    this.rosterError.set(null);
    try {
      const [rows, code] = await Promise.all([this.roster.listMyRoster(), this.roster.getMyInviteCode()]);
      this.rosterRows.set(rows);
      this.inviteCode.set(code);
      // Best-effort: a missing table (migration not applied yet) just
      // means no "needs a reply" card, not a broken roster tab.
      this.openMeetings.set(await this.roster.listOpenMeetingRequestsForAdvisor().catch(() => []));
    } catch (e: any) {
      this.rosterError.set(e?.message ?? 'Could not load your roster.');
    } finally {
      this.rosterLoading.set(false);
    }
  }
}
