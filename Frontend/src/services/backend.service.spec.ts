import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { BackendService, GenEdAutofillContext } from './backend.service';

/** genEdAutofill's contract is the whole point of these: null means the
 * backend answered and there is genuinely no eligible course; a thrown
 * error means the backend could not be reached. Its caller shows the
 * student different messages for the two, and before this the second
 * case was silently reported as the first. */
describe('BackendService.genEdAutofill', () => {
  let svc: BackendService;
  let http: HttpTestingController;
  const ctx: GenEdAutofillContext = {
    major: 'CMPSC', catalog_year: 2025, start_year: 2026,
    second_major: undefined, additional_majors: [], minors: [],
    completed: [], excluded_courses: [], wanted_courses: [],
  };

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    svc = TestBed.inject(BackendService);
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());

  it('resolves null when the backend answers that nothing is eligible', async () => {
    const p = svc.genEdAutofill('GH', ctx);
    http.expectOne('/api/gen-ed-autofill').flush({ code: null });
    await expect(p).resolves.toBeNull();
  });

  it('maps a real answer, including the cross-domain bonus', async () => {
    const p = svc.genEdAutofill('GH', ctx);
    http.expectOne('/api/gen-ed-autofill').flush({
      code: 'HIST 100', name: 'World History', credits: 3, bonus_domain: 'IL',
    });
    await expect(p).resolves.toEqual({
      code: 'HIST 100', name: 'World History', credits: 3, bonusDomain: 'IL',
    });
  });

  it('THROWS on a transport failure instead of pretending nothing is eligible', async () => {
    const p = svc.genEdAutofill('GH', ctx);
    http.expectOne('/api/gen-ed-autofill').error(new ProgressEvent('error'));
    await expect(p).rejects.toBeTruthy();
  });

  it('THROWS on a 5xx as well', async () => {
    const p = svc.genEdAutofill('GH', ctx);
    http.expectOne('/api/gen-ed-autofill').flush('boom', { status: 503, statusText: 'Service Unavailable' });
    await expect(p).rejects.toBeTruthy();
  });

  it('sends the domain alongside the plan context', async () => {
    const p = svc.genEdAutofill('GA', ctx);
    const req = http.expectOne('/api/gen-ed-autofill');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toMatchObject({ domain: 'GA', major: 'CMPSC', catalog_year: 2025 });
    req.flush({ code: null });
    await p;
  });
});
