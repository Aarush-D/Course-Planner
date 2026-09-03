import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { Course } from '../../models/course-plan.model';
import { CourseEnrollmentService, MyEnrollment } from '../../services/course-enrollment.service';
import { PlannerStateService } from '../../services/planner-state.service';
import { SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';

/** Tracks the single in-progress "this course is full, now what?" prompt --
 * only one course's decision is ever open at a time (mirrors the existing
 * applyingCourseId single-flight pattern). 'choosing' is the initial
 * waitlist-vs-find-a-replacement fork; 'finding-alternative' is the async
 * gap while findOpenAlternative() runs; 'alternative-found' offers the
 * discovered sibling course for confirmation; 'no-alternative' means every
 * sibling was also full, so it falls back to just offering the waitlist. */
interface EnrollmentDecision {
  courseId: string;
  estimatedWaitlistPosition: number;
  stage: 'choosing' | 'finding-alternative' | 'alternative-found' | 'no-alternative';
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
  imports: [RouterLink],
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

  /** Set when a full course's Apply was clicked and the student now needs
   * to choose waitlist vs. a replacement -- see EnrollmentDecision above. */
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

    // Loads each enrollable course's real status once the panel actually has
    // something to show (signed in + a real next-semester list exists) --
    // keyed by the course-id list so a plan change (new major, replanned
    // semester) reloads instead of showing stale statuses for courses that
    // are no longer even the same set.
    effect(() => {
      if (!this.isSignedIn()) return;
      const courses = this.enrollableCourses();
      if (!courses.length) return;
      const key = courses.map((c) => c.id).join(',');
      if (this.statusesLoadedFor() === key) return;
      this.statusesLoadedFor.set(key);
      this._loadEnrollmentStatuses(courses);
    });
  }

  private async _loadEnrollmentStatuses(courses: Course[]): Promise<void> {
    const entries = await Promise.all(
      courses
        .filter((c) => c.id)
        .map(async (c) => [c.id, await this.enrollment.getMyEnrollment(c.id).catch(() => null)] as const),
    );
    this.enrollmentStatuses.set(new Map(entries));
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

  /** Standard single-course decision pattern: an open seat applies right
   * away, a full course stops short of apply() (which would silently
   * waitlist) and instead opens the waitlist-vs-replacement prompt for the
   * student to resolve via confirmWaitlist()/findReplacement() below. */
  async applyToCourse(course: Course) {
    if (!course.id) return;
    const courseId = course.id;
    this.applyingCourseId.set(courseId);
    try {
      const { seatAvailable, estimatedWaitlistPosition } = await this.enrollment.checkAvailability(courseId);
      if (seatAvailable) {
        const result = await this.enrollment.apply(courseId);
        this.enrollmentStatuses.update((m) => new Map(m).set(courseId, result));
        this.toast.show(`You’re in ${courseId} — a seat is held for you.`, 'success');
        return;
      }
      this.decision.set({ courseId, estimatedWaitlistPosition, stage: 'choosing' });
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : `Could not check ${courseId} right now.`, 'error');
    } finally {
      this.applyingCourseId.set(null);
    }
  }

  /** "Join the waitlist" -- from either the initial choice or after a
   * replacement search came up empty. */
  async confirmWaitlist(course: Course) {
    if (!course.id) return;
    const courseId = course.id;
    this.applyingCourseId.set(courseId);
    try {
      const result = await this.enrollment.apply(courseId);
      this.enrollmentStatuses.update((m) => new Map(m).set(courseId, result));
      this.toast.show(
        result.status === 'enrolled'
          ? `You’re in ${courseId} — a seat is held for you.`
          : `${courseId} is full — you’re #${result.position} on the waitlist.`,
        'success',
      );
      this.decision.set(null);
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : `Could not join the waitlist for ${courseId}.`, 'error');
    } finally {
      this.applyingCourseId.set(null);
    }
  }

  /** "Find a replacement" -- searches this course's own requirement-slot
   * siblings (Course.options) for one with an open seat right now. */
  async findReplacement(course: Course) {
    const current = this.decision();
    if (!course.id || !current || current.courseId !== course.id) return;
    this.decision.set({ ...current, stage: 'finding-alternative' });
    try {
      const altCode = await this.enrollment.findOpenAlternative(course.options ?? []);
      this.decision.set(
        altCode
          ? { ...current, stage: 'alternative-found', alternativeCode: altCode, alternativeName: this._titleFor(altCode) }
          : { ...current, stage: 'no-alternative' },
      );
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : `Could not look for a replacement for ${course.id}.`, 'error');
      this.decision.set({ ...current, stage: 'choosing' });
    }
  }

  /** Confirms enrolling in the alternative found by findReplacement()
   * instead of the original, full course. */
  async confirmAlternative(course: Course) {
    const current = this.decision();
    if (!course.id || !current || current.courseId !== course.id || !current.alternativeCode) return;
    const courseId = course.id;
    const { alternativeCode, alternativeName } = current;
    this.applyingCourseId.set(courseId);
    try {
      const result = await this.enrollment.apply(alternativeCode);
      this.swappedCourses.update((m) => new Map(m).set(courseId, { code: alternativeCode, name: alternativeName }));
      this.toast.show(
        result.status === 'enrolled'
          ? `You’re in ${alternativeCode} instead — a seat is held for you.`
          : `${alternativeCode} filled up too — you’re #${result.position} on its waitlist.`,
        'success',
      );
      this.decision.set(null);
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : `Could not apply to ${alternativeCode} right now.`, 'error');
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
   * action, so unlike applyToCourse() it never opens an interactive
   * prompt -- it runs the standard pattern's automated resolution per
   * course instead: open seat -> apply directly; full -> try a sibling
   * option automatically and apply there if one's open; otherwise fall
   * back to applying (and thus waitlisting) on the original, since "apply
   * to everything I still need" implies wanting SOME allocation either way. */
  async applyToAll() {
    const courses = this.pendingCourses();
    if (!courses.length) return;
    this.applyingAll.set(true);
    let enrolledCount = 0;
    let waitlistedCount = 0;
    let failedCount = 0;
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
          if (altCode) {
            const result = await this.enrollment.apply(altCode);
            this.swappedCourses.update((m) => new Map(m).set(courseId, { code: altCode, name: this._titleFor(altCode) }));
            if (result.status === 'enrolled') enrolledCount++;
            else waitlistedCount++;
            swaps.push(`${courseId} → ${altCode}`);
            continue;
          }
          const result = await this.enrollment.apply(courseId);
          this.enrollmentStatuses.update((m) => new Map(m).set(courseId, result));
          waitlistedCount++;
        } catch {
          failedCount++;
        }
      }
      const parts: string[] = [];
      if (enrolledCount) parts.push(`${enrolledCount} enrolled`);
      if (waitlistedCount) parts.push(`${waitlistedCount} waitlisted`);
      if (swaps.length) parts.push(`swapped ${swaps.join(', ')}`);
      if (failedCount) parts.push(`${failedCount} failed`);
      this.toast.show(parts.join(', ') || 'Nothing to apply to.', failedCount ? 'error' : 'success');
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
}
