import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { TourService } from '../../services/tour.service';

type Rect = { top: number; left: number; width: number; height: number };

const PAD = 8;
const GAP = 14;
const TOOLTIP_WIDTH = 320;
const VIEWPORT_MARGIN = 16;

@Component({
  selector: 'app-tour-overlay',
  standalone: true,
  templateUrl: './tour-overlay.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TourOverlayComponent {
  readonly tour = inject(TourService);

  // The chat panel only exists in the DOM while open — steps that target
  // something inside it ask the app shell to open it first, via this
  // output, rather than reaching into chat state directly (this component
  // has no business knowing chatOpen is a signal on AppComponent).
  requestChatOpen = output<boolean>();

  readonly rect = signal<Rect | null>(null);
  readonly ready = signal(false);

  private readonly tooltip = viewChild<ElementRef<HTMLDivElement>>('tooltip');
  private returnFocusTo: HTMLElement | null = null;
  private readonly injector = inject(Injector);

  readonly tooltipWidth = TOOLTIP_WIDTH;

  readonly maskTop = computed(() => this._maskTop());
  readonly maskBottom = computed(() => this._maskBottom());
  readonly maskLeft = computed(() => this._maskLeft());
  readonly maskRight = computed(() => this._maskRight());
  readonly highlightStyle = computed(() => this._highlightStyle());
  readonly tooltipStyle = computed(() => this._tooltipStyle());
  readonly tooltipPlacement = computed(() => this._tooltipPlacement());

  private _maskTop() {
    const r = this.rect();
    return r ? { height: `${Math.max(0, r.top)}px` } : { height: '0px' };
  }

  private _maskBottom() {
    const r = this.rect();
    return r ? { top: `${r.top + r.height}px` } : { top: '100%' };
  }

  private _maskLeft() {
    const r = this.rect();
    return r
      ? { top: `${r.top}px`, height: `${r.height}px`, width: `${Math.max(0, r.left)}px` }
      : { top: '0px', height: '0px', width: '0px' };
  }

  private _maskRight() {
    const r = this.rect();
    return r
      ? { top: `${r.top}px`, height: `${r.height}px`, left: `${r.left + r.width}px`, right: '0px' }
      : { top: '0px', height: '0px', left: '100%' };
  }

  private _highlightStyle() {
    const r = this.rect();
    if (!r) return { display: 'none' };
    return {
      top: `${r.top}px`,
      left: `${r.left}px`,
      width: `${r.width}px`,
      height: `${r.height}px`,
    };
  }

  private _tooltipPlacement(): 'below' | 'above' | 'beside' {
    const r = this.rect();
    if (!r) return 'below';
    // A target spanning most of the viewport's height (e.g. the whole
    // sidebar nav, targeted as one step since the tour rework) leaves no
    // real room either above or below it -- the old two-way check always
    // fell through to 'below' in that case, which pushed the tooltip past
    // the bottom edge of the screen. Neither above/below placement makes
    // sense here; place it beside the target instead.
    if (r.height > window.innerHeight - GAP * 2) return 'beside';
    const spaceBelow = window.innerHeight - (r.top + r.height);
    return spaceBelow < 200 && r.top > 200 ? 'above' : 'below';
  }

  private _tooltipStyle() {
    const r = this.rect();
    if (!r) return { display: 'none' };
    const placement = this._tooltipPlacement();
    if (placement === 'beside') {
      const left = Math.min(
        r.left + r.width + GAP,
        window.innerWidth - TOOLTIP_WIDTH - VIEWPORT_MARGIN,
      );
      return { left: `${left}px`, top: `${VIEWPORT_MARGIN}px`, width: `${TOOLTIP_WIDTH}px` };
    }
    const left = Math.min(
      Math.max(r.left, VIEWPORT_MARGIN),
      window.innerWidth - TOOLTIP_WIDTH - VIEWPORT_MARGIN,
    );
    const style: Record<string, string> = { left: `${left}px`, width: `${TOOLTIP_WIDTH}px` };
    if (placement === 'below') {
      style['top'] = `${r.top + r.height + GAP}px`;
    } else {
      style['bottom'] = `${window.innerHeight - r.top + GAP}px`;
    }
    return style;
  }

  constructor() {
    const destroyRef = inject(DestroyRef);
    const injector = inject(Injector);
    const recalc = () => this._recalc();

    window.addEventListener('resize', recalc);
    window.addEventListener('scroll', recalc, true);
    const onKeydown = (e: KeyboardEvent) => {
      if (!this.tour.active()) return;
      if (e.key === 'Escape') {
        this.skip();
        return;
      }
      // The "chat-input" step (TOUR_STEPS, requiresChatOpen) spotlights
      // the chat textarea WITHOUT disabling it -- it's still a live,
      // typeable field. Arrow keys there are normal text-cursor movement,
      // not a tour-navigation gesture; only treat them as the latter when
      // focus isn't sitting inside an editable control.
      const target = e.target as HTMLElement | null;
      const editable =
        target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable;
      if (editable) return;
      if (e.key === 'ArrowRight') this.next();
      if (e.key === 'ArrowLeft') this.back();
    };
    window.addEventListener('keydown', onKeydown);
    destroyRef.onDestroy(() => {
      window.removeEventListener('resize', recalc);
      window.removeEventListener('scroll', recalc, true);
      window.removeEventListener('keydown', onKeydown);
    });

    // Re-measure whenever the active step changes. Steps inside the chat
    // panel need it opened first — the panel isn't in the DOM until then,
    // so ask the shell to open it and wait a render cycle before measuring.
    //
    // Real bug fixed here: this used to schedule the remeasure via a plain
    // `requestAnimationFrame`, which falls outside this app's zoneless
    // change-detection scheduling (see provideZonelessChangeDetection in
    // index.tsx) — Angular never noticed the signal writes made from
    // inside a raw rAF callback, so `_recalc` silently never ran and the
    // tour got stuck forever on a full-screen dark overlay with no visible
    // spotlight or tooltip (confirmed: a real, reproducible freeze, not a
    // slow one — `_recalc` was called zero times). `afterNextRender` is
    // the zoneless-safe equivalent — Angular's own render-timing hook,
    // guaranteed to integrate with its change-detection scheduler.
    effect(() => {
      const step = this.tour.currentStep();
      const active = this.tour.active();
      if (!active || !step) {
        // Whichever element had focus when the tour started (the "Start
        // tour" trigger, or Skip/Next themselves on the last step) never
        // got it back otherwise -- Angular's @if just unmounts the whole
        // overlay, dropping focus to <body> the same way every other
        // gap this audit found does.
        this.returnFocusTo?.focus?.();
        this.returnFocusTo = null;
        this.rect.set(null);
        this.ready.set(false);
        return;
      }
      if (!this.returnFocusTo) {
        this.returnFocusTo = document.activeElement as HTMLElement | null;
      }
      this.ready.set(false);
      if (step.requiresChatOpen) {
        this.requestChatOpen.emit(true);
      }
      // Two renders: one to let the DOM update from opening the chat
      // panel, one to let the browser complete layout from it.
      afterNextRender(
        () => afterNextRender(() => this._recalc(), { injector }),
        { injector },
      );
    });
  }

  private _recalc(attempt = 0) {
    const step = this.tour.currentStep();
    if (!step || !this.tour.active()) return;
    const el = document.querySelector(step.target);
    if (!el) {
      // Target not in the DOM yet (e.g. chat panel still animating in) —
      // retry shortly rather than showing a stale/empty spotlight. Capped
      // so a step whose target selector is wrong (a future data-tour
      // rename that forgets to update TOUR_STEPS) fails loud by skipping
      // ahead instead of leaving the student stuck on a frozen, unusable
      // full-screen mask forever with no visible way out except Skip.
      if (attempt >= 20) {
        this.tour.next();
        return;
      }
      setTimeout(() => this._recalc(attempt + 1), 60);
      return;
    }
    const r = el.getBoundingClientRect();
    this.rect.set({
      top: r.top - PAD,
      left: r.left - PAD,
      width: r.width + PAD * 2,
      height: r.height + PAD * 2,
    });
    this.ready.set(true);
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    // ready just flipped true this same tick -- the tooltip's #tooltip
    // node doesn't exist until Angular's next render, so grab it a tick
    // later rather than reading the (still-undefined) viewChild now.
    afterNextRender(() => this.tooltip()?.nativeElement.focus({ preventScroll: true }), {
      injector: this.injector,
    });
  }

  next() {
    this.tour.next();
  }

  back() {
    this.tour.back();
  }

  skip() {
    this.tour.end();
  }
}
