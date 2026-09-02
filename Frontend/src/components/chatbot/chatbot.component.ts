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

  readonly pendingCourses = computed(() => {
    const statuses = this.enrollmentStatuses();
    return this.enrollableCourses().filter((c) => c.id && !statuses.get(c.id));
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

  async applyToCourse(course: Course) {
    if (!course.id) return;
    this.applyingCourseId.set(course.id);
    try {
      const result = await this.enrollment.apply(course.id);
      this.enrollmentStatuses.update((m) => new Map(m).set(course.id, result));
      this.toast.show(
        result.status === 'enrolled'
          ? `You're in ${course.id} — a seat is held for you.`
          : `${course.id} is full — you're #${result.position} on the waitlist.`,
        'success',
      );
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : `Could not apply to ${course.id} right now.`, 'error');
    } finally {
      this.applyingCourseId.set(null);
    }
  }

  /** One click to act on the whole recommended semester at once -- applies
   * sequentially (not Promise.all) so a student watching the panel sees
   * each course resolve in turn rather than everything flipping at once,
   * and so one course's failure doesn't abort the rest. */
  async applyToAll() {
    const courses = this.pendingCourses();
    if (!courses.length) return;
    this.applyingAll.set(true);
    let enrolledCount = 0;
    let waitlistedCount = 0;
    let failedCount = 0;
    try {
      for (const course of courses) {
        if (!course.id) continue;
        try {
          const result = await this.enrollment.apply(course.id);
          this.enrollmentStatuses.update((m) => new Map(m).set(course.id, result));
          if (result.status === 'enrolled') enrolledCount++;
          else waitlistedCount++;
        } catch {
          failedCount++;
        }
      }
      const parts: string[] = [];
      if (enrolledCount) parts.push(`${enrolledCount} enrolled`);
      if (waitlistedCount) parts.push(`${waitlistedCount} waitlisted`);
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
