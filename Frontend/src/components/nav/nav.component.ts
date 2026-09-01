import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

@Component({
  selector: 'app-nav',
  standalone: true,
  templateUrl: './nav.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
})
export class NavComponent {
  // Starts collapsed to an icon-only rail on phone-width screens, where the
  // full-width labeled sidebar otherwise crowds out the main content —
  // desktop keeps today's always-expanded look. Manually toggleable
  // afterward at any screen size via the button at the bottom of the nav.
  collapsed = signal(window.innerWidth < 768);

  toggleCollapsed(): void {
    this.collapsed.update((v) => !v);
  }
}
