import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AppComponent } from './app.component';
import { PlannerStateService } from './services/planner-state.service';
import { StudentSessionService } from './services/student-session.service';
import { ThemeService } from './services/theme.service';
import { TourService } from './services/tour.service';

/** Builds the root component with an EMPTY template -- these tests are
 * about the startup effect in its constructor, not the shell it renders,
 * and compiling every child component just to exercise that would make
 * the suite slow for nothing. `startPath` is what location.pathname reads
 * at construction (the value `currentPath` is seeded from). */
async function setup(startPath: string) {
  history.replaceState({}, '', startPath);

  const init = vi.fn().mockResolvedValue(undefined);
  const tryResumeSavedPlan = vi.fn().mockResolvedValue(undefined);
  const fakePlanner = {
    init,
    chatOpen: signal(false),
    onboarded: signal(true),
    state: signal({ major: '', undecided: false }),
    completeOnboarding: vi.fn(),
  };

  TestBed.overrideComponent(AppComponent, { set: { template: '', imports: [] } });
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      { provide: PlannerStateService, useValue: fakePlanner },
      { provide: StudentSessionService, useValue: { tryResumeSavedPlan } },
      { provide: ThemeService, useValue: {} },
      { provide: TourService, useValue: {} },
    ],
  });

  // Required after overrideComponent() replaced the template.
  await TestBed.compileComponents();
  const fixture = TestBed.createComponent(AppComponent);
  fixture.detectChanges();
  return { fixture, component: fixture.componentInstance, init, tryResumeSavedPlan };
}

describe('AppComponent student-shell startup (M3: init after leaving the advisor portal)', () => {
  afterEach(() => {
    history.replaceState({}, '', '/');
  });

  it('runs planner.init() once on a normal student boot', async () => {
    const { init, tryResumeSavedPlan } = await setup('/');
    expect(init).toHaveBeenCalledTimes(1);
    await Promise.resolve();
    expect(tryResumeSavedPlan).toHaveBeenCalledTimes(1);
  });

  it('skips init on an /advisor/* boot, then runs it exactly once when a student route first shows', async () => {
    const { component, fixture, init, tryResumeSavedPlan } = await setup('/advisor/dashboard');
    expect(component.isAdvisorRoute()).toBe(true);
    expect(init).not.toHaveBeenCalled();

    // Advisor moves between advisor pages -- still nothing to start.
    component.currentPath.set('/advisor/review/abc');
    fixture.detectChanges();
    expect(init).not.toHaveBeenCalled();

    // ...then navigates in-app to the student side.
    component.currentPath.set('/');
    fixture.detectChanges();
    expect(init).toHaveBeenCalledTimes(1);
    await Promise.resolve();
    expect(tryResumeSavedPlan).toHaveBeenCalledTimes(1);

    // Further student navigation, or a trip back to the portal and out
    // again, never re-runs it.
    component.currentPath.set('/flowchart');
    fixture.detectChanges();
    component.currentPath.set('/advisor/dashboard');
    fixture.detectChanges();
    component.currentPath.set('/progress');
    fixture.detectChanges();
    expect(init).toHaveBeenCalledTimes(1);
  });
});
