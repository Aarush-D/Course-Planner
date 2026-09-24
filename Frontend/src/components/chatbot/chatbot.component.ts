import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { TranscriptImportReviewComponent } from '../transcript-import-review/transcript-import-review.component';
import { Course } from '../../models/course-plan.model';
import {
  CourseEnrollmentService, CourseFullError, MyEnrollment, courseFullMessage,
} from '../../services/course-enrollment.service';
import { PlannerStateService } from '../../services/planner-state.service';
import { SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';

/** Inline follow-up shown under a course whose Apply found it full. A full
 * course is refused outright (there is no waitlist -- see
 * CourseEnrollmentService.CourseFullError), so the only question left for
 * the student is whether to take an open sibling option instead:
 * 'finding-alternative' while that search runs, 'alternative-found' with
 * the discovered course awaiting confirmation. No entry at all when the
 * requirement has no other options, or every one of them is full too --
 * the toast already said so. */
interface EnrollmentDecision {
  courseId: string;
  stage: 'finding-alternative' | 'alternative-found';
  alternativeCode?: string;
  alternativeName?: string;
}

/**
 * Now just the conversational surface — free-text input and message
 * history. Campus/Major/Minors/Number-of-majors/Started-college/
 * Graduate-in moved to PlannerSetupComponent (nav sidebar + onboarding
 * modal); "Allow Summer Courses" moved to PreferencesPanelComponent (top
 * header chrome) since it was crowding this panel's header; message
 * history moved to PlannerStateService.chatMessages so it survives this
 * panel closing and reopening. Injects the service directly instead of the
 * input/output plumbing this used when it also owned the settings —
 * that indirection only earned its keep while there was local state
 * needing to be kept in sync with the backend's echoed-back corrections.
 */
@Component({
  selector: 'app-chatbot',
  standalone: true,
  templateUrl: './chatbot.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, TranscriptImportReviewComponent],
  // Fill the parent panel so the inner messages area gets a real height to scroll in.
  host: { class: 'block h-full min-h-0 overflow-hidden' },
})
export class ChatbotComponent {
  readonly planner = inject(PlannerStateService);
  private readonly supabase = inject(SupabaseService);
  private readonly enrollment = inject(CourseEnrollmentService);
  private readonly toast = inject(ToastService);

  prompt = signal<string>('');
  uploadingTranscript = signal(false);

  /** Session-only dismiss for the transcript-review panel below -- same
   * pattern as home-page's transcript-stale nudge. Reset (not just left
   * false) by the effect in the constructor whenever a NEW upload lands,
   * so dismissing a previous upload's review doesn't silently suppress the
   * next one too. */
  private readonly transcriptReviewDismissed = signal(false);
  showTranscriptReview = computed(
    () => !!this.planner.lastTranscriptImport() && !this.transcriptReviewDismissed(),
  );

  private readonly messagesArea =
    viewChild<ElementRef<HTMLDivElement>>('messagesArea');
  private readonly fileInput =
    viewChild<ElementRef<HTMLInputElement>>('fileInput');

  /** "Enroll with the AI" -- the same real, deterministic next-semester
   * list already shown on Home/Weekly Schedule (not anything the LLM
   * decided; the chat's replies about what's next are phrasing this exact
   * same data, so offering to act on it here keeps the "LLM never
   * mutates, only phrases real facts" boundary intact -- the model never
   * calls claim_course_seat itself, this panel does, from data the
   * planning engine already computed). Only meaningful once signed in
   * (CourseEnrollmentService's own constraint) and once there's a real
   * plan to enroll from. */
  readonly isSignedIn = computed(() => !!this.supabase.session());
  private readonly sessionUserId = computed(() => this.supabase.session()?.user.id ?? null);
  /** Filters out placeholder entries with no real course code (e.g. an
   * unpicked "GEN ED" slot) -- there's nothing a real seat claim could
   * target for those, and showing an Apply button next to one just to
   * have it silently no-op on click is worse than not listing it. */
  readonly enrollableCourses = computed<Course[]>(
    () => (this.planner.coursePlan()?.nextSemester?.courses ?? []).filter((c) => !!c.id),
  );

  private readonly enrollmentStatuses = signal<Map<string, MyEnrollment | null>>(new Map());
  private readonly statusesLoadedFor = signal<string | null>(null);
  applyingCourseId = signal<string | null>(null);
  applyingAll = signal(false);

  /** Set while a full course's Apply is looking for (or offering) an open
   * alternative -- see EnrollmentDecision above. */
  readonly decision = signal<EnrollmentDecision | null>(null);
  /** Courses the student enrolled into an alternative for instead of the
   * original (keyed by the ORIGINAL course's id) -- kept separate from
   * enrollmentStatuses since the claim landed on a different course code. */
  private readonly swappedCourses = signal<Map<string, { code: string; name?: string }>>(new Map());

  readonly pendingCourses = computed(() => {
    const statuses = this.enrollmentStatuses();
    const swapped = this.swappedCourses();
    return this.enrollableCourses().filter((c) => c.id && !statuses.get(c.id) && !swapped.get(c.id));
  });

  constructor() {
    // Keeps the live seat store warm for every course this panel can
    // enroll into, so each row's Apply button reads "Full" the moment that
    // becomes true -- whether this student or any other took the last seat.
    effect(() => {
      const codes = this.enrollableCourses().map((c) => c.id).filter((id): id is string => !!id);
      if (codes.length) this.enrollment.getSeatPools(codes).catch(() => {});
    });

    // Home's example-prompt chips (and anything else calling
    // openChatWithPrompt) seed the input via pendingPrompt — consumed once,
    // then cleared so a later close/reopen of this panel doesn't restore it.
    effect(() => {
      const seed = this.planner.pendingPrompt();
      if (!seed) return;
      this.prompt.set(seed);
      this.planner.pendingPrompt.set(undefined);
    });

    // Keep the newest message in view whenever the list grows.
    effect(() => {
      this.planner.chatMessages();
      const el = this.messagesArea()?.nativeElement;
      if (!el) return;
      setTimeout(() => el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }));
    });

    // A fresh upload replaces lastTranscriptImport with a new object --
    // re-show the review panel for it even if the previous upload's review
    // was dismissed. Runs once at construction against whatever's already
    // there too (e.g. a saved plan reload mid-session), which is harmless:
    // dismissed starts false anyway.
    effect(() => {
      if (this.planner.lastTranscriptImport()) {
        this.transcriptReviewDismissed.set(false);
      }
    });

    // Loads each enrollable course's real status once the panel actually has
    // something to show (signed in + a real next-semester list exists) --
    // keyed by the session user id AND the course-id list, so a plan change
    // (new major, replanned semester) reloads instead of showing stale
    // statuses for courses that are no longer even the same set, and so a
    // different student signing in on the same plan never inherits the
    // previous student's "Enrolled" labels. Signing out
    // clears everything outright: the cache is per-account data.
    effect(() => {
      const userId = this.sessionUserId();
      if (!userId) {
        this._clearEnrollmentState();
        return;
      }
      const courses = this.enrollableCourses();
      if (!courses.length) return;
      const key = `${userId}|${courses.map((c) => c.id).join(',')}`;
      if (this.statusesLoadedFor() === key) return;
      this.statusesLoadedFor.set(key);
      this._loadEnrollmentStatuses(courses, key);
    });
  }

  private _clearEnrollmentState() {
    this.enrollmentStatuses.set(new Map());
    this.statusesLoadedFor.set(null);
    this.swappedCourses.set(new Map());
    this.decision.set(null);
  }

  private async _loadEnrollmentStatuses(courses: Course[], key: string): Promise<void> {
    const entries = await Promise.all(
      courses
        .filter((c) => c.id)
        .map(async (c) => [c.id, await this.enrollment.getMyEnrollment(c.id).catch(() => null)] as const),
    );
    // The user or course list changed while these were in flight -- this
    // batch belongs to whatever was showing before, not to what is now.
    if (this.statusesLoadedFor() !== key) return;
    this.enrollmentStatuses.set(new Map(entries));
  }

  /** Escape closes the panel, same as every other overlay in the app --
   * unless a real modal dialog is open on top of it (e.g. the Weekly
   * Schedule's course modal), which owns Escape for as long as it's up. */
  @HostListener('document:keydown.escape')
  onEscapeKey() {
    if (!this.planner.chatOpen()) return;
    if (document.querySelector('[role="dialog"]')) return;
    this.onClose();
  }

  /** Live: true once the shared pool for this course is known to be at
   * capacity. Unknown reads as not full -- never "Full" before a real row. */
  isFull(courseId: string): boolean {
    return this.enrollment.isFull(courseId);
  }

  statusFor(courseId: string): MyEnrollment | null {
    return this.enrollmentStatuses().get(courseId) ?? null;
  }

  swapFor(courseId: string): { code: string; name?: string } | null {
    return this.swappedCourses().get(courseId) ?? null;
  }

  decisionFor(courseId: string): EnrollmentDecision | null {
    const d = this.decision();
    return d && d.courseId === courseId ? d : null;
  }

  /** Best-effort title lookup for a sibling course code found by
   * findOpenAlternative() -- that RPC only ever returns a code, and this
   * panel's own course list (nextSemester) may not include the sibling, so
   * fall back to the flowchart's full course-card set which usually does. */
  private _titleFor(code: string): string | undefined {
    const plan = this.planner.coursePlan();
    const pool = [...(plan?.flowchart ?? []), ...(plan?.nextSemester?.courses ?? [])];
    return pool.find((c) => c.id === code)?.name;
  }

  /** Standard single-course pattern: an open seat applies right away; a
   * full course is refused -- the student is told (courseFullMessage) and
   * nothing is claimed -- and, when the requirement has sibling options,
   * _onCourseFull() goes looking for one with an open seat and offers it
   * inline (confirmAlternative()). Checked against the live pool first,
   * then enforced again by the server: apply() throws CourseFullError if
   * the last seat went in between, and that lands in the same place. */
  async applyToCourse(course: Course) {
    if (!course.id) return;
    const courseId = course.id;
    this.applyingCourseId.set(courseId);
    try {
      const { seatAvailable } = await this.enrollment.checkAvailability(courseId);
      if (!seatAvailable) {
        await this._onCourseFull(course);
        return;
      }
      const result = await this.enrollment.apply(courseId);
      this.enrollmentStatuses.update((m) => new Map(m).set(courseId, result));
      this.toast.show(`You’re in ${courseId} — a seat is held for you.`, 'success');
    } catch (e) {
      if (e instanceof CourseFullError) await this._onCourseFull(course);
      else this.toast.show(e instanceof Error ? e.message : `Could not check ${courseId} right now.`, 'error');
    } finally {
      this.applyingCourseId.set(null);
    }
  }

  /** The one place "this course is full" is handled: say so, and -- only
   * when the requirement has other options -- look for an open one. The
   * toast waits for that search so the student reads one message, not two
   * stacked ones. */
  private async _onCourseFull(course: Course): Promise<void> {
    const courseId = course.id;
    const options = course.options ?? [];
    if (!options.length) {
      this.toast.show(courseFullMessage(courseId), 'error');
      return;
    }
    this.decision.set({ courseId, stage: 'finding-alternative' });
    try {
      const altCode = await this.enrollment.findOpenAlternative(options);
      if (altCode) {
        this.decision.set({
          courseId, stage: 'alternative-found', alternativeCode: altCode, alternativeName: this._titleFor(altCode),
        });
        this.toast.show(courseFullMessage(courseId), 'error');
      } else {
        this.decision.set(null);
        this.toast.show(`${courseFullMessage(courseId)} Every alternative for this requirement is full too.`, 'error');
      }
    } catch {
      this.decision.set(null);
      this.toast.show(courseFullMessage(courseId), 'error');
    }
  }

  /** Confirms enrolling in the open alternative _onCourseFull() found,
   * instead of the original, full course. */
  async confirmAlternative(course: Course) {
    const current = this.decision();
    if (!course.id || !current || current.courseId !== course.id || !current.alternativeCode) return;
    const courseId = course.id;
    const { alternativeCode, alternativeName } = current;
    this.applyingCourseId.set(courseId);
    try {
      await this.enrollment.apply(alternativeCode);
      this.swappedCourses.update((m) => new Map(m).set(courseId, { code: alternativeCode, name: alternativeName }));
      this.toast.show(`You’re in ${alternativeCode} instead — a seat is held for you.`, 'success');
      this.decision.set(null);
    } catch (e) {
      if (e instanceof CourseFullError) {
        this.decision.set(null);
        this.toast.show(`${alternativeCode} just filled up too — its seats can’t be registered.`, 'error');
      } else {
        this.toast.show(e instanceof Error ? e.message : `Could not apply to ${alternativeCode} right now.`, 'error');
      }
    } finally {
      this.applyingCourseId.set(null);
    }
  }

  /** Backs out of an open decision prompt without applying to anything. */
  cancelDecision() {
    this.decision.set(null);
  }

  /** One click to act on the whole recommended semester at once -- applies
   * sequentially (not Promise.all) so a student watching the panel sees
   * each course resolve in turn rather than everything flipping at once,
   * and so one course's failure doesn't abort the rest. This is a bulk
   * action, so unlike applyToCourse() it never opens an inline prompt: a
   * full course is swapped for an open sibling option automatically when
   * there is one, and otherwise simply reported as full -- never claimed,
   * never waitlisted. */
  async applyToAll() {
    const courses = this.pendingCourses();
    if (!courses.length) return;
    this.applyingAll.set(true);
    let enrolledCount = 0;
    let failedCount = 0;
    const full: string[] = [];
    const swaps: string[] = [];
    try {
      for (const course of courses) {
        if (!course.id) continue;
        const courseId = course.id;
        try {
          const { seatAvailable } = await this.enrollment.checkAvailability(courseId);
          if (seatAvailable) {
            const result = await this.enrollment.apply(courseId);
            this.enrollmentStatuses.update((m) => new Map(m).set(courseId, result));
            enrolledCount++;
            continue;
          }
          const altCode = await this.enrollment.findOpenAlternative(course.options ?? []);
          if (!altCode) {
            full.push(courseId);
            continue;
          }
          await this.enrollment.apply(altCode);
          this.swappedCourses.update((m) => new Map(m).set(courseId, { code: altCode, name: this._titleFor(altCode) }));
          enrolledCount++;
          swaps.push(`${courseId} → ${altCode}`);
        } catch (e) {
          if (e instanceof CourseFullError) full.push(e.courseCode);
          else failedCount++;
        }
      }
      const parts: string[] = [];
      if (enrolledCount) parts.push(`${enrolledCount} enrolled`);
      if (swaps.length) parts.push(`swapped ${swaps.join(', ')}`);
      if (full.length) parts.push(`${full.join(', ')} full — can’t be registered`);
      if (failedCount) parts.push(`${failedCount} failed`);
      this.toast.show(parts.join('; ') || 'Nothing to apply to.', failedCount || full.length ? 'error' : 'success');
    } finally {
      this.applyingAll.set(false);
    }
  }

  onSubmit() {
    const p = this.prompt().trim();
    if (p === '' || this.planner.loading()) return;
    if (this.planner.state().undecided) {
      // No degree plan exists yet — pure exploration, not the scheduling
      // pipeline, so noProgramsForCampus (a plan-data concern) doesn't apply.
      this.prompt.set('');
      this.planner.onExplorePromptSubmitted(p);
      return;
    }
    if (this.planner.noProgramsForCampus()) return;
    this.prompt.set('');
    this.planner.onPromptSubmitted({ prompt: p });
  }

  onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.onSubmit();
    }
  }

  onClose() {
    this.planner.chatOpen.set(false);
  }

  /** The grey + button — opens the hidden file input rather than being a
   * file input itself, so it can look like a normal icon button. */
  onUploadClick() {
    this.fileInput()?.nativeElement.click();
  }

  async onFileSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = ''; // let the same file be re-selected later if needed
    if (!file) return;
    this.uploadingTranscript.set(true);
    try {
      await this.planner.onTranscriptUploaded(file);
    } finally {
      this.uploadingTranscript.set(false);
    }
  }

  dismissTranscriptReview() {
    this.transcriptReviewDismissed.set(true);
  }
}
