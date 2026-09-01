import type { PlannerRequest } from '../services/backend.service';
import type { PlannerState } from '../services/planner-state.service';

/** Maps the app's own PlannerState to the wire shape /api/plan expects --
 * the one place this mapping lives, so PlannerStateService.refreshPlan, the
 * share-link fetch, and the what-if compare fetch never drift into separate
 * copies of the same thing. */
export function toPlannerRequest(
  state: PlannerState,
  prompt = '',
  extra?: { recentReply?: string; turnIndex?: number },
): PlannerRequest {
  return {
    major: state.major,
    prompt,
    completed: state.completed,
    start_year: state.startYear,
    grad_years: state.gradYears,
    allow_summer: state.allowSummer,
    summer_unavailable: state.summerUnavailable,
    consumed_slot_ids: state.consumedSlotIds,
    math_placement_tier: state.mathPlacementTier,
    recent_reply: extra?.recentReply,
    turn_index: extra?.turnIndex,
    // state.additionalMajors[0] fills the backend's original second_major
    // field for backward compatibility; anything beyond that (a 3rd, 4th
    // major) goes through the newer additional_majors list.
    second_major: state.additionalMajors[0],
    additional_majors: state.additionalMajors.slice(1),
    minors: state.minors,
    max_credits: state.maxCreditsPerSemester,
  };
}
