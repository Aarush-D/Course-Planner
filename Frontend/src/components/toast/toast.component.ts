import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  inject,
  viewChildren,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { animate } from 'motion';
import { prefersReducedMotion } from '../../animations/reduced-motion';
import { ToastService } from '../../services/toast.service';

@Component({
  selector: 'app-toast',
  standalone: true,
  templateUrl: './toast.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ToastComponent {
  readonly toast = inject(ToastService);
  private readonly injector = inject(Injector);
  private readonly destroyRef = inject(DestroyRef);

  // Matched against each toast's [attr.data-toast-id] rather than assumed
  // index alignment -- more robust if the @for list ever reorders.
  private readonly toastEls = viewChildren('toastEl', { read: ElementRef<HTMLElement> });

  constructor() {
    // Auto-dismiss (the service's own timer) and the close button both
    // fire this same path -- one place that plays the exit animation
    // before the toast actually disappears, instead of two.
    this.toast.dismissRequested.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((id) => this.animateAndDismiss(id));
  }

  async animateAndDismiss(id: number): Promise<void> {
    const el = this.toastEls().find((ref) => ref.nativeElement.dataset['toastId'] === String(id))?.nativeElement;
    if (el && !prefersReducedMotion()) {
      await animate(el, { opacity: 0, transform: 'translateX(-8px)' }, { duration: 0.15 }).finished;
    }
    // animate()'s promise resolves via motion's own rAF ticker, outside
    // zoneless Angular's change-detection awareness -- same class of
    // callback that needed this guard in tour-overlay.component.ts.
    afterNextRender(() => this.toast.dismiss(id), { injector: this.injector });
  }
}
