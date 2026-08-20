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
    target: '[data-tour="help-button"]',
    title: 'Quick help',
    body: 'A quick-reference summary of everything in this tour, any time you need a reminder — no need to restart the full walkthrough.',
  },
  {
    target: '[data-tour="chat-toggle"]',
    title: 'Advisor chat',
    body: 'This is the main way you talk to the planner. Open it to pick your major and campus, or just type in plain English — e.g. “I’m a CMPSC major who’s taken CMPSC 131 and Calc 1.”',
  },
  {
    target: '[data-tour="chat-campus"]',
    title: 'Campus',
    body: 'Pick your PSU campus. University Park has full degree-plan data today; other campuses are supported in the mechanism but don’t have real plan data loaded yet.',
    requiresChatOpen: true,
  },
  {
    target: '[data-tour="chat-major"]',
    title: 'Major',
    body: 'Search and pick your major — over 150 real PSU majors are supported. You can also just tell the chat your major in plain English instead.',
    requiresChatOpen: true,
  },
  {
    target: '[data-tour="chat-minors"]',
    title: 'Minors',
    body: 'Add one or more minors. Where a minor’s own bulletin lets a course double-count toward your major too, the planner applies that automatically — no double work.',
    requiresChatOpen: true,
  },
  {
    target: '[data-tour="chat-major-count"]',
    title: 'Number of majors',
    body: 'Double or triple majoring? Bump this up and a dropdown appears for each extra major — every picker excludes majors already chosen elsewhere, so you can’t pick the same one twice.',
    requiresChatOpen: true,
  },
  {
    target: '[data-tour="chat-year-planning"]',
    title: 'Year planning',
    body: 'When you started (or plan to start) college, and how many years you’re aiming to graduate in. The plan re-paces itself around whatever you set here.',
    requiresChatOpen: true,
  },
  {
    target: '[data-tour="chat-summer"]',
    title: 'Allow summer courses',
    body: 'Toggle this on if you’re open to summer terms — it can shorten an otherwise tight timeline by spreading credits across an extra term each year.',
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
