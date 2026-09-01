import { EffectRef, Injectable, Injector, computed, effect, inject, signal } from '@angular/core';
import { Subject, Subscription, debounceTime } from 'rxjs';
import { PlannerState, PlannerStateService } from './planner-state.service';
import { SavedPlanMeta, StudentPlanService } from './student-plan.service';
import { SupabaseService } from './supabase.service';

interface QueuedSave {
  planId: string;
  state: PlannerState;
}

/**
 * Orchestrates optional student persistence -- load a saved plan on
 * sign-in/resume, autosave on change, switch between a student's saved
 * plans (see migration 0008 -- a student can have more than one), stop on
 * sign-out. PlannerStateService itself stays completely unaware Supabase
 * exists; this is the only place that bridges the two, matching the same
 * separation ReviewRequestService already keeps for the advisor
 * workspace.
 */
@Injectable({ providedIn: 'root' })
export class StudentSessionService {
  private readonly supabase = inject(SupabaseService);
  private readonly studentPlan = inject(StudentPlanService);
  private readonly planner = inject(PlannerStateService);
  private readonly injector = inject(Injector);

  private autosaveEffect: EffectRef | null = null;
  private readonly saveRequested = new Subject<QueuedSave>();
  private saveSub: Subscription | null = null;
  private userId: string | null = null;

  /** The plan currently loaded/autosaving, and the signed-in student's
   * full saved-plan list -- both empty until a session with at least one
   * saved plan exists. Exposed for the plan-switcher UI (see
   * your-plan-page.component). */
  readonly activePlanId = signal<string | null>(null);
  readonly savedPlans = signal<SavedPlanMeta[]>([]);
  readonly activePlanName = computed(
    () => this.savedPlans().find((p) => p.id === this.activePlanId())?.name ?? null,
  );

  /** Called once on app startup (see app.component.ts) -- a no-op for the
   * ~100% of visitors with no student account. Awaits getSession()
   * directly, not the reactive session signal, for the same
   * fresh-page-load race advisor-auth.guard.ts already avoids. */
  async tryResumeSavedPlan(): Promise<void> {
    const { data } = await this.supabase.client.auth.getSession();
    const userId = data.session?.user.id;
    if (!userId) return;
    this.userId = userId;
    const plans = await this._refreshPlanList(userId);
    if (plans.length) await this._loadAndApply(plans[0]);
    this._startAutosave();
  }

  /** Called from the login page right after a successful sign-in/sign-up.
   * A new account has no saved plan to conflict with -- whatever's
   * currently in memory becomes the first cloud snapshot rather than being
   * discarded. An existing account goes through the same load-and-maybe-
   * confirm path as the startup resume above (falling back to the same
   * "create the first one" behavior if it somehow has none). Swallows a
   * save/load failure here rather than propagating it -- the auth step
   * itself already succeeded by the time this is called, and persistence
   * is this feature's enhancement on top of that, not a reason to block
   * the student from actually being signed in (autosave picks up the next
   * change regardless). */
  async onSignedIn(userId: string, isNewAccount: boolean): Promise<void> {
    this.userId = userId;
    try {
      const plans = isNewAccount ? [] : await this._refreshPlanList(userId);
      if (plans.length) {
        await this._loadAndApply(plans[0]);
      } else {
        const id = await this.studentPlan.createPlan(userId, 'My Plan', this.planner.state());
        this.activePlanId.set(id);
        await this._refreshPlanList(userId);
      }
    } catch {
      // See doc comment above -- intentionally not rethrown.
    }
    this._startAutosave();
  }

  /** Called right before sign-out -- leaves whatever's currently loaded in
   * memory (matches the app's ephemeral-by-default feel), just stops
   * pushing further changes to a session that's about to end. */
  stopAutosave(): void {
    this.autosaveEffect?.destroy();
    this.autosaveEffect = null;
    this.saveSub?.unsubscribe();
    this.saveSub = null;
    this.userId = null;
    this.activePlanId.set(null);
    this.savedPlans.set([]);
  }

