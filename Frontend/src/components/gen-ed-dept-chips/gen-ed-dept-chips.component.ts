import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  computed,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

/** One department prefix's chip data for a single Gen Ed domain (or
 * domain-set) card -- precomputed by GenEdPageComponent from that card's
 * own course pool (see buildDeptChips there), never recomputed here. */
export interface GenEdDeptChip {
  /** The department prefix itself, e.g. "AA", "ENGL", "A-I". */
  prefix: string;
  /** How many of this card's approved courses carry this prefix. */
  count: number;
  /** Up to 6 course titles for the hover/focus preview -- short by design,
   * not the full list (that's what selecting the option to filter is for). */
  previewTitles: string[];
  /** How many more beyond previewTitles this department actually has. */
  previewMore: number;
}

/**
 * Department-prefix filter for one Gen Ed domain card's course pool (e.g.
 * GHW's 130+ approved courses split into 30+ AA/ASIA/BBH/... departments).
 * A dropdown rather than a row of chips -- a domain with this many
 * departments turned into a wall of wrapped pills, which is exactly the
 * clutter this control exists to avoid. Purely presentational -- the
 * parent owns which prefix (if any) is currently active and does the
 * actual course-list filtering; this component only renders the toggle
 * button, the open listbox panel, and a hover-or-keyboard-focus preview
 * popover per option.
 *
 * Hover-with-keyboard-fallback: the preview is driven by ONE signal
 * (previewPrefix) that both (mouseenter)/(mouseleave) on an option's own
 * wrapper AND (focus)/(blur) on the option button itself can set/clear, so
 * a keyboard user tabbing through the open panel gets the exact same
 * preview a mouse user hovering gets -- never a mouse-only affordance.
 * mouseenter/mouseleave are bound to the WRAPPER div (not the button)
 * specifically so that moving the pointer from the button into the
 * popover itself (to read a long title, say) doesn't fire mouseleave
 * partway there -- WCAG 2.1 SC 1.4.13's "hoverable" requirement.
 *
 * Open/close follows the exact click-outside + Escape-returns-focus
 * pattern account-menu.component.ts already established in this codebase
 * (ElementRef + document:click HostListener), rather than inventing a new
 * one.
 */
@Component({
  selector: 'app-gen-ed-dept-chips',
  standalone: true,
  templateUrl: './gen-ed-dept-chips.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GenEdDeptChipsComponent {
  chips = input.required<GenEdDeptChip[]>();
  /** The currently active filter prefix for this card, or null when the
   * full domain list is showing -- owned by the parent, this component
   * never tracks "active" state of its own. */
  active = input<string | null>(null);

  /** Emits the prefix that was selected -- the parent (GenEdPageComponent)
   * decides whether that's a new filter or a toggle-off of the current one
   * (selecting the already-active option again), since it's the one that
   * knows what "active" currently is. Also emitted, unconditionally, by
   * the panel's own "All departments" option when a filter is active --
   * mirrors the old "Clear filter" link's own `chipClick.emit(active()!)`. */
  chipClick = output<string>();

  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly toggleButton = viewChild<ElementRef<HTMLButtonElement>>('toggleButton');

  open = signal(false);

  /** Which option's preview popover is open, if any -- hover OR focus, see
   * class doc comment above. */
  previewPrefix = signal<string | null>(null);

  activeChip = computed(() => this.chips().find((c) => c.prefix === this.active()) ?? null);

  toggleOpen() {
    this.open.update((v) => !v);
  }

  select(prefix: string) {
    this.chipClick.emit(prefix);
    this.open.set(false);
    this.previewPrefix.set(null);
  }

  clearFilter() {
    const current = this.active();
    if (current) this.chipClick.emit(current);
    this.open.set(false);
    this.previewPrefix.set(null);
  }

  showPreview(prefix: string) {
    this.previewPrefix.set(prefix);
  }

  clearPreview(prefix: string) {
    if (this.previewPrefix() === prefix) this.previewPrefix.set(null);
  }

  @HostListener('document:keydown.escape')
  onEscape() {
    if (this.previewPrefix()) {
      this.previewPrefix.set(null);
      return;
    }
    if (this.open()) {
      this.open.set(false);
      this.toggleButton()?.nativeElement.focus();
    }
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent) {
    if (this.open() && !this.host.nativeElement.contains(event.target as Node)) {
      this.open.set(false);
      this.previewPrefix.set(null);
    }
  }
}
