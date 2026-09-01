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
   * client to claim the advisor_profiles row with yet. inviteCode is
   * stashed in the user's own auth metadata so it's available later
   * whenever the first real session actually shows up (either immediately
   * below, or after they confirm and sign in) -- claimAdvisorProfile()
   * itself is what actually vets and creates the row (see
   * claim_advisor_profile in supabase/migrations/0006, a SECURITY DEFINER
   * RPC requiring an unused invite code -- fixes a real Critical bug where
   * ANY authenticated user, including an existing student account, could
   * become a full advisor with zero vetting; see that migration's own
   * comment for the confirmed live exploit path). */
  async signUpAdvisor(
    email: string,
    password: string,
    displayName: string,
    inviteCode: string,
  ): Promise<{ needsEmailConfirmation: boolean }> {
    const { data, error } = await this.client.auth.signUp({
      email,
      password,
      options: { data: { display_name: displayName, invite_code: inviteCode } },
    });
    if (error) throw error;
    if (data.session) await this.claimAdvisorProfile(inviteCode, displayName);
    return { needsEmailConfirmation: !data.session };
  }

  /** Plain sign-in -- deliberately does NOT grant advisor access as a side
   * effect (that used to happen here via an auto-creating _ensureAdvisorProfile()
   * call, which is exactly the Critical bug migration 0006 fixes: signing
   * IN with any account, including a plain student one, silently became
   * "being an advisor"). Callers that need advisor access must check
   * isAdvisor() themselves afterward. */
  async signInAdvisor(email: string, password: string) {
    const { error } = await this.client.auth.signInWithPassword({ email, password });
    if (error) throw error;
  }

  /** True only for a real, vetted advisor_profiles row -- see is_advisor()
   * in supabase/migrations/0003, unaffected by whatever RLS the caller's
   * own session would otherwise see. Used by both the advisor-login page
   * (to reject a sign-in that isn't actually an advisor) and
   * advisorAuthGuard (to gate /advisor/* routes server-side, not just on
   * "is there a session"). */
  async isAdvisor(): Promise<boolean> {
    const { data } = await this.client.rpc('is_advisor');
    return data === true;
  }

  /** Redeems a one-time invite code and creates the caller's own
   * advisor_profiles row -- see claim_advisor_profile in
   * supabase/migrations/0006. Throws (with a message safe to show the
   * user) if the code is missing/already used or the name is invalid. */
  async claimAdvisorProfile(inviteCode: string, displayName: string): Promise<void> {
    const { error } = await this.client.rpc('claim_advisor_profile', {
      invite_code: inviteCode,
      display_name: displayName,
    });
    if (error) throw error;
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

  /** One shared reset flow for both roles -- advisor and student accounts
   * live in the same auth.users pool, and "forgot my password" isn't a
   * role-specific operation. Points the emailed link at /reset-password
   * (resolved against the real <base href>, same pattern
   * your-plan-page.component.ts already uses for its share link -- the
   * production build's --base-href flag means this can't be a hardcoded
   * path) where ResetPasswordPageComponent finishes the flow. Supabase
   * intentionally returns success here even for an email with no account,
   * so this alone can't be used to enumerate real accounts. */
  async requestPasswordReset(email: string): Promise<void> {
    const baseHref = document.querySelector('base')?.getAttribute('href') ?? '/';
    const redirectTo = new URL('reset-password', new URL(baseHref, location.origin)).toString();
    const { error } = await this.client.auth.resetPasswordForEmail(email, { redirectTo });
    if (error) throw error;
  }

  /** Called from ResetPasswordPageComponent once the emailed link has
   * established a temporary recovery session (see that component for how
   * it detects one). */
  async updatePassword(newPassword: string): Promise<void> {
    const { error } = await this.client.auth.updateUser({ password: newPassword });
    if (error) throw error;
  }
}
