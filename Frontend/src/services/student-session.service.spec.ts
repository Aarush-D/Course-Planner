import { signal, WritableSignal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PlannerState, PlannerStateService } from './planner-state.service';
import { SavedPlanMeta, StudentPlanService } from './student-plan.service';
import { StudentSessionService } from './student-session.service';
import { SupabaseService } from './supabase.service';

/** A private-field/method escape hatch for the handful of internals these
 * tests need to reach directly (userId, _startAutosave) -- deletePlan()'s
 * autosave-pause behavior can't be observed from the public API alone. */
type Peek = {
  userId: string | null;
  _startAutosave(): void;
};

function poke(service: StudentSessionService): Peek {
  return service as unknown as Peek;
}

function makePlannerState(overrides: Partial<PlannerState> = {}): PlannerState {
  return {
    major: 'CMPSC',
    catalogYear: undefined,
    completed: [],
    startYear: 2026,
    gradYears: 4,
    allowSummer: false,
    summerUnavailable: [],
    consumedSlotIds: [],
    mathPlacementTier: undefined,
    additionalMajors: [],
    minors: [],
    campus: 'University Park',
    undecided: false,
    scheduledCourseIds: [],
    maxCreditsPerSemester: undefined,
    wantedCourses: [],
    excludedCourses: [],
    genEdOverrides: {},
    pendingMajorChange: null,
    ...overrides,
  };
}

function makeMeta(id: string, name: string, updatedAt: string): SavedPlanMeta {
  return { id, name, updated_at: updatedAt };
}

/** Builds the service under test with fake studentPlan/planner dependencies
 * -- deletePlan()'s own doc comment explains exactly why the ordering
 * between them matters (a plan switch pairing the wrong id with the wrong
 * content), so these fakes are real enough to let a regression reproduce
 * that: `state` is a genuine writable signal (so the autosave effect can
 * react to it), and applyLoadedState/deletePlan/loadPlan/savePlan are
 * spies a test can script and inspect. */
function setup(opts: { userId: string | null; sessionUserId?: string | null } = { userId: 'user-1' }) {
  const stateSignal: WritableSignal<PlannerState> = signal(makePlannerState());

  const applyLoadedState = vi.fn(async (saved: PlannerState) => {
    stateSignal.set(saved);
  });
  // Deliberately NOT typed as `Pick<PlannerStateService, ...>` -- that would
  // widen each property back to a plain function type and hide the
  // `.mockImplementation`/`.mockResolvedValue` methods tests below need.
  const fakePlanner = {
    state: stateSignal,
    applyLoadedState,
    // Read by _isDirty() on the resume/sign-in load path; just the welcome
    // message means "untouched", so no confirm() dialog fires.
    chatMessages: signal([{ role: 'assistant' as const, text: 'welcome' }]),
    completeOnboarding: vi.fn(),
  };

  const deletePlan = vi.fn().mockResolvedValue(undefined);
  const loadPlan = vi.fn<(planId: string) => Promise<PlannerState | null>>();
  const savePlan = vi.fn().mockResolvedValue(undefined);
  const listPlans = vi.fn<(userId: string) => Promise<SavedPlanMeta[]>>().mockResolvedValue([]);
  const createPlan = vi.fn<(userId: string, name: string, state: PlannerState) => Promise<SavedPlanMeta>>();
  const fakeStudentPlan = {
    deletePlan,
    loadPlan,
    savePlan,
    listPlans,
    createPlan,
  };

  // tryResumeSavedPlan() awaits getSession() directly -- `sessionUserId`
  // (undefined = same as userId) is who that reports as signed in.
  const sessionUserId = opts.sessionUserId === undefined ? opts.userId : opts.sessionUserId;
  const fakeSupabase = {
    client: {
      auth: {
        getSession: vi.fn().mockResolvedValue({
          data: { session: sessionUserId ? { user: { id: sessionUserId } } : null },
        }),
      },
    },
  };

  TestBed.configureTestingModule({
    providers: [
      { provide: StudentPlanService, useValue: fakeStudentPlan },
      { provide: PlannerStateService, useValue: fakePlanner },
      { provide: SupabaseService, useValue: fakeSupabase },
    ],
  });

  const service = TestBed.inject(StudentSessionService);
  poke(service).userId = opts.userId;

  return { service, stateSignal, fakePlanner, fakeStudentPlan };
}

