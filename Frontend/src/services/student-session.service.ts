import { EffectRef, Injectable, Injector, effect, inject } from '@angular/core';
import { Subject, Subscription, debounceTime } from 'rxjs';
import { PlannerState, PlannerStateService } from './planner-state.service';
import { StudentPlanService } from './student-plan.service';
import { SupabaseService } from './supabase.service';

/**
 * Orchestrates optional student persistence -- load a saved plan on
 * sign-in/resume, autosave on change, stop on sign-out. PlannerStateService
 * itself stays completely unaware Supabase exists; this is the only place
 * that bridges the two, matching the same separation ReviewRequestService
 * already keeps for the advisor workspace.
 */
@Injectable({ providedIn: 'root' })
export class StudentSessionService {
  private readonly supabase = inject(SupabaseService);
  private readonly studentPlan = inject(StudentPlanService);
  private readonly planner = inject(PlannerStateService);
  private readonly injector = inject(Injector);

  private autosaveEffect: EffectRef | null = null;
  private readonly saveRequested = new Subject<PlannerState>();
  private saveSub: Subscription | null = null;

  /** Called once on app startup (see app.component.ts) -- a no-op for the
   * ~100% of visitors with no student account. Awaits getSession()
   * directly, not the reactive session signal, for the same
   * fresh-page-load race advisor-auth.guard.ts already avoids. */
  async tryResumeSavedPlan(): Promise<void> {
    const { data } = await this.supabase.client.auth.getSession();
    const userId = data.session?.user.id;
    if (!userId) return;
    await this._loadAndApply(userId);
    this._startAutosave(userId);
  }

  /** Called from the login page right after a successful sign-in/sign-up.
   * A new account has no saved plan to conflict with -- whatever's
   * currently in memory becomes the first cloud snapshot rather than being
   * discarded. An existing account goes through the same load-and-maybe-
   * confirm path as the startup resume above. Swallows a save/load failure
   * here rather than propagating it -- the auth step itself already
   * succeeded by the time this is called, and persistence is this
   * feature's enhancement on top of that, not a reason to block the
   * student from actually being signed in (autosave picks up the next
   * change regardless). */
  async onSignedIn(userId: string, isNewAccount: boolean): Promise<void> {
    try {
      if (isNewAccount) {
        await this.studentPlan.savePlan(userId, this.planner.state());
      } else {
        await this._loadAndApply(userId);
      }
    } catch {
      // See doc comment above -- intentionally not rethrown.
    }
    this._startAutosave(userId);
  }

  /** Called right before sign-out -- leaves whatever's currently loaded in
   * memory (matches the app's ephemeral-by-default feel), just stops
   * pushing further changes to a session that's about to end. */
  stopAutosave(): void {
    this.autosaveEffect?.destroy();
    this.autosaveEffect = null;
    this.saveSub?.unsubscribe();
    this.saveSub = null;
  }

  private async _loadAndApply(userId: string): Promise<void> {
    let saved: PlannerState | null;
    try {
      saved = await this.studentPlan.loadPlan(userId);
    } catch {
      return; // a failed fetch shouldn't block the rest of the app from loading
    }
    if (!saved) return;
    if (this._isDirty()) {
      const proceed = window.confirm(
        "You've already started a plan in this browser. Load your saved plan instead? " +
          'This replaces what\'s currently shown here.'
      );
      if (!proceed) return;
    }
    this.planner.state.set(saved);
  }

  /** "Untouched defaults" proxy: no completed courses and no real
   * conversation yet (just the welcome message). Not a perfect signal, but
   * good enough to decide whether silently loading is safe or whether to
   * ask first. */
  private _isDirty(): boolean {
    return this.planner.state().completed.length > 0 || this.planner.chatMessages().length > 1;
  }

  private _startAutosave(userId: string): void {
    if (this.autosaveEffect) return;
    this.saveSub = this.saveRequested.pipe(debounceTime(1500)).subscribe((state) => {
      this.studentPlan.savePlan(userId, state).catch(() => {});
    });
    this.autosaveEffect = effect(
      () => {
        const state = this.planner.state();
        this.saveRequested.next(state);
      },
      { injector: this.injector }
    );
  }
}
