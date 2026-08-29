/** This is a client-only SPA (no SSR) so `window` always exists here --
 * unlike a universal app, there's no need to guard against it being absent. */
export function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
