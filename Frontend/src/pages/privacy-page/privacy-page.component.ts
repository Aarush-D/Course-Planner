import { ChangeDetectionStrategy, Component } from '@angular/core';

@Component({
  selector: 'app-privacy-page',
  standalone: true,
  templateUrl: './privacy-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PrivacyPageComponent {
  readonly effectiveDate = 'September 1, 2026';
  readonly contactEmail = 'aarush.d9@gmail.com';
}
