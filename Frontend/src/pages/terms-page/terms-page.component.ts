import { ChangeDetectionStrategy, Component } from '@angular/core';

@Component({
  selector: 'app-terms-page',
  standalone: true,
  templateUrl: './terms-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TermsPageComponent {
  readonly effectiveDate = 'August 29, 2026';
  readonly contactEmail = 'aarush.d9@gmail.com';
}
