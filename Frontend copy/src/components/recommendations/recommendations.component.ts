import { Component, ChangeDetectionStrategy, input } from '@angular/core';

@Component({
  selector: 'app-recommendations',
  standalone: true,
  templateUrl: './recommendations.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RecommendationsComponent {
  text = input<string | null>();
  isLoading = input.required<boolean>();
}