  /** Switches which saved plan is live/autosaving -- loads its content
   * into the live planner state immediately, no dirty-check confirm
   * (unlike the sign-in/resume load below): the student explicitly picked
   * this from their own plan list, so there's no ambiguity about intent
   * the way "you already started something in this browser" has. Flushes
   * any pending autosave for the plan being switched AWAY from first --
   * without this, an edit made in the last <1.5s before switching could
   * get silently dropped (the debounced save queue only keeps the latest
   * pending write, and switching changes what "latest" should target). */
  async switchToPlan(planId: string): Promise<void> {
    const currentId = this.activePlanId();
    if (currentId && currentId !== planId) {
      try {
        await this.studentPlan.savePlan(currentId, this.planner.state());
      } catch {
        // Best-effort flush -- proceed with the switch regardless.
      }
    }
    const saved = await this.studentPlan.loadPlan(planId);
    if (!saved) return;
    this.planner.state.set(saved);
    this.activePlanId.set(planId);
  }

  /** Saves the CURRENT live state as a brand-new plan and switches to it
   * -- "save my in-progress changes as a separate plan" rather than
   * overwriting whichever one was active. */
  async saveAsNewPlan(name: string): Promise<void> {
    if (!this.userId) return;
    const id = await this.studentPlan.createPlan(this.userId, name, this.planner.state());
    this.activePlanId.set(id);
    await this._refreshPlanList(this.userId);
  }

  async renamePlan(planId: string, name: string): Promise<void> {
    await this.studentPlan.renamePlan(planId, name);
    if (this.userId) await this._refreshPlanList(this.userId);
  }

  /** Deletes a plan; if it was the active one, switches to whichever plan
   * is now most recently updated, or clears activePlanId if that was the
   * student's last one. The live in-memory state is left untouched either
   * way (matches this app's ephemeral-by-default feel) -- only the next
   * autosave's target changes. */
  async deletePlan(planId: string): Promise<void> {
    await this.studentPlan.deletePlan(planId);
    if (!this.userId) return;
    const plans = await this._refreshPlanList(this.userId);
    if (this.activePlanId() === planId) {
      this.activePlanId.set(plans[0]?.id ?? null);
    }
  }

  private async _refreshPlanList(userId: string): Promise<SavedPlanMeta[]> {
    try {
      const plans = await this.studentPlan.listPlans(userId);
      this.savedPlans.set(plans);
      return plans;
    } catch {
      return [];
    }
  }

  private async _loadAndApply(meta: SavedPlanMeta): Promise<void> {
    let saved: PlannerState | null;
    try {
      saved = await this.studentPlan.loadPlan(meta.id);
    } catch {
      return; // a failed fetch shouldn't block the rest of the app from loading
    }
    if (!saved) return;
    if (this._isDirty()) {
      const proceed = window.confirm(
        "You've already started a plan in this browser. Load your saved plan instead? " +
          'This replaces what\'s currently shown here.'
      );
      if (!proceed) {
        // Keep what's in the browser -- but don't let autosave silently
        // overwrite the plan they just declined to load. Save the
        // in-browser content as its own new plan instead, now that
        // multiple plans are actually supported.
        if (this.userId) {
          try {
            const id = await this.studentPlan.createPlan(this.userId, 'My Plan', this.planner.state());
            this.activePlanId.set(id);
            await this._refreshPlanList(this.userId);
          } catch {
            // Best-effort -- autosave just has nothing to target yet.
          }
        }
        return;
      }
    }
    this.planner.state.set(saved);
    this.activePlanId.set(meta.id);
  }

  /** "Untouched defaults" proxy: no completed courses and no real
   * conversation yet (just the welcome message). Not a perfect signal, but
   * good enough to decide whether silently loading is safe or whether to
   * ask first. */
  private _isDirty(): boolean {
    return this.planner.state().completed.length > 0 || this.planner.chatMessages().length > 1;
  }

  private _startAutosave(): void {
    if (this.autosaveEffect) return;
    this.saveSub = this.saveRequested.pipe(debounceTime(1500)).subscribe(({ planId, state }) => {
      this.studentPlan.savePlan(planId, state).catch(() => {});
    });
    this.autosaveEffect = effect(
      () => {
        const state = this.planner.state();
        const planId = this.activePlanId();
        if (planId) this.saveRequested.next({ planId, state });
      },
      { injector: this.injector }
    );
  }
}
