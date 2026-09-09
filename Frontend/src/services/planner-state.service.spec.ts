import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CoursePlan } from '../models/course-plan.model';
import { BackendService } from './backend.service';
import { PlannerState, PlannerStateService } from './planner-state.service';
import { ToastService } from './toast.service';

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

/** The minimum /api/plan response refreshPlan() reads. Everything else on
 * CoursePlan is only consumed by page components, not the service. */
function makePlan(overrides: Partial<CoursePlan> = {}): CoursePlan {
  return {
    major: 'CMPSC',
    dept: 'CMPSC',
    completed: [],
    eligible: [],
    graph: { nodes: [], edges: [] },
    rag_response: 'Here is your plan.',
    recommendations: [],
    flowchart: [],
    ...overrides,
  } as CoursePlan;
}

/** A promise whose settlement the test controls -- the in-flight window
 * the M1 tests below mutate state inside of. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function setup() {
  const plan = vi.fn<(req: unknown) => Promise<CoursePlan>>();
  const campuses = vi.fn<() => Promise<{ campuses: string[]; default: string }>>();
  const degreePlans = vi.fn().mockResolvedValue([]);
  const minorPlans = vi.fn().mockResolvedValue([]);
  const fakeBackend = { plan, campuses, degreePlans, minorPlans };

  TestBed.configureTestingModule({
    providers: [
      { provide: BackendService, useValue: fakeBackend },
      { provide: ToastService, useValue: { show: vi.fn() } },
    ],
  });

  const service = TestBed.inject(PlannerStateService);
  return { service, fakeBackend };
}

describe('PlannerStateService.applyLoadedState (H1: saved plans that predate newer fields)', () => {
  it('fills in every missing array/object field so refreshPlan can run against the loaded state', async () => {
    const { service, fakeBackend } = setup();
    // A row written before scheduledCourseIds / wantedCourses /
    // excludedCourses / genEdOverrides / maxCreditsPerSemester /
    // pendingMajorChange existed -- only the original core fields.
    const legacy = {
      major: 'CMPSC',
      completed: ['CMPSC 131'],
      startYear: 2024,
      gradYears: 4,
      allowSummer: false,
      summerUnavailable: [],
      consumedSlotIds: [],
      additionalMajors: [],
      minors: [],
      campus: 'University Park',
      undecided: false,
    } as unknown as PlannerState;
    fakeBackend.plan.mockResolvedValue(makePlan({ completed: ['CMPSC 131'] }));

    await expect(service.applyLoadedState(legacy)).resolves.toBeUndefined();

    const st = service.state();
    expect(Array.isArray(st.scheduledCourseIds)).toBe(true);
    expect(Array.isArray(st.wantedCourses)).toBe(true);
    expect(Array.isArray(st.excludedCourses)).toBe(true);
    expect(Array.isArray(st.completed)).toBe(true);
    expect(Array.isArray(st.summerUnavailable)).toBe(true);
    expect(Array.isArray(st.consumedSlotIds)).toBe(true);
    expect(Array.isArray(st.additionalMajors)).toBe(true);
    expect(Array.isArray(st.minors)).toBe(true);
    expect(st.genEdOverrides).toEqual({});
    expect(st.pendingMajorChange).toBeNull();
    expect(st.maxCreditsPerSemester).toBeUndefined();
    // What the row DID carry is kept verbatim, and the plan was actually
    // re-derived (the merge inside refreshPlan didn't throw).
    expect(st.major).toBe('CMPSC');
    expect(st.completed).toEqual(['CMPSC 131']);
    expect(st.startYear).toBe(2024);
    expect(fakeBackend.plan).toHaveBeenCalledTimes(1);
    expect(service.coursePlan()).not.toBeNull();
  });

  it('repairs a field that is present but not an array (e.g. a null written by an older build)', async () => {
    const { service, fakeBackend } = setup();
    const corrupt = makePlannerState({
      scheduledCourseIds: null as unknown as string[],
      genEdOverrides: null as unknown as Record<string, string>,
    });
    fakeBackend.plan.mockResolvedValue(makePlan());

    await expect(service.applyLoadedState(corrupt)).resolves.toBeUndefined();

    expect(service.state().scheduledCourseIds).toEqual([]);
    expect(service.state().genEdOverrides).toEqual({});
  });

  it('does not re-plan for an undecided saved state, but still normalizes it', async () => {
    const { service, fakeBackend } = setup();
    const legacyUndecided = { major: '', completed: [], undecided: true } as unknown as PlannerState;

    await service.applyLoadedState(legacyUndecided);

    expect(fakeBackend.plan).not.toHaveBeenCalled();
    expect(service.coursePlan()).toBeNull();
    expect(service.state().undecided).toBe(true);
    expect(service.state().scheduledCourseIds).toEqual([]);
  });
});

describe('PlannerStateService.refreshPlan (M1: state changed while a request is in flight)', () => {
  it('keeps a toggleScheduled() made while the request was pending', async () => {
    const { service, fakeBackend } = setup();
    service.state.set(makePlannerState());
    const response = deferred<CoursePlan>();
    fakeBackend.plan.mockReturnValue(response.promise);

    // onProgramsChanged is a public path straight into refreshPlan('').
    const inFlight = service.onProgramsChanged([], []);
    service.toggleScheduled('CMPSC 132');
    expect(service.state().scheduledCourseIds).toEqual(['CMPSC 132']);

    response.resolve(makePlan({ completed: [] }));
    await inFlight;

    expect(service.state().scheduledCourseIds).toEqual(['CMPSC 132']);
    expect(service.coursePlan()).not.toBeNull();
  });

  it('still drops a scheduled course the backend reports as completed', async () => {
    const { service, fakeBackend } = setup();
    service.state.set(makePlannerState({ scheduledCourseIds: ['CMPSC 131'] }));
    const response = deferred<CoursePlan>();
    fakeBackend.plan.mockReturnValue(response.promise);

    const inFlight = service.onProgramsChanged([], []);
    service.toggleScheduled('CMPSC 132');
    response.resolve(makePlan({ completed: ['CMPSC 131'] }));
    await inFlight;

    expect(service.state().completed).toEqual(['CMPSC 131']);
    expect(service.state().scheduledCourseIds).toEqual(['CMPSC 132']);
  });

  it('keeps setUndecided(true) made while pending, and leaves coursePlan null', async () => {
    const { service, fakeBackend } = setup();
    service.state.set(makePlannerState());
    const response = deferred<CoursePlan>();
    fakeBackend.plan.mockReturnValue(response.promise);

    const inFlight = service.onProgramsChanged([], []);
    service.setUndecided(true);
    // The backend echoes the `undecided: false` this request sent -- an
    // unchanged echo must not undo the student's newer choice.
    response.resolve(makePlan({ state: { dept: 'CMPSC', completed: [], undecided: false } }));
    await inFlight;

    expect(service.state().undecided).toBe(true);
    expect(service.coursePlan()).toBeNull();
  });

  it('keeps a campus changed while pending when the backend merely echoed the old one', async () => {
    const { service, fakeBackend } = setup();
    service.state.set(makePlannerState({ campus: 'University Park' }));
    const response = deferred<CoursePlan>();
    fakeBackend.plan.mockReturnValue(response.promise);

    const inFlight = service.onProgramsChanged([], []);
    service.state.update((s) => ({ ...s, campus: 'Penn State Behrend' }));
    response.resolve(makePlan({ state: { dept: 'CMPSC', completed: [], campus: 'University Park' } }));
    await inFlight;

    expect(service.state().campus).toBe('Penn State Behrend');
  });

  it('still applies a value the backend actually changed this turn (chat-corrected start year)', async () => {
    const { service, fakeBackend } = setup();
    service.state.set(makePlannerState({ startYear: 2026 }));
    fakeBackend.plan.mockResolvedValue(
      makePlan({ state: { dept: 'CMPSC', completed: [], startYear: 2022 } }),
    );

    await service.onProgramsChanged([], []);

    expect(service.state().startYear).toBe(2022);
  });

  it('honors an explicit pendingMajorChange: null from the backend over the live value', async () => {
    const { service, fakeBackend } = setup();
    const pending = { kind: 'switch', major: 'MATH' } as unknown as PlannerState['pendingMajorChange'];
    service.state.set(makePlannerState({ pendingMajorChange: pending }));
    fakeBackend.plan.mockResolvedValue(
      makePlan({ state: { dept: 'CMPSC', completed: [], pendingMajorChange: null } }),
    );

    await service.onProgramsChanged([], []);

    expect(service.state().pendingMajorChange).toBeNull();
  });
});

describe('PlannerStateService.init (M10: cold-backend race)', () => {
  it('does not overwrite a campus/major the student picked while campuses() was still loading', async () => {
    const { service, fakeBackend } = setup();
    const campusesResponse = deferred<{ campuses: string[]; default: string }>();
    fakeBackend.campuses.mockReturnValue(campusesResponse.promise);
    fakeBackend.degreePlans.mockResolvedValue([{ major: 'CMPSC', title: 'Computer Science' }]);

    const init = service.init();
    // Meanwhile the student picked a Behrend demo profile: state has a real
    // major and campus, and that campus's plan lists are already loaded.
    service.state.set(makePlannerState({ major: 'MEBH', campus: 'Penn State Behrend' }));
    service.degreePlans.set([{ major: 'MEBH', title: 'Mechanical Engineering' } as never]);

    campusesResponse.resolve({ campuses: ['University Park', 'Penn State Behrend'], default: 'University Park' });
    await init;

    expect(service.campuses()).toEqual(['University Park', 'Penn State Behrend']);
    expect(service.state().campus).toBe('Penn State Behrend');
    expect(service.state().major).toBe('MEBH');
    // Behrend's already-loaded lists weren't clobbered with the default
    // campus's, and no major fallback ran against them.
    expect(service.degreePlans().map((p) => p.major)).toEqual(['MEBH']);
    expect(fakeBackend.degreePlans).not.toHaveBeenCalled();
  });

  it('applies the default campus and loads its plans on a normal, untouched boot', async () => {
    const { service, fakeBackend } = setup();
    fakeBackend.campuses.mockResolvedValue({ campuses: ['University Park'], default: 'University Park' });
    fakeBackend.degreePlans.mockResolvedValue([{ major: 'CMPSC', title: 'Computer Science' }]);

    await service.init();

    expect(service.state().campus).toBe('University Park');
    expect(service.state().major).toBe(''); // still blank -- no silent default
    expect(fakeBackend.degreePlans).toHaveBeenCalledWith('University Park');
    expect(service.degreePlans()).toHaveLength(1);
  });
});

describe('PlannerStateService chat transcript (L6)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('records an identical reply again when it answers a NEW typed prompt', async () => {
    const { service, fakeBackend } = setup();
    service.state.set(makePlannerState());
    fakeBackend.plan.mockResolvedValue(makePlan({ rag_response: 'Same answer.' }));

    await service.onPromptSubmitted({ prompt: 'what next?' });
    await service.onPromptSubmitted({ prompt: 'and after that?' });

    const assistantReplies = service.chatMessages().filter((m) => m.role === 'assistant' && m.text === 'Same answer.');
    expect(assistantReplies).toHaveLength(2);
  });

  it('still dedupes an identical reply on a settings-only refresh (empty prompt)', async () => {
    const { service, fakeBackend } = setup();
    service.state.set(makePlannerState());
    fakeBackend.plan.mockResolvedValue(makePlan({ rag_response: 'Same answer.' }));

    await service.onPromptSubmitted({ prompt: 'what next?' });
    await service.onProgramsChanged([], []);

    const assistantReplies = service.chatMessages().filter((m) => m.role === 'assistant' && m.text === 'Same answer.');
    expect(assistantReplies).toHaveLength(1);
  });

  it('resetToDefault() clears a queued pendingPrompt', () => {
    const { service } = setup();
    service.openChatWithPrompt('I took CMPSC 131');
    expect(service.pendingPrompt()).toBe('I took CMPSC 131');

    service.resetToDefault();

    expect(service.pendingPrompt()).toBeUndefined();
  });
});
