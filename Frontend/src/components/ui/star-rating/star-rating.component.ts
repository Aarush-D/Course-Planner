import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

/** One star-rating control, two modes via `interactive`:
 * - read-only (default): shows `value` (rounded to the nearest whole star
 *   -- a deliberate simplification over a true partial-fill render) and an
 *   optional `count`, for course cards.
 * - interactive: clickable 1-5 stars, emits `rated`, for the submit modal
 *   and the Your Plan page's free-text rating entry. */
@Component({
  selector: 'app-star-rating',
  standalone: true,
  templateUrl: './star-rating.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StarRatingComponent {
  interactive = input(false);
  value = input(0);
  count = input<number | null>(null);

  rated = output<number>();

  private readonly hovered = signal<number | null>(null);
  private readonly selected = signal(0);

  readonly stars = [1, 2, 3, 4, 5];

  displayValue = computed(() => this.hovered() ?? this.selected() ?? this.value());

  isFilled(star: number): boolean {
    if (this.interactive()) return star <= this.displayValue();
    return star <= Math.round(this.value());
  }

  onHover(star: number) {
    if (this.interactive()) this.hovered.set(star);
  }

  onHoverEnd() {
    this.hovered.set(null);
  }

  onClick(star: number) {
    if (!this.interactive()) return;
    this.selected.set(star);
    this.rated.emit(star);
  }
}
