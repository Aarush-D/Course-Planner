import type { PlannerState } from '../services/planner-state.service';

/** The blank slate a fresh, never-used visitor sees. Lives here (not on
 * PlannerStateService) so the non-service code paths that ingest an
 * EXTERNAL PlannerState -- decodeShareToken() for old share links,
 * StudentPlanService.loadPlan() rows saved before newer fields existed --
 * can fill in whatever that older snapshot is missing without a circular
 * import back into the service. PlannerStateService._defaultState()
 * delegates here rather than keeping its own copy. */
export function defaultPlannerState(): PlannerState {
  return {
    // Genuinely blank -- a fresh visitor hasn't picked anything yet, and
    // this used to silently read 'CMPSC' here, which meant "Get started"
    // (or a chat message before Setup was ever touched) built a real
    // Computer Science plan for a student who never chose a major. Setup's
    // "Get started" validation and onPromptSubmitted's guard both exist to
    // catch this blank value before it reaches the backend.
    major: '',
    catalogYear: undefined,
    completed: [],
    startYear: new Date().getFullYear(),
    gradYears: 4,
    allowSummer: false,
    summerUnavailable: [],
    consumedSlotIds: [],
    additionalMajors: [],
    minors: [],
    campus: 'University Park',
    undecided: false,
    scheduledCourseIds: [],
    wantedCourses: [],
    excludedCourses: [],
    genEdOverrides: {},
    pendingMajorChange: null,
  };
}

const ARRAY_FIELDS = [
  'completed',
  'summerUnavailable',
  'consumedSlotIds',
  'additionalMajors',
  'minors',
  'scheduledCourseIds',
  'wantedCourses',
  'excludedCourses',
] as const satisfies readonly (keyof PlannerState)[];

/** Fills in every field an older PlannerState snapshot may lack. Fields
 * were added to PlannerState over time (scheduledCourseIds, wantedCourses,
 * excludedCourses, genEdOverrides, maxCreditsPerSemester,
 * pendingMajorChange, ...) and a plan saved -- or a share link minted --
 * before one of them existed simply has no such key. Loading that row raw
 * used to throw inside refreshPlan() (`st.scheduledCourseIds.filter` on
 * undefined) and, since autosave only ever writes what's in memory, the
 * account stayed stuck on every subsequent load. Every array-typed field
 * is guaranteed to come out an array, genEdOverrides an object, and
 * pendingMajorChange `null` rather than undefined; everything the snapshot
 * DOES carry is kept verbatim. */
export function normalizePlannerState(saved: Partial<PlannerState> | null | undefined): PlannerState {
  const merged: PlannerState = { ...defaultPlannerState(), ...(saved ?? {}) };
  for (const key of ARRAY_FIELDS) {
    if (!Array.isArray(merged[key])) (merged as Record<string, unknown>)[key] = [];
  }
  if (!merged.genEdOverrides || typeof merged.genEdOverrides !== 'object' || Array.isArray(merged.genEdOverrides)) {
    merged.genEdOverrides = {};
  }
  if (merged.pendingMajorChange === undefined) merged.pendingMajorChange = null;
  if (typeof merged.undecided !== 'boolean') merged.undecided = false;
  return merged;
}
