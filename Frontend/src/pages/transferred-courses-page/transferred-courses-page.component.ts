import { ChangeDetectionStrategy, Component } from '@angular/core';

@Component({
  selector: 'app-transferred-courses-page',
  standalone: true,
  templateUrl: './transferred-courses-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TransferredCoursesPageComponent {}