describe('StudentSessionService.tryResumeSavedPlan (M7/M9)', () => {
  it('creates a first plan when a signed-in student has none, and savingFirstPlan is only true meanwhile', async () => {
    const { service, fakeStudentPlan } = setup();
    fakeStudentPlan.listPlans.mockResolvedValue([]);
    let savingDuringCreate: boolean | undefined;
    fakeStudentPlan.createPlan.mockImplementation(async () => {
      savingDuringCreate = service.savingFirstPlan();
      return makeMeta('plan-new', 'My Plan', '2026-01-01T00:00:00Z');
    });

    await service.tryResumeSavedPlan();

    expect(fakeStudentPlan.createPlan).toHaveBeenCalledWith('user-1', 'My Plan', expect.anything());
    expect(savingDuringCreate).toBe(true);
    expect(service.savingFirstPlan()).toBe(false);
    expect(service.activePlanId()).toBe('plan-new');
    expect(service.savedPlans().map((p) => p.id)).toEqual(['plan-new']);
  });

  it('leaves savingFirstPlan false (not stuck true) when creating the first plan fails', async () => {
    const { service, fakeStudentPlan } = setup();
    fakeStudentPlan.listPlans.mockResolvedValue([]);
    fakeStudentPlan.createPlan.mockRejectedValue(new Error('network error'));

    await expect(service.tryResumeSavedPlan()).resolves.toBeUndefined();

    expect(service.savingFirstPlan()).toBe(false);
    expect(service.savedPlans()).toEqual([]);
    expect(service.activePlanId()).toBeNull();
  });

  it('marks onboarding complete once a saved plan is actually loaded', async () => {
    const { service, fakePlanner, fakeStudentPlan } = setup();
    const meta = makeMeta('plan-A', 'A', '2026-01-01T00:00:00Z');
    fakeStudentPlan.listPlans.mockResolvedValue([meta]);
    fakeStudentPlan.loadPlan.mockResolvedValue(makePlannerState({ major: 'MATH' }));

    await service.tryResumeSavedPlan();

    expect(fakePlanner.applyLoadedState).toHaveBeenCalledTimes(1);
    expect(fakePlanner.completeOnboarding).toHaveBeenCalledTimes(1);
    expect(service.activePlanId()).toBe('plan-A');
    expect(fakeStudentPlan.createPlan).not.toHaveBeenCalled();
  });

  it('does not mark onboarding complete when the saved plan fails to load', async () => {
    const { service, fakePlanner, fakeStudentPlan } = setup();
    fakeStudentPlan.listPlans.mockResolvedValue([makeMeta('plan-A', 'A', '2026-01-01T00:00:00Z')]);
    fakeStudentPlan.loadPlan.mockRejectedValue(new Error('network error'));

    await service.tryResumeSavedPlan();

    expect(fakePlanner.completeOnboarding).not.toHaveBeenCalled();
    expect(service.savingFirstPlan()).toBe(false);
  });

  it('is a no-op for a visitor with no session', async () => {
    const { service, fakeStudentPlan } = setup({ userId: null, sessionUserId: null });

    await service.tryResumeSavedPlan();

    expect(fakeStudentPlan.listPlans).not.toHaveBeenCalled();
    expect(fakeStudentPlan.createPlan).not.toHaveBeenCalled();
  });
});

