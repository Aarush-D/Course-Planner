import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { PlannerState } from '../../services/planner-state.service';
import { ReviewRequestService } from '../../services/review-request.service';
import { SupabaseService } from '../../services/supabase.service';
import { ReviewRequestRow } from '../../services/supabase.service';

@Component({
  selector: 'app-advisor-dashboard-page',
  standalone: true,
  templateUrl: './advisor-dashboard-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe],
})
export class AdvisorDashboardPageComponent implements OnInit {
  private readonly reviewRequests = inject(ReviewRequestService);
  private readonly supabase = inject(SupabaseService);
  private readonly router = inject(Router);

  requests = signal<ReviewRequestRow[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);

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
}
