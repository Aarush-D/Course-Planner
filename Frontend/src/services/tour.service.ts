import { Injectable, computed, signal } from '@angular/core';

export interface TourStep {
  /** CSS selector for the element to spotlight — must match a real
   * `data-tour="..."` attribute in the DOM, never a generic class selector
   * that could drift as styling changes. */
  target: string;
  title: string;
  body: string;
  /** True for steps that live inside the chat panel — the panel is only
   * in the DOM while open, so the tour opens it before measuring these. */
  requiresChatOpen?: boolean;
}

// Kept deliberately short — 5 steps covering the big things, not a
// step-per-page walkthrough. The 7 individual nav-item steps this used to
// have were consolidated into one sidebar-overview step, and the summer-
// courses toggle was dropped from the tour entirely (it's still in the
// product, just not worth a dedicated step in a "short and sweet" tour).
export const TOUR_STEPS: TourStep[] = [
  {
    target: '[data-tour="nav-sidebar"]',
    title: 'Your sidebar',
    body: 'Everything you need to see lives here — your dashboard, Flowchart, Progress, Recommendations, Gen Ed, Transferred Courses, and Your Plan (campus/major/minors/start year). Come back any time to change your mind about any of it.',
  },
  {
    target: '[data-tour="chat-toggle"]',
    title: 'Chat with advisor',
    body: 'This is where you talk to the advisor — type in plain English, e.g. “I’ve taken CMPSC 131 and Calc 1, what’s next?” The full conversation is remembered even if you close and reopen this panel.',
  },
  {
    target: '[data-tour="chat-input"]',
    title: 'Tell it what you’ve taken',
    body: 'Type in plain English — course codes, common names ("calc 1"), or bigger phrases like "I’m a junior." Or click the upload button to hand it a PDF transcript instead — either way, it matches your completed courses against the real catalog and builds your plan from there.',
    requiresChatOpen: true,
  },
  {
    target: '[data-tour="theme-toggle"]',
    title: 'Light / dark mode',
    body: 'Switch between a white and a dark background any time — it starts in light mode and remembers whichever you pick for next time.',
  },
  {
    target: '[data-tour="help-button"]',
    title: 'Quick help',
    body: 'A quick-reference summary of everything in this tour, any time you need a reminder — no need to restart the full walkthrough.',
  },
];

@Injectable({ providedIn: 'root' })
export class TourService {
  readonly active = signal(false);
  readonly stepIndex = signal(0);
  readonly steps = TOUR_STEPS;

  readonly currentStep = computed<TourStep | undefined>(() => this.steps[this.stepIndex()]);
  readonly isFirst = computed(() => this.stepIndex() === 0);
  readonly isLast = computed(() => this.stepIndex() === this.steps.length - 1);
  readonly stepCount = computed(() => this.steps.length);

  start() {
    this.stepIndex.set(0);
    this.active.set(true);
  }

  next() {
    if (this.isLast()) {
      this.end();
      return;
    }
    this.stepIndex.update((i) => i + 1);
  }

  back() {
    if (this.isFirst()) return;
    this.stepIndex.update((i) => i - 1);
  }

  end() {
    this.active.set(false);
  }
}
