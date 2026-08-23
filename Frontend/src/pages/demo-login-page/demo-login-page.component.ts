import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { PlannerStateService } from '../../services/planner-state.service';

interface DemoProfile {
  id: string;
  name: string;
  major: string;
  majorLabel: string;
  standingPrompt: string;
  standingLabel: string;
  minors: string[];
  minorLabels: string[];
  blurb: string;
}

// Real majors/minors already built in Backend/degree_plans and
// Backend/minors, real "class standing" phrases the chat already
// understands (Backend/planner_engine.py's detect_bulk_completion) — no
// invented course lists. See PlannerStateService.loginAsDemoStudent for why
// that matters: every profile's "completed courses" are derived live from
// the real degree plan, not hand-typed here, so they can never drift out
// of sync with it.
const DEMO_PROFILES: DemoProfile[] = [
  {
    id: 'alex',
    name: 'Alex Chen',
    major: 'CMPSC',
    majorLabel: 'Computer Science, B.S.',
    standingPrompt: "I'm a junior",
    standingLabel: 'Junior standing',
    minors: [],
    minorLabels: [],
    blurb: 'Just a major, partway through — the simplest case.',
  },
  {
    id: 'priya',
    name: 'Priya Sharma',
    major: 'NURS',
    majorLabel: 'Nursing, B.S.N.',
    standingPrompt: "I'm a senior",
    standingLabel: 'Senior standing',
    minors: [],
    minorLabels: [],
    blurb: 'Nearly done — shows what the plan looks like close to graduation.',
  },
  {
    id: 'jordan',
    name: 'Jordan Lee',
    major: 'CMPSC',
    majorLabel: 'Computer Science, B.S.',
    standingPrompt: "I've completed 2 years",
    standingLabel: 'Sophomore standing',
    minors: ['MATHMIN'],
    minorLabels: ['Mathematics Minor'],
    blurb: 'A major plus a minor — shows shared/double-counted requirements.',
  },
];

@Component({
  selector: 'app-demo-login-page',
  standalone: true,
  templateUrl: './demo-login-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DemoLoginPageComponent {
  private readonly planner = inject(PlannerStateService);
  private readonly router = inject(Router);

  readonly profiles = DEMO_PROFILES;
  loggingInAs = signal<string | null>(null);

  constructor() {
    // Arriving here at all is already a deliberate choice of onboarding
    // path — the generic "Welcome to Course Planner" setup modal would
    // otherwise stack on top of this page's own profile cards, blocking
    // them, for a visitor who came here directly (not via Home's link).
    this.planner.completeOnboarding();
  }

  initials(name: string): string {
    return name
      .split(' ')
      .map((n) => n[0])
      .join('');
  }

  firstName(name: string): string {
    return name.split(' ')[0];
  }

  async loginAs(profile: DemoProfile) {
    this.loggingInAs.set(profile.id);
    try {
      await this.planner.loginAsDemoStudent(profile.major, profile.standingPrompt, profile.minors);
      await this.router.navigate(['/']);
    } finally {
      this.loggingInAs.set(null);
    }
  }
}
