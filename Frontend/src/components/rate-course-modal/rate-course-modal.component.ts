import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  afterNextRender,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { animateModalIn, animateModalOut } from '../../animations/modal-fade';
import { CourseRatingService } from '../../services/course-rating.service';
import { ToastService } from '../../services/toast.service';
import { StarRatingComponent } from '../ui/star-rating/star-rating.component';

/** Submit form for a course rating -- course code (display-only), an
 * interactive star pick, and an optional review body. Rendered by a parent
 * behind an `@if`, matching AppComponent's own 3-modal mount/unmount
 * pattern (animate in via afterNextRender once the backdrop/panel nodes
 * exist, animate out before the parent's `@if` actually removes them). */
@Component({
  selector: 'app-rate-course-modal',
  standalone: true,
  templateUrl: './rate-course-modal.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StarRatingComponent],
})
export class RateCourseModalComponent {
  courseCode = input.required<string>();
  courseName = input<string>('');

  closed = output<void>();

  private readonly ratings = inject(CourseRatingService);
  private readonly toast = inject(ToastService);
  private readonly injector = inject(Injector);

  private readonly backdrop = viewChild<ElementRef<HTMLElement>>('modalBackdrop');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('modalPanel');

  rating = signal(0);
  reviewBody = signal('');
  submitting = signal(false);

  constructor() {
    afterNextRender(() => this._animateIn(), { injector: this.injector });
  }

  onRated(stars: number) {
    this.rating.set(stars);
  }

  async submit() {
    if (!this.rating() || this.submitting()) return;
    this.submitting.set(true);
    try {
      await this.ratings.submitRating(this.courseCode(), this.rating(), this.reviewBody());
      this.toast.show(`Thanks for rating ${this.courseCode()}!`);
      await this.close();
    } catch {
      this.toast.show(`Couldn't submit your rating — try again in a moment.`, 'error');
    } finally {
      this.submitting.set(false);
    }
  }

  async close() {
    await this._animateOut();
    this.closed.emit();
  }

  private _animateIn() {
    const b = this.backdrop();
    const p = this.panel();
    if (b && p) animateModalIn(b.nativeElement, p.nativeElement);
  }

  private async _animateOut(): Promise<void> {
    const b = this.backdrop();
    const p = this.panel();
    if (b && p) await animateModalOut(b.nativeElement, p.nativeElement);
  }
}
