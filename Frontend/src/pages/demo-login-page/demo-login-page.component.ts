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
  {
    id: 'marcus',
    name: 'Marcus Webb',
    major: 'AERSP',
    majorLabel: 'Aerospace Engineering, B.S.',
    standingPrompt: "I'm a freshman",
    standingLabel: 'Freshman standing',
    minors: [],
    minorLabels: [],
    blurb: 'Day one, on the standard 4-year plan — shows how the planner flags a timeline that doesn’t fit and suggests summer courses.',
  },
  {
    id: 'elena',
    name: 'Elena Rodriguez',
    major: 'BUSINESS',
    majorLabel: 'Business, B.S. (Intercollege)',
    standingPrompt: "I'm a sophomore",
    standingLabel: 'Sophomore standing',
    minors: [],
    minorLabels: [],
    blurb: 'An Intercollege program instead of a single department — a different plan structure than the majors above.',
  },
  {
    id: 'tyler',
    name: 'Tyler Brooks',
    major: 'KINES',
    majorLabel: 'Kinesiology, B.S.',
    standingPrompt: "I'm a junior",
    standingLabel: 'Junior standing',
    minors: [],
    minorLabels: [],
    blurb: 'College of Health and Human Development, partway through — another real college represented.',
  },
  {
    id: 'sophie',
    name: 'Sophie Nguyen',
    major: 'ARTH',
    majorLabel: 'Art History, B.A.',
    standingPrompt: "I'm a senior",
    standingLabel: 'Senior standing',
    minors: [],
    minorLabels: [],
    blurb: 'College of Arts and Architecture, close to graduation — a very different Gen Ed mix than a STEM plan.',
  },
  {
    id: 'omar',
    name: 'Omar Hassan',
    major: 'ECON',
    majorLabel: 'Economics, B.S.',
    standingPrompt: "I'm a junior",
    standingLabel: 'Junior standing',
    minors: [],
    minorLabels: [],
    blurb: 'College of the Liberal Arts, partway through — shows how a non-STEM flowchart paces out.',
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
