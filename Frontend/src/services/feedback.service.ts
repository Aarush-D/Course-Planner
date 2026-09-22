import { Injectable, inject } from '@angular/core';
import { SupabaseService } from './supabase.service';

/**
 * Submission-only: user_feedback has no read policy for anon/authenticated
 * at all (see supabase/migrations/0021_user_feedback.sql) -- by design,
 * nothing in the app ever reads this back. Triage happens by hand, via a
 * direct Supabase connection, not through this service or any UI.
 */
@Injectable({ providedIn: 'root' })
export class FeedbackService {
  private readonly supabase = inject(SupabaseService);
  private get client() {
    return this.supabase.client;
  }

  async submitFeedback(
    category: 'bug' | 'request' | 'other',
    body: string,
    contact?: string,
    pageContext?: string,
  ): Promise<void> {
    const { error } = await this.client.from('user_feedback').insert({
      category,
      body: body.trim(),
      contact: contact?.trim() || null,
      page_context: pageContext?.trim() || null,
    });
    if (error) throw error;
  }
}
