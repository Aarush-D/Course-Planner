import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { TranscriptMatchedCourse, transcriptStatusLabel } from '../../models/course-plan.model';
import { StatusBadgeComponent, StatusBadgeTone } from '../ui/status-badge/status-badge.component';
import { TranscriptImportResult } from '../../services/planner-state.service';

/**
 * Purely-presentational recap of one transcript upload -- PlannerStateService
 * already applies "completed" matches to `state.completed` and phrases a
 * text summary into the chat transcript (see onTranscriptUploaded there);
 * this is the dedicated visibility surface `lastTranscriptImport` was added
 * for, so a student can actually SEE the full breakdown (what got added,
 * what was recognized but deliberately left out, what couldn't be read at
 * all) instead of only a comma-joined sentence in a chat bubble. No
 * confirm/apply gate here on purpose -- completed courses already applied
 * correctly before this ever renders; this only explains what happened.
 * Parent (ChatbotComponent) owns whether it's currently shown at all, the
 * same "parent owns visibility, this owns rendering" split as
 * GenEdDeptChipsComponent.
 */
@Component({
  selector: 'app-transcript-import-review',
  standalone: true,
  templateUrl: './transcript-import-review.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StatusBadgeComponent],
})
export class TranscriptImportReviewComponent {
  result = input.required<TranscriptImportResult>();

  /** Parent decides what dismissing means (a session-only "hide until the
   * next upload" flag, same pattern as home-page's transcript-stale nudge)
   * -- this component never tracks its own visibility. */
  dismiss = output<void>();

  private readonly appliedCodes = computed(() => new Set(this.result().appliedCodes));

  /** Recognized AND actually added to `state.completed` this pass. */
  appliedCourses = computed<TranscriptMatchedCourse[]>(() =>
    this.result().matched.filter((m) => this.appliedCodes().has(m.code)),
  );

  /** Recognized but NOT added -- either a real non-completed status
   * (failed/withdrawn/in-progress) or a "completed" match that was already
   * in `state.completed` before this upload (see onTranscriptUploaded's
   * `newCodes` filter), which never makes it into appliedCodes either. */
  notAppliedCourses = computed<TranscriptMatchedCourse[]>(() =>
    this.result().matched.filter((m) => !this.appliedCodes().has(m.code)),
  );

  unmatchedHints = computed<string[]>(() => this.result().unmatched);

  /** Label for a notAppliedCourses() row. A "completed"-status match that
   * still landed here can only be the already-tracked case above --
   * transcriptStatusLabel's own fallback would otherwise print the raw,
   * misleading "completed" right next to a "not added" heading. */
  statusLabel(course: TranscriptMatchedCourse): string {
    if (course.status === 'completed') return 'already in your plan';
    return transcriptStatusLabel(course.status);
  }

  statusTone(course: TranscriptMatchedCourse): StatusBadgeTone {
    switch (course.status) {
      case 'failed':
        return 'red';
      case 'withdrawn':
        return 'amber';
      case 'in-progress':
        return 'indigo';
      default:
        // Already-tracked "completed" duplicate -- neutral, not a warning.
        return 'slate';
    }
  }
}
