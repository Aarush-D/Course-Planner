import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  OnInit,
  afterNextRender,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { animateModalIn, animateModalOut } from '../../animations/modal-fade';
import { CourseRatingService } from '../../services/course-rating.service';
import { CourseRatingRow } from '../../services/supabase.service';
import { StarRatingComponent } from '../ui/star-rating/star-rating.component';

/** Read-only list of what other students actually wrote -- triggered by
 * "See reviews" next to a course card's star rating (see
 * flowchart.component.html). Same modal shell/animate pattern as
 * RateCourseModalComponent, just showing reviews instead of collecting
 * one. Ratings are anonymous (see course-rating.service.ts), so no
 * author info to show beyond the star count and roughly when it was
 * left. */
@Component({
  selector: 'app-course-reviews-modal',
  standalone: true,
  templateUrl: './course-reviews-modal.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StarRatingComponent, DatePipe],
})
export class CourseReviewsModalComponent implements OnInit {
  courseCode = input.required<string>();
  courseName = input<string>('');

  closed = output<void>();

  private readonly ratings = inject(CourseRatingService);
  private readonly injector = inject(Injector);

  private readonly backdrop = viewChild<ElementRef<HTMLElement>>('modalBackdrop');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('modalPanel');

  loading = signal(true);
  reviews = signal<CourseRatingRow[]>([]);
  error = signal(false);

  constructor() {
    afterNextRender(() => this._animateIn(), { injector: this.injector });
  }

  // Required inputs aren't guaranteed readable in the constructor (the
  // compiler flags it -- NG8118) even though this app's other modals only
  // ever read theirs from event handlers, never eagerly on construct like
  // this one needs to. ngOnInit is the first point they're safe to read.
  ngOnInit() {
    this.ratings
      .getReviews(this.courseCode())
      .then((rows) => this.reviews.set(rows))
      .catch(() => this.error.set(true))
      .finally(() => this.loading.set(false));
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
