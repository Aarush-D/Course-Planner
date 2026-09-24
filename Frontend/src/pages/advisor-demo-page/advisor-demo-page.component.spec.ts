import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';
import { AdvisorDemoPageComponent } from './advisor-demo-page.component';

describe('AdvisorDemoPageComponent', () => {
  let demo: AdvisorDemoPageComponent;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    demo = TestBed.createComponent(AdvisorDemoPageComponent).componentInstance;
  });

  it('starts on the advisor screen with one open request, on Jordan', () => {
    expect(demo.role()).toBe('advisor');
    expect(demo.openMeetings().length).toBe(1);
    expect(demo.openCountFor('jordan')).toBe(1);
    expect(demo.openCountFor('alex')).toBe(0);
  });

  it('lets a student request a meeting and blocks a second open one', () => {
    demo.setRole('student');
    demo.select('alex');
    demo.topicDraft.set('Push CMPSC 311 to spring?');
    demo.requestMeeting();
    expect(demo.openCountFor('alex')).toBe(1);
    expect(demo.hasOpenForSelected()).toBe(true);

    demo.topicDraft.set('Another one');
    demo.requestMeeting();
    expect(demo.openCountFor('alex')).toBe(1);
    expect(demo.error()).toContain('already a pending request');
  });

  it('requires a time to confirm, then records the confirmation', () => {
    const id = demo.meetings()[0].id;
    demo.respond(id, 'confirmed');
    expect(demo.error()).toContain('Pick a date and time');
    expect(demo.meetings()[0].status).toBe('requested');

    demo.confirmTime.set('2026-09-30T14:30');
    demo.respond(id, 'confirmed');
    expect(demo.meetings()[0].status).toBe('confirmed');
    expect(demo.meetings()[0].confirmedAt).toBe('2026-09-30T14:30');
    expect(demo.openMeetings().length).toBe(0);
  });

  it('declines without needing a time, and lets the student cancel', () => {
    const id = demo.meetings()[0].id;
    demo.respond(id, 'declined');
    expect(demo.meetings()[0].status).toBe('declined');

    demo.setRole('student');
    demo.select('alex');
    demo.topicDraft.set('Quick question');
    demo.requestMeeting();
    const alexMeeting = demo.meetings().find((m) => m.studentId === 'alex')!;
    demo.cancelMeeting(alexMeeting.id);
    expect(demo.meetings().find((m) => m.id === alexMeeting.id)!.status).toBe('cancelled');
    expect(demo.hasOpenForSelected()).toBe(false);
  });

  it('posts comments as whichever role is showing', () => {
    demo.select('alex');
    const before = demo.thread().length;
    demo.commentDraft.set('See you Tuesday');
    demo.postComment();
    expect(demo.thread().length).toBe(before + 1);
    expect(demo.thread().at(-1)!.from).toBe('advisor');

    demo.setRole('student');
    demo.commentDraft.set('Thanks!');
    demo.postComment();
    expect(demo.thread().at(-1)!.from).toBe('student');
  });
});
