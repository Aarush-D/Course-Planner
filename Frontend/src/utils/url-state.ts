import { DestroyRef, WritableSignal, effect, inject, untracked } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

/** How a change to this piece of state should affect the back button.
 *
 * 'push' gives it a real history entry, so Back undoes it -- right for
 * something a student deliberately opened and would expect Back to close.
 * 'replace' rewrites the current entry instead, so the URL still describes
 * the page (and is still pasteable) without burying the previous page
 * under an entry per keystroke. Filters and text queries are always
 * 'replace' for exactly that reason. */
export type HistoryMode = 'push' | 'replace';

export interface QueryParamLink<T> {
  /** Query-param name. Scoped per route in practice, so the same key can
   * mean "the open course" on both Home and Flowchart without ambiguity. */
  key: string;
  signal: WritableSignal<T>;
  /** Signal value -> param string. Return null to drop the param entirely
   * rather than write an empty one (`?q=` is noise in a shared link). */
  toParam: (value: T) => string | null;
  /** Param -> signal value. Receives null when the param is absent (a
   * fresh load, or a Back that popped past it). Return undefined to leave
   * the signal alone -- used when the param names something that doesn't
   * exist yet or at all. */
  fromParam: (param: string | null) => T | undefined;
  history?: HistoryMode;
}

/**
 * Two-way sync between a component signal and one query param, so state
 * that was previously invisible to the URL becomes shareable, reloadable,
 * and reachable with the back button.
 *
 * Must be called from an injection context (a field initializer or
 * constructor), since it injects Router/ActivatedRoute and creates an
 * effect.
 *
 * The loop this has to avoid is the obvious one: signal writes URL, URL
 * write notifies the subscription, subscription writes signal, and around
 * again. Two things break it -- an explicit `applying` flag while a URL ->
 * signal write is in flight, and a value comparison on each side so a
 * write that changes nothing never happens in the first place. The flag
 * alone isn't enough (the router notifies asynchronously, after the flag
 * has already been cleared), and the comparison alone isn't enough either
 * (the two sides can disagree transiently while a navigation resolves).
 */
export function linkQueryParam<T>(link: QueryParamLink<T>): void {
  const router = inject(Router);
  const route = inject(ActivatedRoute);
  const destroyRef = inject(DestroyRef);
  const { key, signal, toParam, fromParam, history = 'replace' } = link;

  let applying = false;

  // Read the incoming param BEFORE the effect below exists. The effect
  // runs once on creation, and if it ran first it would write the signal's
  // default value over whatever the pasted link actually asked for --
  // deep-linking would appear to work and then immediately undo itself.
  const initial = fromParam(route.snapshot.queryParamMap.get(key));
  if (initial !== undefined) {
    applying = true;
    signal.set(initial);
    applying = false;
  }

  const subscription = route.queryParamMap.subscribe((params) => {
    const next = fromParam(params.get(key));
    if (next === undefined || Object.is(next, untracked(signal))) return;
    applying = true;
    signal.set(next);
    applying = false;
  });
  destroyRef.onDestroy(() => subscription.unsubscribe());

  effect(() => {
    const value = signal();
    if (applying) return;
    const param = toParam(value);
    // Nothing to do when the URL already says this. Skipping here is what
    // keeps a Back-triggered signal write from immediately re-pushing the
    // entry the user just backed out of.
    if (router.routerState.snapshot.root.queryParamMap.get(key) === param) return;
    router.navigate([], {
      relativeTo: route,
      // null drops the key; 'merge' leaves every other param untouched, so
      // two independent pieces of state can share a URL without either
      // clobbering the other.
      queryParams: { [key]: param },
      queryParamsHandling: 'merge',
      replaceUrl: history === 'replace',
    });
  });
}
