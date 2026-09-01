import { Injectable, inject, signal } from '@angular/core';
import { normalizeCourseCode } from '../utils/course-code.util';
import { CourseRatingRow, CourseRatingSummaryRow, SupabaseService } from './supabase.service';

const RATED_COURSES_KEY = 'rated-courses';

/**
 * Anonymous, unauthenticated course ratings -- kept separate from the
 * advisor workspace (review-request.service.ts) and, deliberately, from
 * any student login/session: a rating carries no identity of any kind,
 * regardless of whether the submitting browser happens to be signed in
 * elsewhere in the app. See supabase/migrations/0004_course_ratings.sql
 * for the RLS shape (public read, scoped-but-anonymous write, no RPC
 * needed since submission doesn't need to echo back the new row's id).
 */
@Injectable({ providedIn: 'root' })
export class CourseRatingService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  /** Which course codes this browser has already rated -- a soft,
   * client-side-only guard (same trust boundary as everything else in this
   * app: clearing storage or switching browsers lets someone rate again).
   * Not tamper-proof by design, not a gap. */
  readonly ratedCourses = signal<string[]>(this._readRatedCourses());

  hasRated(courseCode: string): boolean {
    return this.ratedCourses().includes(normalizeCourseCode(courseCode));
  }

  /** One batched query for a whole list of cards, never N+1. */
  async getSummaries(courseCodes: string[]): Promise<Map<string, CourseRatingSummaryRow>> {
    const codes = [...new Set(courseCodes.map(normalizeCourseCode))].filter(Boolean);
    if (!codes.length) return new Map();
    const { data, error } = await this.client
      .from('course_rating_summary')
      .select('*')
      .in('course_code', codes);
    if (error) throw error;
    const map = new Map<string, CourseRatingSummaryRow>();
    for (const row of (data as CourseRatingSummaryRow[]) ?? []) {
      map.set(row.course_code, row);
    }
    return map;
  }

  /** Individual reviews for one course, newest first, capped at 50 -- this
   * is a "see what people wrote" list for a single card's modal, not a
   * paginated feed. course_ratings has a plain public SELECT policy (see
   * migration 0004 -- ratings are meant to be publicly readable, that's
   * the whole point), so this is a direct table read, no RPC needed. */
  async getReviews(courseCode: string): Promise<CourseRatingRow[]> {
    const code = normalizeCourseCode(courseCode);
    const { data, error } = await this.client
      .from('course_ratings')
      .select('*')
      .eq('course_code', code)
      .order('created_at', { ascending: false })
      .limit(50);
    if (error) throw error;
    return (data as CourseRatingRow[]) ?? [];
  }

  async submitRating(courseCode: string, rating: number, reviewBody?: string): Promise<void> {
    const code = normalizeCourseCode(courseCode);
    const { error } = await this.client.from('course_ratings').insert({
      course_code: code,
      rating,
      review_body: reviewBody?.trim() || null,
    });
    if (error) throw error;
    this._markRated(code);
  }

  private _markRated(courseCode: string) {
    const next = [...new Set([...this.ratedCourses(), courseCode])];
    this.ratedCourses.set(next);
    try {
      localStorage.setItem(RATED_COURSES_KEY, JSON.stringify(next));
    } catch {
      // A full/blocked localStorage shouldn't break the submission itself,
      // just the "don't ask again" part surviving a reload.
    }
  }

  private _readRatedCourses(): string[] {
    try {
      const v = localStorage.getItem(RATED_COURSES_KEY);
      return v ? (JSON.parse(v) as string[]) : [];
    } catch {
      return [];
    }
  }
}
