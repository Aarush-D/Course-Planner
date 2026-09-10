import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CoursePlan, GenEdSlot } from '../../models/course-plan.model';
import { BackendService, GenEdDomainInfo } from '../../services/backend.service';
import { PlannerStateService } from '../../services/planner-state.service';
import { ToastService } from '../../services/toast.service';
import { GenEdPageComponent } from './gen-ed-page.component';

function makeSlot(id: number, domains: string[], done = false): GenEdSlot {
  return {
    id,
    label: `GEN ED (${domains.join('/')})`,
    domains,
    isChoice: domains.length > 1,
    credits: 3,
    done,
    satisfiedBy: done ? 'DONE 1' : null,
  };
}

/** A big enough GH pool that "every list in the DOM at once" would be
 * obviously measurable -- the point of the lazy-render test below. */
function makeCourseMap(): Record<string, GenEdDomainInfo> {
  const gh = Array.from({ length: 300 }, (_, i) => ({
    code: `HIST ${100 + i}`,
    title: `History course ${i}`,
    credits: '3',
  }));
  return {
    GH: { name: 'Humanities', creditsRequired: 3, courses: gh },
    GA: { name: 'Arts', creditsRequired: 3, courses: [{ code: 'ART 100', title: 'Intro Art', credits: '3' }] },
  };
}

function setup(slots: GenEdSlot[]) {
  const coursePlan = signal<CoursePlan | null>({
    progress: {
      doneItems: 0,
      totalItems: slots.length,
      creditsDone: 0,
      totalCredits: slots.length * 3,
      byCategory: {
        gen_ed: { doneItems: 0, totalItems: slots.length, creditsDone: 0, totalCredits: slots.length * 3, percent: 0 },
      },
    },
    genEdDetail: { slots, ambiguousCourses: [] },
  } as unknown as CoursePlan);
  const state = signal({ wantedCourses: [] as string[] });

  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      { provide: PlannerStateService, useValue: { coursePlan, state, chatOpen: signal(false) } },
      { provide: BackendService, useValue: { genEdCourses: vi.fn().mockResolvedValue(makeCourseMap()) } },
      { provide: ToastService, useValue: { show: vi.fn() } },
    ],
  });

  const fixture = TestBed.createComponent(GenEdPageComponent);
  return { fixture, component: fixture.componentInstance, coursePlan };
}

/** The constructor's genEdCourses().then(...) is a plain promise chain, not
 * something zoneless whenStable() tracks -- a macrotask turn lets it land. */
const flushCourseMap = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe('GenEdPageComponent', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('counts only not-done slots as open requirements in the header', async () => {
    const { fixture, component } = setup([makeSlot(1, ['GH']), makeSlot(2, ['GA'], true)]);
    await flushCourseMap();
    fixture.detectChanges();

    expect(component.openSlotCount()).toBe(1);
    const header: HTMLElement = fixture.nativeElement;
    expect(header.textContent).toContain('1 open requirement in your plan');
    // Still renders every PSU domain card -- the whole reason the label
    // has to say "open" instead of implying it counts the cards below.
    expect(header.querySelectorAll('details').length).toBeGreaterThan(1);
  });

  it('renders a browse list only after its <details> is opened', async () => {
    const { fixture } = setup([makeSlot(1, ['GH'])]);
    await flushCourseMap();
    fixture.detectChanges();

    const root: HTMLElement = fixture.nativeElement;
    // Nothing expanded yet: no course rows anywhere, even though GH alone
    // has 300 courses in the fixture's pool.
    expect(root.querySelectorAll('details ul li').length).toBe(0);

    const ghDetails = [...root.querySelectorAll<HTMLDetailsElement>('details')].find((d) =>
      d.closest('[class*="rounded-lg"]')?.textContent?.includes('Humanities (GH)'),
    )!;
    expect(ghDetails).toBeTruthy();

    ghDetails.open = true;
    ghDetails.dispatchEvent(new Event('toggle'));
    fixture.detectChanges();

    expect(ghDetails.querySelectorAll('ul li').length).toBe(300);
    // Other cards stay unrendered -- opening one doesn't pull in the rest.
    expect(root.querySelectorAll('details ul li').length).toBe(300);

    ghDetails.open = false;
    ghDetails.dispatchEvent(new Event('toggle'));
    fixture.detectChanges();
    expect(root.querySelectorAll('details ul li').length).toBe(0);
  });

  it('keeps the department filter working on an opened list', async () => {
    const { fixture, component } = setup([makeSlot(1, ['GH'])]);
    await flushCourseMap();
    fixture.detectChanges();

    const root: HTMLElement = fixture.nativeElement;
    const ghDetails = [...root.querySelectorAll<HTMLDetailsElement>('details')].find((d) =>
      d.closest('[class*="rounded-lg"]')?.textContent?.includes('Humanities (GH)'),
    )!;
    ghDetails.open = true;
    ghDetails.dispatchEvent(new Event('toggle'));
    fixture.detectChanges();
    expect(ghDetails.querySelectorAll('ul li').length).toBe(300);

    // A prefix with no courses in the pool empties the list in place.
    component.setDeptFilter('GH', 'NOPE');
    fixture.detectChanges();
    expect(ghDetails.querySelectorAll('ul li').length).toBe(0);
    expect(ghDetails.textContent).toContain('No courses match this filter.');

    component.setDeptFilter('GH', 'NOPE'); // toggle off
    fixture.detectChanges();
    expect(ghDetails.querySelectorAll('ul li').length).toBe(300);
  });
});

