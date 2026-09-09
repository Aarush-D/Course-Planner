import { ChangeDetectionStrategy, Component, ElementRef, HostListener, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive } from '@angular/router';
import { filter } from 'rxjs';

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
  private readonly router = inject(Router);

  // Starts closed at every screen size. It used to start open on
  // desktop-width screens, which meant a first-time visitor saw it
  // rendered underneath the welcome modal's backdrop, and a returning one
  // had a floating panel covering the top of whichever page they'd loaded
  // -- a flyout that is a real overlay (not a docked rail) should be
  // something you open, not something you dismiss. The guided tour still
  // opens it on the step that needs it (see TourService's
  // requiresNavOpen and the (requestNavOpen) binding in app.component.html).
  open = signal(false);

  readonly toggleButton = viewChild<ElementRef<HTMLButtonElement>>('toggleButton');

  constructor() {
    // Picking a page is the end of the flyout's job -- left open, it sat
    // over the new page's header until the next outside click. Same
    // NavigationEnd pattern app.component.ts uses for currentPath.
    this.router.events
      .pipe(filter((e) => e instanceof NavigationEnd), takeUntilDestroyed())
      .subscribe(() => this.open.set(false));
  }

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
