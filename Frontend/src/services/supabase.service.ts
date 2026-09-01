import { Injectable, signal } from '@angular/core';
import { createClient, Session, SupabaseClient } from '@supabase/supabase-js';
import { environment } from '../environments/environment';

export interface AdvisorProfile {
  id: string;
  display_name: string;
  created_at: string;
}

export interface ReviewRequestRow {
  id: string;
  plan_state: unknown;
  student_label: string | null;
  status: 'pending' | 'reviewed';
  created_at: string;
}

export interface PlanCommentRow {
  id: string;
  review_request_id: string;
  author_role: 'advisor' | 'student';
  author_name: string;
  body: string;
  created_at: string;
}

export interface MeetingProposalRow {
  id: string;
  review_request_id: string;
  advisor_id: string;
  proposed_at: string;
  note: string | null;
  status: 'proposed' | 'accepted' | 'declined';
  created_at: string;
}

export interface CourseRatingSummaryRow {
  course_code: string;
  rating_count: number;
  average_rating: number;
}

/**
 * Thin wrapper around the Supabase client + advisor auth session state.
 * This is the only new subsystem in the app that talks to a real database
 * -- everything else stays exactly as stateless as before (see
 * supabase/migrations/0001_advisor_workspace.sql for the schema/RLS this
 * relies on, and docs/COMPLIANCE_BACKLOG.md for why now, not sooner).
 */
@Injectable({ providedIn: 'root' })
export class SupabaseService {
  readonly client: SupabaseClient = createClient(environment.supabaseUrl, environment.supabaseAnonKey);

  readonly session = signal<Session | null>(null);

  constructor() {
    this.client.auth.getSession().then(({ data }) => this.session.set(data.session));
    this.client.auth.onAuthStateChange((_event, session) => this.session.set(session));
  }

  /** Returns needsEmailConfirmation: true when the project has email
   * confirmation turned on (Supabase's default) -- signUp() then creates
   * the auth.users row but returns no session, so there's no authenticated
   * client to create the advisor_profiles row with yet (an anon insert
   * attempt here would just fail with a permission error). display_name
   * is stashed in the user's own auth metadata so _ensureAdvisorProfile
   * can use it later, whenever the first real session actually shows up
   * -- either immediately below, or after they confirm and sign in. */
  async signUpAdvisor(email: string, password: string, displayName: string): Promise<{ needsEmailConfirmation: boolean }> {
    const { data, error } = await this.client.auth.signUp({
      email,
      password,
      options: { data: { display_name: displayName } },
    });
    if (error) throw error;
    if (data.session) await this._ensureAdvisorProfile();
    return { needsEmailConfirmation: !data.session };
  }

  async signInAdvisor(email: string, password: string) {
    const { error } = await this.client.auth.signInWithPassword({ email, password });
    if (error) throw error;
    await this._ensureAdvisorProfile();
  }

  async signOutAdvisor() {
    await this.client.auth.signOut();
  }

  /** Optional student accounts, purely for persisting a plan across
   * sessions (see student-session.service.ts) -- no display name, no
   * profile row, nothing to "ensure" the way advisors need: a student's
   * account has nothing to store beyond the plan snapshot itself
   * (student_plans, see migration 0005), so signUp/signIn here are plain
   * passthroughs, unlike signUpAdvisor/signInAdvisor above. */
  async signUpStudent(email: string, password: string): Promise<{ needsEmailConfirmation: boolean }> {
    const { data, error } = await this.client.auth.signUp({ email, password });
    if (error) throw error;
    return { needsEmailConfirmation: !data.session };
  }

  async signInStudent(email: string, password: string) {
    const { error } = await this.client.auth.signInWithPassword({ email, password });
    if (error) throw error;
  }

  /** Same underlying Supabase sign-out for both roles -- a session is a
   * session -- kept as a separate name for symmetry with signOutAdvisor
   * at call sites, not because the implementation actually differs. */
  async signOutStudent() {
    await this.client.auth.signOut();
  }

  /** Creates the advisor_profiles row on whichever sign-in first has a
   * real authenticated session -- a no-op every time after the first. */
  private async _ensureAdvisorProfile() {
    const { data: userData } = await this.client.auth.getUser();
    const user = userData.user;
    if (!user) return;
    const { data: existing } = await this.client
      .from('advisor_profiles')
      .select('id')
      .eq('id', user.id)
      .maybeSingle();
    if (existing) return;
    const displayName = (user.user_metadata?.['display_name'] as string | undefined) || user.email || 'Advisor';
    const { error } = await this.client.from('advisor_profiles').insert({ id: user.id, display_name: displayName });
    if (error) throw error;
  }
}
