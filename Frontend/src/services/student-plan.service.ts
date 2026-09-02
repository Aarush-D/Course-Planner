import { Injectable, inject } from '@angular/core';
import type { PlannerState } from './planner-state.service';
import { SupabaseService } from './supabase.service';

export interface SavedPlanMeta {
  id: string;
  name: string;
  updated_at: string;
}

/** Persistence for an optional, logged-in student's plans -- separate from
 * PlannerStateService (which owns the live, client-side plan and stays
 * completely unaware Supabase exists) and from ReviewRequestService (a
 * different subsystem entirely: a one-time snapshot shared with an
 * advisor, not a student's own ongoing save). See
 * student-session.service.ts for the orchestration that ties this to the
 * live plan (load on sign-in, autosave on change, switching between
 * saved plans).
 *
 * A student can save more than one named plan (see migration 0008) --
 * every method here operates on a specific plan's row id, not the user
 * directly, except listPlans (the one place a student's whole set of
 * plans is relevant) and createPlan (which doesn't have an id yet). */
@Injectable({ providedIn: 'root' })
export class StudentPlanService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  /** Most-recently-updated first, so callers that just want "the" plan
   * (the common case: a student with exactly one) can take the first
   * entry without a separate query. */
  async listPlans(userId: string): Promise<SavedPlanMeta[]> {
    const { data, error } = await this.client
      .from('student_plans')
      .select('id, name, updated_at')
      .eq('user_id', userId)
      .order('updated_at', { ascending: false });
    if (error) throw error;
    return (data as SavedPlanMeta[]) ?? [];
  }

  async loadPlan(planId: string): Promise<PlannerState | null> {
    const { data, error } = await this.client
      .from('student_plans')
      .select('plan_state')
      .eq('id', planId)
      .maybeSingle();
    if (error) throw error;
    return (data?.['plan_state'] as PlannerState | undefined) ?? null;
  }

  async savePlan(planId: string, state: PlannerState): Promise<void> {
    const { error } = await this.client
      .from('student_plans')
      .update({ plan_state: state, updated_at: new Date().toISOString() })
      .eq('id', planId);
    if (error) throw error;
  }

  /** Returns the new row's id/name/updated_at (not just the id), so the
   * caller can both make it the active plan right away AND splice it
   * straight into an already-held plan list -- no need to re-SELECT the
   * whole list just to learn what this insert already returned. */
  async createPlan(userId: string, name: string, state: PlannerState): Promise<SavedPlanMeta> {
    const { data, error } = await this.client
      .from('student_plans')
      .insert({ user_id: userId, name: name.trim() || 'My Plan', plan_state: state })
      .select('id, name, updated_at')
      .single();
    if (error) throw error;
    return data as SavedPlanMeta;
  }

  /** Returns the updated row for the same reason createPlan does -- the
   * caller can patch its held list in place instead of re-fetching it. */
  async renamePlan(planId: string, name: string): Promise<SavedPlanMeta> {
    const { data, error } = await this.client
      .from('student_plans')
      .update({ name: name.trim() || 'My Plan' })
      .eq('id', planId)
      .select('id, name, updated_at')
      .single();
    if (error) throw error;
    return data as SavedPlanMeta;
  }

  async deletePlan(planId: string): Promise<void> {
    const { error } = await this.client.from('student_plans').delete().eq('id', planId);
    if (error) throw error;
  }
}
