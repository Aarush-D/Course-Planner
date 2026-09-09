import { signal, WritableSignal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PlannerStateService } from '../../services/planner-state.service';
import { StudentProfileService } from '../../services/student-profile.service';
import { StudentSessionService } from '../../services/student-session.service';
import { SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { AccountMenuComponent } from './account-menu.component';

type FakeSession = { user: { id: string; email: string } } | null;

/** Builds the component with every dependency faked. `session` is a real
 * writable signal so a test can flip the signed-in user the same way
 * SupabaseService's onAuthStateChange does, and `profiles` is a per-user
 * lookup table so the fetch returns whichever user is signed in at the
 * moment the request is made. */
function setup(profiles: Record<string, { linkedinUrl: string | null; isLinkedinPublic: boolean }>) {
  const session: WritableSignal<FakeSession> = signal(null);
  const getMyProfile = vi.fn(async () => {
    const id = session()?.user.id;
    if (!id || !profiles[id]) throw new Error('no session');
    return profiles[id];
  });
  const updateProfile = vi.fn().mockResolvedValue(undefined);
  const signOutStudent = vi.fn().mockResolvedValue(undefined);
  const deleteMyAccount = vi.fn().mockResolvedValue(undefined);
  const stopAutosave = vi.fn();
  const tryResumeSavedPlan = vi.fn().mockResolvedValue(undefined);
  const resetToDefault = vi.fn();

  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      { provide: SupabaseService, useValue: { session, signOutStudent, deleteMyAccount } },
      { provide: StudentProfileService, useValue: { getMyProfile, updateProfile } },
      { provide: StudentSessionService, useValue: { stopAutosave, tryResumeSavedPlan } },
      { provide: PlannerStateService, useValue: { resetToDefault } },
      { provide: ToastService, useValue: { show: vi.fn() } },
    ],
  });

  const fixture = TestBed.createComponent(AccountMenuComponent);
  return {
    fixture,
    component: fixture.componentInstance,
    session,
    getMyProfile,
    updateProfile,
    deleteMyAccount,
    stopAutosave,
    tryResumeSavedPlan,
    resetToDefault,
  };
}

const userA = { user: { id: 'user-a', email: 'a@psu.edu' } };
const userB = { user: { id: 'user-b', email: 'b@psu.edu' } };

describe('AccountMenuComponent per-user profile state', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('never carries user A’s LinkedIn URL over to user B after sign out / sign in (the reported leak)', async () => {
    const { component, session, getMyProfile, updateProfile } = setup({
      'user-a': { linkedinUrl: 'https://linkedin.com/in/user-a', isLinkedinPublic: true },
      'user-b': { linkedinUrl: null, isLinkedinPublic: false },
    });

    session.set(userA);
    await component.toggleOpen();
    expect(component.linkedinUrl()).toBe('https://linkedin.com/in/user-a');
    expect(component.linkedinPublic()).toBe(true);

    await component.signOut();
    session.set(null);
    // Signed out: nothing of A's may linger in the fields.
    expect(component.linkedinUrl()).toBe('');
    expect(component.linkedinPublic()).toBe(false);

    session.set(userB);
    await component.toggleOpen();
    // B's own (empty) profile was fetched, not A's cached one.
    expect(getMyProfile).toHaveBeenCalledTimes(2);
    expect(component.linkedinUrl()).toBe('');
    expect(component.linkedinPublic()).toBe(false);

    // And the thing that made the bug destructive: "Save LinkedIn" as B
    // must upsert B's values, never A's URL onto B's row.
    await component.saveLinkedin();
    expect(updateProfile).toHaveBeenCalledWith(null, false);
  });

  it('refetches when the session user changes even without an explicit sign out (latch is keyed on user id)', async () => {
    const { component, session, getMyProfile } = setup({
      'user-a': { linkedinUrl: 'https://linkedin.com/in/user-a', isLinkedinPublic: false },
      'user-b': { linkedinUrl: 'https://linkedin.com/in/user-b', isLinkedinPublic: true },
    });

    session.set(userA);
    await component.toggleOpen();
    expect(component.linkedinUrl()).toBe('https://linkedin.com/in/user-a');
    component.open.set(false);

    // Same user reopening the menu: no refetch (the lazy-load latch holds).
    await component.toggleOpen();
    expect(getMyProfile).toHaveBeenCalledTimes(1);
    component.open.set(false);

    // Different user: the latch must not hold.
    session.set(userB);
    await component.toggleOpen();
    expect(getMyProfile).toHaveBeenCalledTimes(2);
    expect(component.linkedinUrl()).toBe('https://linkedin.com/in/user-b');
    expect(component.linkedinPublic()).toBe(true);
  });

  it('resets the cached profile after a successful account deletion', async () => {
    const { component, session, stopAutosave, resetToDefault } = setup({
      'user-a': { linkedinUrl: 'https://linkedin.com/in/user-a', isLinkedinPublic: true },
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    session.set(userA);
    await component.toggleOpen();
    expect(component.linkedinUrl()).toBe('https://linkedin.com/in/user-a');

    await component.deleteAccount();

    expect(stopAutosave).toHaveBeenCalled();
    expect(resetToDefault).toHaveBeenCalled();
    expect(component.linkedinUrl()).toBe('');
    expect(component.linkedinPublic()).toBe(false);
  });

  it('re-arms autosave (tryResumeSavedPlan) when the delete RPC fails, since the student is still signed in', async () => {
    const { component, session, deleteMyAccount, stopAutosave, tryResumeSavedPlan, resetToDefault } = setup({
      'user-a': { linkedinUrl: 'https://linkedin.com/in/user-a', isLinkedinPublic: true },
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    deleteMyAccount.mockRejectedValue(new Error('network'));

    session.set(userA);
    await component.toggleOpen();
    await component.deleteAccount();

    expect(stopAutosave).toHaveBeenCalled();
    expect(tryResumeSavedPlan).toHaveBeenCalledTimes(1);
    // Failure path keeps the pre-deletion state on screen, profile included.
    expect(resetToDefault).not.toHaveBeenCalled();
    expect(component.linkedinUrl()).toBe('https://linkedin.com/in/user-a');
    expect(component.deleting()).toBe(false);
  });
});
