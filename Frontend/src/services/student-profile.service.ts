import { Injectable, inject } from '@angular/core';
import { SupabaseService } from './supabase.service';

export interface MyProfile {
  linkedinUrl: string | null;
  isLinkedinPublic: boolean;
}

/** Optional, opt-in LinkedIn sharing -- OFF by default (see
 * student_profiles' is_linkedin_public column, migration Part C). Even
 * once turned on, another student can only ever see it through
 * getClassmateLinkedins(), which is itself scoped by RLS to students who
 * share an actual enrolled course -- never a site-wide directory. This is
 * the one piece of student identity this app exposes to other students at
 * all; everywhere else (course_enrollments, course_group_members) is
 * anonymous by design, so treat this service's write path as sensitive:
 * never call updateProfile() as a side effect of anything the student
 * didn't directly and knowingly opt into on a settings screen. */
@Injectable({ providedIn: 'root' })
export class StudentProfileService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  async getMyProfile(): Promise<MyProfile> {
    const { data, error } = await this.client
      .from('student_profiles')
      .select('linkedin_url, is_linkedin_public')
      .maybeSingle();
    if (error) throw error;
    return {
      linkedinUrl: data?.linkedin_url ?? null,
      isLinkedinPublic: data?.is_linkedin_public ?? false,
    };
  }

  /** Upsert since a student may not have a student_profiles row yet (it's
   * only created the first time they set anything here, unlike
   * student_plans which is created at onboarding). */
  async updateProfile(linkedinUrl: string | null, isPublic: boolean): Promise<void> {
    const userId = this.supabase.session()?.user.id;
    if (!userId) throw new Error('must be signed in');
    const { error } = await this.client
      .from('student_profiles')
      .upsert(
        { id: userId, linkedin_url: linkedinUrl?.trim() || null, is_linkedin_public: isPublic, updated_at: new Date().toISOString() },
        { onConflict: 'id' },
      );
    if (error) throw error;
  }

  /** Every classmate LinkedIn URL visible to the caller for this course --
   * routed through get_classmate_linkedins, a SECURITY DEFINER RPC, not a
   * direct table query: course_enrollments' RLS only ever exposes a
   * student's own row (even to a fellow classmate), so a plain query here
   * would silently return nothing for anyone but the caller. The RPC
   * itself also requires the caller be 'enrolled' (not just waitlisted)
   * in the course before it returns anything. Returns bare URLs only,
   * deliberately no other identity field -- this app has nothing else to
   * show anyway (no student display name anywhere). */
  async getClassmateLinkedins(courseCode: string): Promise<string[]> {
    const { data, error } = await this.client.rpc('get_classmate_linkedins', { p_course_code: courseCode });
    if (error) throw error;
    return ((data as { linkedin_url: string }[]) ?? []).map((row) => row.linkedin_url);
  }
}
