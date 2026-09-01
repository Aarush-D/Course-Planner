import { Injectable, inject } from '@angular/core';
import type { PlannerState } from './planner-state.service';
import { SupabaseService } from './supabase.service';

/** Persistence for an optional, logged-in student's plan -- separate from
 * PlannerStateService (which owns the live, client-side plan and stays
 * completely unaware Supabase exists) and from ReviewRequestService (a
 * different subsystem entirely: a one-time snapshot shared with an
 * advisor, not a student's own ongoing save). See
 * student-session.service.ts for the orchestration that ties this to the
 * live plan (load on sign-in, autosave on change). */
@Injectable({ providedIn: 'root' })
export class StudentPlanService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  async loadPlan(userId: string): Promise<PlannerState | null> {
    const { data, error } = await this.client
      .from('student_plans')
      .select('plan_state')
      .eq('user_id', userId)
      .maybeSingle();
    if (error) throw error;
    return (data?.['plan_state'] as PlannerState | undefined) ?? null;
  }

  async savePlan(userId: string, state: PlannerState): Promise<void> {
    const { error } = await this.client
      .from('student_plans')
      .upsert({ user_id: userId, plan_state: state, updated_at: new Date().toISOString() });
    if (error) throw error;
  }
}
