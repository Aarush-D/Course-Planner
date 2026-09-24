import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

type Role = 'advisor' | 'student';
type MeetingStatus = 'requested' | 'confirmed' | 'declined' | 'cancelled';

interface DemoTerm {
  label: string;
  courses: string[];
  done: boolean;
}

interface DemoStudent {
  id: string;
  name: string;
  major: string;
  standing: string;
  credits: number;
  creditsRequired: number;
  gpa: string;
  flag: string | null;
  terms: DemoTerm[];
}

interface DemoComment {
  id: number;
  studentId: string;
  from: Role;
  body: string;
  at: Date;
}

interface DemoMeeting {
  id: number;
  studentId: string;
  topic: string;
  preferred: string;
  status: MeetingStatus;
  confirmedAt: string | null;
  response: string | null;
}

/** A self-contained, no-login walkthrough of the advisor workspace: the
 * roster, one student's plan snapshot, the comment thread, and the
 * student-initiated meeting request flow, with a switch between the
 * advisor's screen and the student's so both halves of each action are
 * visible. Entirely local sample data -- nothing here reads or writes
 * Supabase, so it's safe to open with no session and can't touch a real
 * roster. Course codes are real Penn State CMPSC/MATH/STAT numbers; the
 * people, GPAs and messages are invented. */
@Component({
  selector: 'app-advisor-demo-page',
  standalone: true,
  templateUrl: './advisor-demo-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe],
})
export class AdvisorDemoPageComponent {
  readonly students: DemoStudent[] = [
    {
      id: 'alex',
      name: 'Alex Chen',
      major: 'Computer Science, B.S.',
      standing: 'Junior',
      credits: 62,
      creditsRequired: 120,
      gpa: '3.41',
      flag: null,
      terms: [
        { label: 'Fall 2025', courses: ['CMPSC 121', 'MATH 140', 'ENGL 15'], done: true },
        { label: 'Spring 2026', courses: ['CMPSC 122', 'MATH 141', 'STAT 318'], done: true },
        { label: 'Fall 2026', courses: ['CMPSC 360', 'CMPSC 311', 'CMPSC 221'], done: false },
        { label: 'Spring 2027', courses: ['CMPSC 461', 'CMPSC 465', 'STAT 414'], done: false },
      ],
    },
    {
      id: 'priya',
      name: 'Priya Sharma',
      major: 'Nursing, B.S.N.',
      standing: 'Senior',
      credits: 108,
      creditsRequired: 120,
      gpa: '3.78',
      flag: null,
      terms: [
        { label: 'Spring 2026', courses: ['NURS 306', 'NURS 309'], done: true },
        { label: 'Fall 2026', courses: ['NURS 412', 'NURS 413'], done: false },
        { label: 'Spring 2027', courses: ['NURS 460', 'NURS 495'], done: false },
      ],
    },
    {
      id: 'jordan',
      name: 'Jordan Lee',
      major: 'Computer Science, B.S. + Math minor',
      standing: 'Sophomore',
      credits: 41,
      creditsRequired: 120,
      gpa: '2.94',
      flag: 'Math prerequisite chain is a term behind',
      terms: [
        { label: 'Fall 2025', courses: ['CMPSC 121', 'MATH 140'], done: true },
        { label: 'Spring 2026', courses: ['CMPSC 122', 'MATH 140 (retake)'], done: true },
        { label: 'Fall 2026', courses: ['CMPSC 131', 'MATH 141', 'STAT 200'], done: false },
      ],
    },
  ];

  role = signal<Role>('advisor');
  selectedId = signal<string>('alex');
  private nextId = 100;

  comments = signal<DemoComment[]>([
    {
      id: 1,
      studentId: 'alex',
      from: 'advisor',
      body: 'Nice work this term. CMPSC 360 is a heavy one, so I would not stack it with CMPSC 311 unless you are comfortable with proofs.',
      at: new Date('2026-09-14T15:20:00'),
    },
    {
      id: 2,
      studentId: 'alex',
      from: 'student',
      body: 'Thanks. I was worried about that too. Should I push 311 to the spring?',
      at: new Date('2026-09-14T18:05:00'),
    },
    {
      id: 3,
      studentId: 'jordan',
      from: 'advisor',
      body: 'Jordan, MATH 141 is the gate for most of your CMPSC upper levels. Can we find time this week to talk through options?',
      at: new Date('2026-09-18T13:00:00'),
    },
  ]);