/** Auto-fill must tell "the service was unreachable" apart from "no course
 * exists for this requirement". They used to collapse into the same null,
 * so a dropped request produced a false claim about the student's degree
 * ("No eligible course found") on the one button whose job is to find
 * one. These pin the distinction at the component boundary. */
describe('GenEdPageComponent.onAutofill', () => {
  beforeEach(() => TestBed.resetTestingModule());

  function setupAutofill(genEdAutofill: (domain: string) => Promise<unknown>) {
    const slot = makeSlot(1, ['GA', 'GH']);
    const coursePlan = signal<CoursePlan | null>({
      progress: { doneItems: 0, totalItems: 1, creditsDone: 0, totalCredits: 3, byCategory: {} },
      genEdDetail: { slots: [slot], ambiguousCourses: [] },
    } as unknown as CoursePlan);
    const state = signal({
      major: 'CMPSC', catalogYear: 2025, startYear: 2026, completed: [],
      additionalMajors: [], minors: [], wantedCourses: [], excludedCourses: [],
    });
    const addWantedCourse = vi.fn().mockResolvedValue(undefined);
    const show = vi.fn();
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: PlannerStateService, useValue: { coursePlan, state, chatOpen: signal(false), addWantedCourse } },
        {
          provide: BackendService,
          useValue: { genEdCourses: vi.fn().mockResolvedValue(makeCourseMap()), genEdAutofill: vi.fn(genEdAutofill) },
        },
        { provide: ToastService, useValue: { show } },
      ],
    });
    const fixture = TestBed.createComponent(GenEdPageComponent);
    return { component: fixture.componentInstance, slot, show, addWantedCourse };
  }

  const lastToast = (show: ReturnType<typeof vi.fn>) => String(show.mock.calls.at(-1)?.[0] ?? '');

  it('reports a service failure, not "no eligible course", when every domain request throws', async () => {
    const { component, slot, show, addWantedCourse } = setupAutofill(() => Promise.reject(new Error('net down')));
    await component.onAutofill(slot);

    expect(lastToast(show)).toMatch(/reach the course service/i);
    expect(lastToast(show)).not.toMatch(/no eligible course/i);
    expect(addWantedCourse).not.toHaveBeenCalled();
    expect(component.autofillingSlotId()).toBeNull();
  });

  it('reports "no eligible course" only when the backend genuinely answered null', async () => {
    const { component, slot, show } = setupAutofill(() => Promise.resolve(null));
    await component.onAutofill(slot);

    expect(lastToast(show)).toMatch(/no eligible course/i);
    expect(lastToast(show)).not.toMatch(/reach the course service/i);
  });

  it('keeps trying later domains after one request fails, and adds the course it finds', async () => {
    const { component, slot, show, addWantedCourse } = setupAutofill((domain) =>
      domain === 'GA'
        ? Promise.reject(new Error('net down'))
        : Promise.resolve({ code: 'HIST 100', name: 'History course 0', credits: 3, bonusDomain: null }),
    );
    await component.onAutofill(slot);

    expect(addWantedCourse).toHaveBeenCalledWith('HIST 100');
    expect(lastToast(show)).toMatch(/Added HIST 100/);
    expect(lastToast(show)).not.toMatch(/reach the course service/i);
  });
});
