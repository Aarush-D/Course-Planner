import { Injectable, signal } from '@angular/core';
import { Subject } from 'rxjs';

export interface Toast {
  id: number;
  text: string;
  kind: 'success' | 'error';
}

const AUTO_DISMISS_MS = 4000;

/** Lightweight confirmation for actions that would otherwise be silent
 * outside the chat panel -- e.g. removing a completed course from the
 * Flowchart page. The chat transcript already carries this kind of
 * feedback when the panel is open; a toast is the same confirmation for
 * when it's closed. */
@Injectable({ providedIn: 'root' })
export class ToastService {
  readonly toasts = signal<Toast[]>([]);
  private nextId = 0;

  /** Auto-dismiss and the close button both funnel through this instead of
   * calling dismiss() directly, so ToastComponent has one single place to
   * play the exit animation before the entry actually disappears. */
  readonly dismissRequested = new Subject<number>();

  show(text: string, kind: Toast['kind'] = 'success') {
    const id = this.nextId++;
    this.toasts.update((list) => [...list, { id, text, kind }]);
    setTimeout(() => this.dismissRequested.next(id), AUTO_DISMISS_MS);
  }

  dismiss(id: number) {
    this.toasts.update((list) => list.filter((t) => t.id !== id));
  }
}
