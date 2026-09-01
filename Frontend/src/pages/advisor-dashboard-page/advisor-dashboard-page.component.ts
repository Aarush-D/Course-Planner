import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { StatusBadgeComponent } from '../../components/ui/status-badge/status-badge.component';
import { PlannerState } from '../../services/planner-state.service';
import { ReviewRequestService } from '../../services/review-request.service';
import { SupabaseService } from '../../services/supabase.service';
import { ReviewRequestRow } from '../../services/supabase.service';
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
  private readonly supabase = inject(SupabaseService);
  private readonly router = inject(Router);
  private readonly toast = inject(ToastService);

  requests = signal<ReviewRequestRow[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  deleting = signal(false);

  async ngOnInit() {
    try {
      this.requests.set(await this.reviewRequests.listPendingRequests());
    } catch (e: any) {
      this.error.set(e?.message ?? 'Could not load review requests.');
    } finally {
      this.loading.set(false);
    }
  }

  major(row: ReviewRequestRow): string {
    return (row.plan_state as PlannerState | null)?.major ?? '—';
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
      this.toast.show("Couldn't delete your account — try again in a moment.", 'error');
    } finally {
      this.deleting.set(false);
    }
  }
}
