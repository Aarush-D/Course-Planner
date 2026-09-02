import { Directive, ElementRef, HostListener, OnDestroy, OnInit, inject, input } from '@angular/core';

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Applied to a modal/dialog panel element (put it right next to the
 * existing #modalPanel/#panel ref every modal in this app already has).
 * Fixes the same gap in every one of them at once: no role=dialog+focus
 * management anywhere (confirmed via the accessibility audit -- help
 * modal, welcome modal, rate-course-modal, course-reviews-modal, and the
 * Weekly Schedule's course-detail popup all had this identical gap).
 *
 * On mount: remembers whatever had focus before the modal opened, moves
 * focus into the panel, and traps Tab/Shift+Tab within it. On unmount
 * (Angular's @if removing the node -- which for this app's modals happens
 * AFTER their own animateModalOut() await, see animations/modal-fade.ts):
 * returns focus to whatever had it before.
 *
 * Escape is delivered via a plain [onEscape] callback input, not an
 * output() -- and the keydown listener itself is Angular's own
 * @HostListener('document:keydown', ...), not a manually-attached
 * document.addEventListener. Both of those are deliberate, and both
 * exist because of the SAME real bug, found live: this app's modals
 * close via an async afterNextRender() chain (await the fade-out
 * animation, THEN flip the "open" signal). A normal Angular-bound
 * (click) handler always gets a render for free afterward (Angular's
 * compiled listener marks the view dirty before invoking it) -- which
 * afterNextRender needs, or it just never fires. A handler invoked from
 * a manually-attached, non-Angular-tracked listener does NOT get that
 * for free in a zoneless app: confirmed live (via output()'s .emit(),
 * then again via a plain callback plus a manual ApplicationRef.tick())
 * that the callback demonstrably ran but the modal never actually
 * closed -- the signal write inside afterNextRender's callback just
 * never happened, because nothing had scheduled the render it was
 * waiting for. @HostListener goes through Angular's own compiled event
 * binding the same way a template (keydown) would, which reliably
 * triggers that scheduling -- this is the actual fix, not a version of
 * the same workaround. */
@Directive({
  selector: '[appModalFocusTrap]',
  standalone: true,
})
export class ModalFocusTrapDirective implements OnInit, OnDestroy {
  private readonly host = inject(ElementRef<HTMLElement>);

  onEscape = input<(() => void) | undefined>(undefined);

  private returnFocusTo: HTMLElement | null = null;

  ngOnInit() {
    const el = this.host.nativeElement;
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    this.returnFocusTo = document.activeElement as HTMLElement | null;
    const first = this._focusables()[0] ?? el;
    first.focus();
  }

  ngOnDestroy() {
    // Deferred one tick: Angular calls ngOnDestroy BEFORE actually
    // detaching this node from the document, and the browser's own
    // "focused element left the document" handling (which unconditionally
    // moves focus to <body>) was observed, live, to run after this and
    // clobber a same-tick .focus() call here.
    const target = this.returnFocusTo;
    setTimeout(() => target?.focus?.(), 0);
  }

  private _focusables(): HTMLElement[] {
    return Array.from(this.host.nativeElement.querySelectorAll(FOCUSABLE_SELECTOR)) as HTMLElement[];
  }

  @HostListener('document:keydown', ['$event'])
  onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      // stopImmediatePropagation, not just stopPropagation: if more than
      // one instance is alive at once (e.g. a previous one mid-close),
      // both are bound to the same document target via @HostListener,
      // and a plain stopPropagation doesn't stop a second listener
      // already registered on that exact node from also firing.
      e.stopImmediatePropagation();
      this.onEscape()?.();
      return;
    }
    if (e.key !== 'Tab') return;
    const focusables = this._focusables();
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
}
