import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  output,
  signal,
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
      ? { top: `${r.top}px`, height: `${r.height}px`, left: `${r.left + r.width}px` }
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

  private _tooltipPlacement(): 'below' | 'above' {
    const r = this.rect();
    if (!r) return 'below';
    const spaceBelow = window.innerHeight - (r.top + r.height);
    return spaceBelow < 200 && r.top > 200 ? 'above' : 'below';
  }

  private _tooltipStyle() {
    const r = this.rect();
    if (!r) return { display: 'none' };
    const left = Math.min(
      Math.max(r.left, VIEWPORT_MARGIN),
      window.innerWidth - TOOLTIP_WIDTH - VIEWPORT_MARGIN,
    );
    const style: Record<string, string> = { left: `${left}px`, width: `${TOOLTIP_WIDTH}px` };
    if (this._tooltipPlacement() === 'below') {
      style['top'] = `${r.top + r.height + GAP}px`;
    } else {
      style['bottom'] = `${window.innerHeight - r.top + GAP}px`;
    }
    return style;
  }

  constructor() {
    const destroyRef = inject(DestroyRef);
    const recalc = () => this._recalc();

    window.addEventListener('resize', recalc);
    window.addEventListener('scroll', recalc, true);
    const onKeydown = (e: KeyboardEvent) => {
      if (!this.tour.active()) return;
      if (e.key === 'Escape') this.skip();
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
    effect(() => {
      const step = this.tour.currentStep();
      const active = this.tour.active();
      if (!active || !step) {
        this.rect.set(null);
        this.ready.set(false);
        return;
      }
      this.ready.set(false);
      if (step.requiresChatOpen) {
        this.requestChatOpen.emit(true);
      }
      // Double rAF: one to let Angular flush the DOM update from opening
      // the chat panel, one to let the browser complete layout from it.
      requestAnimationFrame(() => requestAnimationFrame(() => this._recalc()));
    });
  }

  private _recalc() {
    const step = this.tour.currentStep();
    if (!step || !this.tour.active()) return;
    const el = document.querySelector(step.target);
    if (!el) {
      // Target not in the DOM yet (e.g. chat panel still animating in) —
      // retry shortly rather than showing a stale/empty spotlight.
      setTimeout(() => this._recalc(), 60);
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
