import { describe, expect, it } from 'vitest';
import { decodeShareToken, encodeShareToken } from './share-token.util';
import type { PlannerState } from '../services/planner-state.service';

describe('decodeShareToken (H1: links minted before newer PlannerState fields existed)', () => {
  it('fills in the newer fields so an old link decodes to a complete state', () => {
    // Exactly what an early share link carried -- none of the fields added
    // since (scheduledCourseIds, wantedCourses, excludedCourses,
    // genEdOverrides, maxCreditsPerSemester, pendingMajorChange).
    const legacy = {
      major: 'CMPSC',
      completed: ['CMPSC 131', 'MATH 140'],
      startYear: 2024,
      gradYears: 4,
      allowSummer: true,
      summerUnavailable: [],
      consumedSlotIds: [3],
      additionalMajors: [],
      minors: ['MATH'],
      campus: 'University Park',
      undecided: false,
    };
    const token = encodeShareToken(legacy as unknown as PlannerState);

    const decoded = decodeShareToken(token);

    expect(decoded.scheduledCourseIds).toEqual([]);
    expect(decoded.wantedCourses).toEqual([]);
    expect(decoded.excludedCourses).toEqual([]);
    expect(decoded.genEdOverrides).toEqual({});
    expect(decoded.pendingMajorChange).toBeNull();
    expect(decoded.maxCreditsPerSemester).toBeUndefined();
    // ...without touching anything the link actually carried.
    expect(decoded.completed).toEqual(['CMPSC 131', 'MATH 140']);
    expect(decoded.minors).toEqual(['MATH']);
    expect(decoded.consumedSlotIds).toEqual([3]);
    expect(decoded.allowSummer).toBe(true);
  });

  it('still rejects a token that is not a plan at all', () => {
    const token = encodeShareToken({ hello: 'world' } as unknown as PlannerState);
    expect(() => decodeShareToken(token)).toThrow('This link is broken or out of date.');
    expect(() => decodeShareToken('not-base64-json')).toThrow('This link is broken or out of date.');
  });
});