  meetings = signal<DemoMeeting[]>([
    {
      id: 1,
      studentId: 'jordan',
      topic: 'Getting back on track with the math sequence',
      preferred: 'Wednesday or Thursday afternoon',
      status: 'requested',
      confirmedAt: null,
      response: null,
    },
  ]);

  commentDraft = signal('');
  topicDraft = signal('');
  timesDraft = signal('');
  confirmTime = signal('');
  responseMsg = signal('');
  error = signal<string | null>(null);

  selected = computed(() => this.students.find((s) => s.id === this.selectedId()) ?? this.students[0]);
  thread = computed(() => this.comments().filter((c) => c.studentId === this.selectedId()));
  studentMeetings = computed(() => this.meetings().filter((m) => m.studentId === this.selectedId()));
  openMeetings = computed(() => this.meetings().filter((m) => m.status === 'requested'));
  hasOpenForSelected = computed(() => this.studentMeetings().some((m) => m.status === 'requested'));
  progressPct = computed(() => Math.round((this.selected().credits / this.selected().creditsRequired) * 100));

  setRole(role: Role) {
    this.role.set(role);
    this.error.set(null);
  }

  select(id: string) {
    this.selectedId.set(id);
    this.error.set(null);
  }

  nameFor(id: string): string {
    return this.students.find((s) => s.id === id)?.name ?? 'A student';
  }

  openCountFor(id: string): number {
    return this.openMeetings().filter((m) => m.studentId === id).length;
  }

  postComment() {
    const body = this.commentDraft().trim();
    if (!body) return;
    this.comments.update((c) => [
      ...c,
      { id: this.nextId++, studentId: this.selectedId(), from: this.role(), body, at: new Date() },
    ]);
    this.commentDraft.set('');
  }

  requestMeeting() {
    const topic = this.topicDraft().trim();
    if (!topic) return;
    if (this.hasOpenForSelected()) {
      this.error.set('There is already a pending request. Cancel it first to send a new one.');
      return;
    }
    this.meetings.update((m) => [
      {
        id: this.nextId++,
        studentId: this.selectedId(),
        topic,
        preferred: this.timesDraft().trim(),
        status: 'requested',
        confirmedAt: null,
        response: null,
      },
      ...m,
    ]);
    this.topicDraft.set('');
    this.timesDraft.set('');
    this.error.set(null);
  }

  cancelMeeting(id: number) {
    this.meetings.update((m) => m.map((x) => (x.id === id ? { ...x, status: 'cancelled' as MeetingStatus } : x)));
  }

  respond(id: number, status: 'confirmed' | 'declined') {
    if (status === 'confirmed' && !this.confirmTime()) {
      this.error.set('Pick a date and time before confirming.');
      return;
    }
    const at = status === 'confirmed' ? this.confirmTime() : null;
    const msg = this.responseMsg().trim() || null;
    this.meetings.update((m) =>
      m.map((x) => (x.id === id ? { ...x, status, confirmedAt: at, response: msg } : x)),
    );
    this.confirmTime.set('');
    this.responseMsg.set('');
    this.error.set(null);
  }

  statusLabel(status: MeetingStatus): string {
    const forAdvisor = this.role() === 'advisor';
    switch (status) {
      case 'requested':
        return forAdvisor ? 'Needs a reply' : 'Waiting for a reply';
      case 'confirmed':
        return 'Confirmed';
      case 'declined':
        return 'Declined';
      default:
        return forAdvisor ? 'Cancelled by student' : 'Cancelled';
    }
  }

  reset() {
    this.comments.set([]);
    this.meetings.set([]);
    this.error.set(null);
  }
}
