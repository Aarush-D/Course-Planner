import { ChangeDetectionStrategy, Component, ElementRef, HostListener, inject, signal, viewChild } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** A toggleable flyout, not a docked sidebar -- closed, it's a single
 * small button and takes no layout space at all; open, it floats over
 * the page as an absolutely-positioned panel rather than pushing <main>
 * over. A docked sidebar (even collapsed to an icon-only rail, which is
 * what this used to do on phone-width screens) permanently eats some of
 * the page's width; this doesn't, at any screen size, open or closed --
 * that was the actual point of switching to this shape, not just a
 * visual refresh. Same outside-click/Escape-to-close pattern as
 * PreferencesPanelComponent, for the same reason: consistent behavior
 * for every dropdown-shaped control in this app's header chrome. */
@Component({
  selector: 'app-nav',
  standalone: true,
  templateUrl: './nav.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
})
export class NavComponent {
  private readonly host = inject(ElementRef<HTMLElement>);

  // Starts open on desktop-width screens (where a floating panel costs
  // nothing to leave up) and closed on phone-width ones -- same breakpoint
  // this always used, just deciding "shown or not" now instead of
  // "labeled or icon-only." Evaluated once at construction, same as
  // before: this intentionally doesn't reactively track window resizes
  // after the fact (see the layout-bug writeup this replaced, which
  // covers why that's fine -- a real fresh load at a given width is what
  // matters, not a mid-session resize).
  open = signal(window.innerWidth >= 768);

  readonly toggleButton = viewChild<ElementRef<HTMLButtonElement>>('toggleButton');

  toggleOpen(): void {
    this.open.update((v) => !v);
  }

  @HostListener('document:keydown.escape')
  onEscape() {
    if (!this.open()) return;
    this.open.set(false);
    this.toggleButton()?.nativeElement.focus();
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent) {
    if (this.open() && !this.host.nativeElement.contains(event.target as Node)) {
      this.open.set(false);
    }
  }
}
