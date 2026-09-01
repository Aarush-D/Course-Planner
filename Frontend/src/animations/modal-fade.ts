import { animate } from 'motion';
import { prefersReducedMotion } from './reduced-motion';

/** Fire-and-forget -- called once per mount (from afterNextRender, so the
 * DOM node already exists), nothing needs to happen after it finishes. */
export function animateModalIn(backdrop: HTMLElement, panel: HTMLElement): void {
  if (prefersReducedMotion()) return;
  animate(backdrop, { opacity: [0, 1] }, { duration: 0.15 });
  animate(panel, { opacity: [0, 1], transform: ['scale(0.95)', 'scale(1)'] }, { duration: 0.15 });
}

/** Awaited by the caller before the state change that unmounts the modal --
 * Angular's @if removes the DOM node the instant that flips, with no async
 * teardown hook, so the animation has to finish BEFORE that happens. */
export async function animateModalOut(backdrop: HTMLElement, panel: HTMLElement): Promise<void> {
  if (prefersReducedMotion()) return;
  await Promise.all([
    animate(backdrop, { opacity: 0 }, { duration: 0.12 }).finished,
    animate(panel, { opacity: 0, transform: 'scale(0.95)' }, { duration: 0.12 }).finished,
  ]);
}