describe('StudentSessionService.deletePlan', () => {
  it('loads and applies the replacement plan before activePlanId ever points at it', async () => {
    const { service, fakePlanner, fakeStudentPlan } = setup();
    const replacementState = makePlannerState({ major: 'MATH' });
    fakeStudentPlan.loadPlan.mockResolvedValue(replacementState);

    // Captured from inside the applyLoadedState spy itself -- if deletePlan
    // regressed to set activePlanId before this resolves (the exact bug
    // being guarded against), this would already read 'plan-B' here.
    let activePlanIdDuringApply: string | null | undefined;
    fakePlanner.applyLoadedState.mockImplementation(async (saved: PlannerState) => {
      activePlanIdDuringApply = service.activePlanId();
      fakePlanner.state.set(saved);
    });

    service.activePlanId.set('plan-A');
    service.savedPlans.set([
      makeMeta('plan-A', 'A', '2026-01-01T00:00:00Z'),
      makeMeta('plan-B', 'B', '2026-01-02T00:00:00Z'),
    ]);

    await service.deletePlan('plan-A');

    expect(fakeStudentPlan.loadPlan).toHaveBeenCalledWith('plan-B');
    expect(fakePlanner.applyLoadedState).toHaveBeenCalledWith(replacementState);
    expect(activePlanIdDuringApply).not.toBe('plan-B');
    expect(service.activePlanId()).toBe('plan-B');
  });

  it('sets activePlanId to null when the deleted plan was the student\'s last one', async () => {
    const { service, fakePlanner } = setup();
    service.activePlanId.set('plan-A');
    service.savedPlans.set([makeMeta('plan-A', 'A', '2026-01-01T00:00:00Z')]);

    await service.deletePlan('plan-A');

    expect(service.activePlanId()).toBeNull();
    expect(service.savedPlans()).toEqual([]);
    expect(fakePlanner.applyLoadedState).not.toHaveBeenCalled();
  });

  it('sets activePlanId to null (not the replacement id) when loading the replacement throws', async () => {
    const { service, fakePlanner, fakeStudentPlan } = setup();
    fakeStudentPlan.loadPlan.mockRejectedValue(new Error('network error'));

    service.activePlanId.set('plan-A');
    service.savedPlans.set([
      makeMeta('plan-A', 'A', '2026-01-01T00:00:00Z'),
      makeMeta('plan-B', 'B', '2026-01-02T00:00:00Z'),
    ]);

    await service.deletePlan('plan-A');

    expect(service.activePlanId()).toBeNull();
    expect(fakePlanner.applyLoadedState).not.toHaveBeenCalled();
  });

  it('sets activePlanId to null (not the replacement id) when loading the replacement resolves empty', async () => {
    const { service, fakePlanner, fakeStudentPlan } = setup();
    fakeStudentPlan.loadPlan.mockResolvedValue(null);

    service.activePlanId.set('plan-A');
    service.savedPlans.set([
      makeMeta('plan-A', 'A', '2026-01-01T00:00:00Z'),
      makeMeta('plan-B', 'B', '2026-01-02T00:00:00Z'),
    ]);

    await service.deletePlan('plan-A');

    expect(service.activePlanId()).toBeNull();
    expect(fakePlanner.applyLoadedState).not.toHaveBeenCalled();
  });

  it('does not touch activePlanId when the deleted plan was not the active one', async () => {
    const { service, fakePlanner, fakeStudentPlan } = setup();
    service.activePlanId.set('plan-A');
    service.savedPlans.set([
      makeMeta('plan-A', 'A', '2026-01-01T00:00:00Z'),
      makeMeta('plan-B', 'B', '2026-01-02T00:00:00Z'),
    ]);

    await service.deletePlan('plan-B');

    expect(service.activePlanId()).toBe('plan-A');
    expect(fakeStudentPlan.loadPlan).not.toHaveBeenCalled();
    expect(fakePlanner.applyLoadedState).not.toHaveBeenCalled();
  });

  describe('autosave pausing', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    it('pauses autosave for the whole transition -- a save already queued is discarded, and the resumed autosave only ever pairs the new plan id with the newly loaded content', async () => {
      const { service, stateSignal, fakeStudentPlan } = setup();
      const oldContent = makePlannerState({ major: 'OLD_ACTIVE_CONTENT' });
      const replacementContent = makePlannerState({ major: 'PLAN_B_REAL_CONTENT' });
      stateSignal.set(oldContent);
      fakeStudentPlan.loadPlan.mockResolvedValue(replacementContent);

      service.activePlanId.set('plan-A');
      service.savedPlans.set([
        makeMeta('plan-A', 'A', '2026-01-01T00:00:00Z'),
        makeMeta('plan-B', 'B', '2026-01-02T00:00:00Z'),
      ]);

      // Autosave is already live for this session, same as real usage --
      // deletePlan() itself is responsible for pausing/resuming it.
      poke(service)._startAutosave();
      TestBed.flushEffects(); // queues a save for (plan-A, oldContent)

      // Delete before that queued save's 1500ms debounce elapses. Pausing
      // must discard it outright, not just block future emissions.
      const deletion = service.deletePlan('plan-A');
      vi.advanceTimersByTime(1500);
      expect(fakeStudentPlan.savePlan).not.toHaveBeenCalled();

      await deletion;

      TestBed.flushEffects();
      vi.advanceTimersByTime(1500);

      expect(fakeStudentPlan.savePlan).toHaveBeenCalledTimes(1);
      expect(fakeStudentPlan.savePlan).toHaveBeenCalledWith('plan-B', replacementContent);
      // The mismatched pairing the bug produced: the deleted plan's stale
      // content saved under the replacement's id.
      expect(fakeStudentPlan.savePlan).not.toHaveBeenCalledWith('plan-B', oldContent);
      expect(fakeStudentPlan.savePlan).not.toHaveBeenCalledWith('plan-A', expect.anything());
    });
  });
});
