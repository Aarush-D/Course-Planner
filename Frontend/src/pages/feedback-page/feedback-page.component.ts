import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { FeedbackService } from '../../services/feedback.service';

type FeedbackCategory = 'bug' | 'request' | 'other';

/** A direct line to report a problem or ask for something -- deliberately
 * no in-app view of what's submitted (see FeedbackService / migration
 * 0021_user_feedback.sql): this page is submission-only by design, not a
 * cut corner. Triage happens by hand against the live table. */
@Component({
  selector: 'app-feedback-page',
  standalone: true,
  templateUrl: './feedback-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FeedbackPageComponent {
  private readonly feedback = inject(FeedbackService);
  private readonly router = inject(Router);

  readonly categories: { value: FeedbackCategory; label: string }[] = [
    { value: 'bug', label: 'Something broke' },
    { value: 'request', label: 'I want a feature' },
    { value: 'other', label: 'Something else' },
  ];

  category = signal<FeedbackCategory>('bug');
  body = signal('');
  contact = signal('');
  submitting = signal(false);
  error = signal<string | null>(null);
  submitted = signal(false);

  async submit() {
    const body = this.body().trim();
    if (!body || this.submitting()) return;
    this.submitting.set(true);
    this.error.set(null);
    try {
      await this.feedback.submitFeedback(this.category(), body, this.contact(), this.router.url);
      this.submitted.set(true);
      this.body.set('');
      this.contact.set('');
    } catch {
      this.error.set("Couldn’t send that. Try again in a moment.");
    } finally {
      this.submitting.set(false);
    }
  }

  submitAnother() {
    this.submitted.set(false);
  }
}
