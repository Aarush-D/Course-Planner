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

export const TOUR_STEPS: TourStep[] = [
  {
    target: '[data-tour="nav-home"]',
    title: 'Home',
    body: 'Your dashboard — overall percent complete, credits earned, whether you’re on pace to graduate, and what’s next. This is where you land after telling the advisor your major.',
  },
  {
    target: '[data-tour="nav-flowchart"]',
    title: 'Flowchart',
    body: 'Your full path to graduation, laid out term by term. Each course shows its credits, and a term’s credit-load badge flags real PSU billing facts — part-time (under 12cr) or an extra-fee overload (over 19cr).',
  },
  {
    target: '[data-tour="nav-progress"]',
    title: 'Progress',
    body: 'How much of your degree is done, broken down by category — major requirements, Gen Ed, and a separate bucket for every minor or additional major you’ve added.',
  },
  {
    target: '[data-tour="nav-gen-ed"]',
    title: 'General education',
    body: 'Browse Gen Ed requirements by domain. This page is still In Construction — Gen Ed picks already show up mixed into your Flowchart and Recommendations today.',
  },
  {
    target: '[data-tour="nav-transferred"]',
    title: 'Transferred courses',
    body: 'Check how credits from another school would transfer in. Also In Construction — being built on top of PSU’s real Transfer Credit Tool data.',
  },
  {
    target: '[data-tour="nav-recommendations"]',
    title: 'Recommendations',
    body: 'Courses you’re eligible for right now, ranked by how central they are to unlocking the rest of your plan — not just whatever comes next on the flowchart.',
  },
  {
    target: '[data-tour="nav-your-plan"]',
    title: 'Your plan',
    body: 'Campus, major, minors, double/triple-major slots, when you started, and how many years to graduate in — all the "set this once" basics live here, not in chat. Not sure of a major yet? Check "I\'m undecided" and chat with the advisor about your interests instead — it\'ll ask a few questions and suggest real majors.',
  },
  {
    target: '[data-tour="help-button"]',
    title: 'Quick help',
    body: 'A quick-reference summary of everything in this tour, any time you need a reminder — no need to restart the full walkthrough.',
  },
  {
    target: '[data-tour="chat-toggle"]',
    title: 'Advisor chat',
    body: 'Now that your major/campus/minors are set, this is where you talk to the planner — type in plain English, e.g. “I’ve taken CMPSC 131 and Calc 1, what’s next?” The full conversation is remembered even if you close and reopen this panel.',
  },
  {
    target: '[data-tour="chat-summer"]',
    title: 'Allow summer courses',
    body: 'Toggle this on if you’re open to summer terms — it can shorten an otherwise tight timeline by spreading credits across an extra term each year. This is the one setting that lives in chat, not the sidebar, since it’s the kind of thing you might reconsider mid-conversation.',
    requiresChatOpen: true,
  },
  {
    target: '[data-tour="chat-input"]',
    title: 'Tell it what you’ve taken',
    body: 'Type in plain English — course codes, common names ("calc 1"), or bigger phrases like "I’m a junior" or "I’ve done everything except my last year." The planner matches what it can and asks about the rest.',
    requiresChatOpen: true,
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
