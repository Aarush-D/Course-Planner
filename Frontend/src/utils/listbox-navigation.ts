import { Signal, signal } from '@angular/core';

/** What a combobox has to be able to do for the navigator to drive it.
 * Deliberately callbacks rather than a base class: the four comboboxes
 * this backs (primary major, minors, each extra-major slot, and Plan
 * Compare's own major picker) already keep their open/close state in
 * component signals with real behavior attached -- selecting a major
 * re-plans the whole degree, selecting a minor toggles it in a set -- so
 * the navigator drives those existing methods instead of owning state
 * that would then need syncing back. */
export interface ListboxHandlers<T> {
  isOpen: () => boolean;
  open: () => void;
  close: () => void;
  select: (option: T) => void;
}

/** Keyboard behavior for the ARIA combobox + listbox pattern.
 *
 * Every one of this app's major/minor pickers *declared* the pattern --
 * role="combobox", aria-autocomplete="list", role="option" -- but
 * implemented none of its keyboard contract: options were selectable by
 * (mousedown) only, with no Arrow keys, no Enter, no Escape, and no
 * aria-activedescendant. A screen reader would announce "combobox,
 * expanded" and then offer no way to reach what it just announced, and a
 * sighted keyboard user had to Tab through all ~160 majors one at a time.
 *
 * Note the pattern deliberately does NOT move real DOM focus onto the
 * options: focus stays in the text input (so typing keeps filtering) and
 * aria-activedescendant tells assistive tech which option is current.
 * That's why the options need their own `active` styling -- there's no
 * :focus on them to style. */
export class ListboxNavigator<T> {
  /** Index into options() of the option the user has arrowed to, or -1
   * when the listbox is open but nothing is highlighted yet (the state
   * right after focusing, before the first Arrow key). */
  readonly activeIndex = signal(-1);

  constructor(
    private readonly idPrefix: string,
    private readonly options: Signal<T[]> | (() => T[]),
    private readonly handlers: ListboxHandlers<T>,
  ) {}

  optionId(index: number): string {
    return `${this.idPrefix}-opt-${index}`;
  }

  /** Bind to the input's [attr.aria-activedescendant]. Null (not empty
   * string) when nothing is active, so the attribute is removed entirely
   * rather than left pointing at nothing. */
  activeDescendantId(): string | null {
    const index = this.activeIndex();
    if (!this.handlers.isOpen() || index < 0 || index >= this.options().length) return null;
    return this.optionId(index);
  }

  isActive(index: number): boolean {
    return this.activeIndex() === index;
  }

  /** Call whenever the option list changes underneath the highlight --
   * i.e. on every keystroke in the filter box. Without this, typing after
   * arrowing down leaves the highlight on whatever now happens to sit at
   * that index, which is a different major than the one the user was
   * looking at. */
  reset(): void {
    this.activeIndex.set(-1);
  }

  handleKeydown(event: KeyboardEvent): void {
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowUp': {
        event.preventDefault(); // stop the caret jumping to either end of the input
        if (!this.handlers.isOpen()) {
          this.handlers.open();
          this.activeIndex.set(0);
          this._scrollActiveIntoView();
          return;
        }
        const count = this.options().length;
        if (!count) return;
        const current = this.activeIndex();
        // Wraps in both directions: from the last option ArrowDown lands
        // back on the first, which is what a native <select> does and what
        // makes a 160-item list navigable from either end.
        this.activeIndex.set(
          event.key === 'ArrowDown'
            ? (current + 1) % count
            : current <= 0
              ? count - 1
              : current - 1,
        );
        this._scrollActiveIntoView();
        return;
      }
      case 'Home':
      case 'End': {
        if (!this.handlers.isOpen() || !this.options().length) return;
        event.preventDefault();
        this.activeIndex.set(event.key === 'Home' ? 0 : this.options().length - 1);
        this._scrollActiveIntoView();
        return;
      }
      case 'Enter': {
        const index = this.activeIndex();
        const options = this.options();
        if (!this.handlers.isOpen() || index < 0 || index >= options.length) return;
        // Only swallowed when it actually selects something -- an Enter
        // with nothing highlighted stays available to whatever form the
        // combobox happens to sit inside.
        event.preventDefault();
        this.handlers.select(options[index]);
        this.reset();
        return;
      }
      case 'Escape': {
        if (!this.handlers.isOpen()) return;
        event.preventDefault();
        // stopPropagation matters here, and only here: these comboboxes
        // sit inside modals whose focus trap listens for Escape on
        // `document` (see ModalFocusTrapDirective). Without this, one
        // Escape closes the dropdown AND the modal around it -- confirmed
        // live, the whole first-visit welcome modal vanished mid-search.
        // An Escape with the listbox already closed still falls through
        // to the modal, which is the behavior you want: first Escape
        // dismisses the dropdown, second dismisses the dialog.
        event.stopPropagation();
        this.handlers.close();
        this.reset();
        return;
      }
    }
  }

  /** The option elements already exist in the DOM (only their highlight
   * class changes), so this needs no render tick to find one. `nearest`
   * scrolls the listbox only when the option is actually out of view,
   * leaving it still when it isn't. */
  private _scrollActiveIntoView(): void {
    document.getElementById(this.optionId(this.activeIndex()))?.scrollIntoView({ block: 'nearest' });
  }
}

/** A grouped option list flattened into (a) render rows that keep the
 * college headers and (b) the flat, header-free option array the
 * navigator indexes into. The two have to be built together: the rows
 * carry each option's index in the flat list, which is what links a
 * rendered <button> back to the arrow-key highlight. */
export type ListboxRow<T> =
  | { kind: 'header'; label: string }
  | { kind: 'option'; option: T; index: number };

export function buildListboxRows<T>(
  groups: readonly { college: string; options: readonly T[] }[],
  /** Options rendered above the first group -- the extra-major pickers'
   * "None" entry, which has to be arrow-reachable like any other. */
  leading: readonly T[] = [],
): { rows: ListboxRow<T>[]; options: T[] } {
  const rows: ListboxRow<T>[] = [];
  const options: T[] = [];
  for (const option of leading) {
    rows.push({ kind: 'option', option, index: options.length });
    options.push(option);
  }
  for (const group of groups) {
    rows.push({ kind: 'header', label: group.college });
    for (const option of group.options) {
      rows.push({ kind: 'option', option, index: options.length });
      options.push(option);
    }
  }
  return { rows, options };
}
