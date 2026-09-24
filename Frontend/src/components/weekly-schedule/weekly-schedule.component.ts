import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { Location } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { animateModalIn, animateModalOut } from '../../animations/modal-fade';
import { ModalFocusTrapDirective } from '../../directives/modal-focus-trap.directive';
import { Course, CourseGraphEntry } from '../../models/course-plan.model';
import { BackendService } from '../../services/backend.service';
import {
  CourseEnrollmentService, CourseFullError, MyEnrollment, SeatPoolInfo, UNCLAIMED_SEAT_POOL,
  courseFullMessage, seatStatusFrom,
} from '../../services/course-enrollment.service';
import { CourseGroupSummary, CourseGroupService } from '../../services/course-group.service';
import { CourseRatingService } from '../../services/course-rating.service';
import { StudentProfileService } from '../../services/student-profile.service';
import { CourseRatingSummaryRow, SupabaseService } from '../../services/supabase.service';
import { ToastService } from '../../services/toast.service';
import { normalizeCourseCode } from '../../utils/course-code.util';
import { linkQueryParam } from '../../utils/url-state';
import {
  DAY_LABELS, Modality, ScheduleSlot, WEEKDAY_CODES,
  dummyBuildingFor, dummyModalityFor, dummyProfessorFor, dummySlotFor, formatClockTime,
} from '../../utils/dummy-schedule.util';
import { CourseReviewsModalComponent } from '../course-reviews-modal/course-reviews-modal.component';
import { StarRatingComponent } from '../ui/star-rating/star-rating.component';

const GRID_START_MINUTES = 8 * 60; // 8:00 AM
const GRID_END_MINUTES = 17 * 60; // 5:00 PM
const PX_PER_MINUTE = 1;

/** Real per-course seat availability for a grid block's dot + short label
 * -- derived from the exact same course_seat_pools-backed data (via
 * CourseEnrollmentService.getSeatPools + seatStatusFrom) as the modal's
 * own "Registration status"/"Real seat, held for you" sections, so a
 * block can never show "Full" for a course the modal then reports as
 * open (or vice versa) -- see registrationStatusFor() below and
 * seatStatusFrom's own doc comment. Binary -- open or full -- because
 * that is the whole story since migration 0023: a full course can't be
 * registered, there is no waitlist behind it, and course_seat_pools'
 * public columns (capacity, seats_taken) say exactly that and nothing
 * identifying. */
interface BlockSeatStatus {
  status: 'open' | 'full';
  seatsLeft: number;
  capacity: number;
}

interface PlacedBlock {
  course: Course;
  slot: ScheduleSlot;
  seats: BlockSeatStatus;
  top: number;
  height: number;
}

/** A Mon–Fri weekly grid of the student's "Recommended Next Semester"
 * courses, at MADE-UP times (see dummy-schedule.util.ts for why real ones
 * don't exist yet) -- lets a student see what a real term could look like
 * shaped out, click a block for the course's full info, and mark it as
 * planned. Intentionally scoped to recommended (not completed) courses --
 * this is about what to take next, not a record of what's already done. */
@Component({
  selector: 'app-weekly-schedule',
  standalone: true,
  templateUrl: './weekly-schedule.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ModalFocusTrapDirective, CourseReviewsModalComponent, StarRatingComponent],
})
export class WeeklyScheduleComponent {
  private readonly injector = inject(Injector);
  private readonly supabase = inject(SupabaseService);
  private readonly enrollment = inject(CourseEnrollmentService);
  private readonly groups = inject(CourseGroupService);
  private readonly profiles = inject(StudentProfileService);
  private readonly toast = inject(ToastService);
  private readonly backend = inject(BackendService);
  private readonly ratings = inject(CourseRatingService);
  private readonly _route = inject(ActivatedRoute);
  private readonly _location = inject(Location);

  courses = input<Course[]>([]);
  scheduledCourseIds = input<string[]>([]);
  /** For the modal's real "This course also unlocks" section -- same
   * major/catalog-year scoping BackendService.courseGraph() needs
   * elsewhere (see course-explorer.component.ts). Optional/nullable since
   * an undecided student never reaches this component at all (Home page
   * only renders it once a real plan exists), but there's no reason to
   * force a value the caller might not have handy. */
  major = input<string | null>();
  catalogYear = input<number | undefined>();

  toggleScheduled = output<string>();

  readonly isSignedIn = computed(() => !!this.supabase.session());

  /** Real, shared seat/group/networking state for whichever course the
   * modal currently has open -- loaded fresh each time openCourse() runs
   * (see below), separate from the sample/dummy data above them in the
   * modal, which stays exactly as illustrative-only as before. Plain
   * component signals rather than per-course caching: this modal only
   * ever shows one course at a time, so there's nothing to keep in sync
   * across courses. */
  myEnrollment = signal<MyEnrollment | null>(null);
  /** The open course's pool, read straight out of CourseEnrollmentService's
   * live store -- so when another student claims or drops a seat in this
   * course while the modal is open, the count and the Open/Full status
   * change in front of the student, no refresh. Null until the store has
   * a row for this course (the section just doesn't render yet). Keyed by
   * the selected course's code, which is also what makes a late response
   * for a PREVIOUSLY opened course harmless: it lands under that course's
   * key, never this one's. */
  readonly seatPool = computed<SeatPoolInfo | null>(() => {
    const code = this.selectedCourse()?.id;
    return code ? this.enrollment.livePool(code) : null;
  });
  groupStatus = signal<CourseGroupSummary | null>(null);
  classmateLinkedins = signal<string[]>([]);
  applyBusy = signal(false);
  groupBusy = signal(false);
  joinCodeInput = signal('');
  justCreatedInviteCode = signal<string | null>(null);

  /** Real course_rating_summary for whichever course the modal has open --
   * unlike everything above, ratings need no signed-in session at all (see
   * CourseRatingService), so this loads for every visitor, not gated by
   * isSignedIn(). */
  courseRatingSummary = signal<CourseRatingSummaryRow | null>(null);
  reviewsModalOpen = signal(false);

  /** Real course_seat_pools rows for every course on the grid, keyed by
   * course code: CourseEnrollmentService's live store, which one batched
   * getSeatPools() call (see the effect below) fills and Supabase
   * Realtime keeps current from then on. This is what blocksForDay()
   * reads for each block's dot/short label, so a block's "Full"/"N left"
   * and the modal's "Registration status" for that same course are
   * guaranteed to agree -- both come from this exact map, through the
   * same seatStatusFrom() comparison -- and both move the instant any
   * student anywhere claims or drops a seat. A course not yet in the map
   * (still loading) reads as UNCLAIMED_SEAT_POOL, never "Full". */
  private readonly seatPools = this.enrollment.pools;

  /** The current major's full prereq/unlock graph, loaded once (and
   * reloaded on a major/catalog-year change) exactly like
   * course-explorer.component.ts's own courseGraph fetch -- gives the
   * modal's "This course also unlocks" section real course codes/names
   * instead of the flowchart card's plain unlocks *count*. */
  private readonly courseGraph = signal<CourseGraphEntry[]>([]);
  private readonly courseGraphByCode = computed(
    () => new Map(this.courseGraph().map((c) => [c.code, c])),
  );

  readonly days = WEEKDAY_CODES;
  readonly dayLabels = DAY_LABELS;
  readonly gridHeight = (GRID_END_MINUTES - GRID_START_MINUTES) * PX_PER_MINUTE;
  readonly hourMarks = Array.from(
    { length: GRID_END_MINUTES / 60 - GRID_START_MINUTES / 60 + 1 },
    (_, i) => GRID_START_MINUTES / 60 + i,
  );

  /** What the modal renders. Downstream of selectedCourseCode below --
   * never written directly except by the effect that resolves one into
   * the other. */
  selectedCourse = signal<Course | null>(null);

  /** Which course the URL says is open. A bare code rather than the
   * resolved Course, because that's what has to survive a reload: on a
   * pasted link this is read before courses() has arrived, so there is
   * nothing yet to resolve it against. */
  readonly selectedCourseCode = signal<string | null>(null);

  /** True while a history entry WE pushed is the current one. Decides
   * whether closeCourse pops that entry or just drops the param. */
  private _pushedHistoryEntry = false;

  private readonly modalBackdrop = viewChild<ElementRef<HTMLElement>>('modalBackdrop');
  private readonly modalPanel = viewChild<ElementRef<HTMLElement>>('modalPanel');
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  /** Generation tokens for every fire-and-forget fetch this component
   * starts. Each is bumped when a NEW request supersedes the old one, and
   * every `await` in the corresponding loader re-checks it before writing
   * -- so opening course A, closing it, and quickly opening course B can
   * never land A's slower seat-pool/enrollment/group/rating response in
   * B's modal (the reported bug), and a stale course-graph/seat-pools
   * response from a previous major or course list can't overwrite the
   * newer one. Read only via the `_isCurrent*` guards below. */
  private _loadToken = 0;
  private _graphToken = 0;

  /** The grid block that opened the current modal, for restoring focus on
   * close. The focus-trap directive already returns focus to whatever was
   * active at mount time, but the modal's mount is decoupled from the click
   * by a URL round-trip (openCourse -> ?course= -> effect -> _showCourse),
   * so this keeps its own reference to the actual opener as a backstop --
   * and looks it back up by course id if that node was re-rendered. */
  private _opener: HTMLElement | null = null;
  private _openerCourseId: string | null = null;

  /** Whether the modal's "What this course covers" <details> is open --
   * mirrored from the element's own toggle event purely so the <summary>
   * can carry a real aria-expanded. Reset per open. */
  descriptionOpen = signal(false);

  /** courseId -> its dummy slot, computed once per course list so every
   * block on every day column reads from the same stable value per render. */
  private readonly slotsByCourse = computed(() => {
    const map = new Map<string, ScheduleSlot>();
    for (const c of this.courses()) {
      if (c.id) map.set(c.id, dummySlotFor(c.id));
    }
    return map;
  });

  constructor() {
    // Loads (and reloads on a major/catalog-year change) the whole major's
    // course graph once, same pattern as course-explorer.component.ts --
    // every open of the modal then just looks its course up client-side in
    // courseGraphByCode rather than round-tripping per open.
    effect(() => {
      const major = this.major();
      const year = this.catalogYear();
      const token = ++this._graphToken;
      if (!major) {
        this.courseGraph.set([]);
        return;
      }
      this.backend.courseGraph(major, year).then(
        (list) => {
          if (token !== this._graphToken) return; // a newer major/year request superseded this one
          this.courseGraph.set(list);
        },
        () => {}, // best-effort -- "also unlocks" just stays empty
      );
    });

    // Asks the live store for every course on the grid, in one batched
    // call, whenever the recommended course list changes. The response
    // itself is not kept here: getSeatPools() writes into
    // CourseEnrollmentService.pools, which blocksForDay() reads, and which
    // Realtime then keeps current. Because that map is keyed by course
    // code, a slow response for a course list that has since been replaced
    // can't misfile anything -- it lands under its own courses' codes, and
    // the grid only ever reads the codes it is currently showing.
    effect(() => {
      const codes = [...new Set(this.courses().map((c) => c.id).filter((id): id is string => !!id))];
      if (!codes.length) return;
      this.enrollment.getSeatPools(codes).catch(() => {
        // best-effort -- blocks just keep reading UNCLAIMED_SEAT_POOL until a retry succeeds
      });
    });

    // 'push', unlike every other param in this app: this modal covers the
    // screen, and both a phone's back gesture and the desktop back button
    // are expected to close a thing like that rather than leave the page.
    linkQueryParam({
      key: 'course',
      signal: this.selectedCourseCode,
      toParam: (code) => code,
      fromParam: (param) => param,
      history: 'push',
    });

    // Resolves the URL's course code into the actual Course to render.
    // Depends on courses() as well as the code, which is what makes a
    // pasted link work: on first load the code arrives long before the
    // plan does, this finds nothing, and it simply runs again -- with no
    // retry logic of its own -- the moment courses() populates.
    effect(() => {
      const code = this.selectedCourseCode();
      const courses = this.courses();
      const current = untracked(() => this.selectedCourse());
      if (!code) {
        // Covers the back button and the forward-into-nothing case. No
        // exit animation on purpose: a browser-driven navigation should
        // feel immediate, not wait on a fade.
        if (current) {
          // Anything still in flight for the course that just closed must
          // not land in whatever modal opens next.
          this._loadToken++;
          this.selectedCourse.set(null);
          this._restoreOpenerFocus();
        }
        return;
      }
      if (current?.id === code) return;
      const course = courses.find((c) => c.id === code);
      if (course) this._showCourse(course);
    });
  }

  blocksForDay(day: string): PlacedBlock[] {
    const blocks: PlacedBlock[] = [];
    const pools = this.seatPools();
    for (const course of this.courses()) {
      if (!course.id) continue;
      const slot = this.slotsByCourse().get(course.id);
      if (!slot || !slot.days.includes(day)) continue;
      const top = (slot.startMinutes - GRID_START_MINUTES) * PX_PER_MINUTE;
      const height = (slot.endMinutes - slot.startMinutes) * PX_PER_MINUTE;
      blocks.push({ course, slot, seats: this._blockSeatsFor(course.id, pools), top, height });
    }
    // Sort by start time -- the block list otherwise follows whatever
    // order `courses()` happened to arrive in, which has no relation to
    // the slot's start time (that's hash-derived). Visually the blocks
    // are positioned by `top`, so an unsorted DOM order left keyboard/
    // screen-reader tab order out of sync with the visual top-to-bottom
    // order within a day column.
    return blocks.sort((a, b) => a.slot.startMinutes - b.slot.startMinutes);
  }

  /** The real, single-source-of-truth seat status for one block -- same
   * seatStatusFrom() math registrationStatusFor() below uses for the
   * modal, applied here to the batched seatPools() map instead of the
   * modal's single seatPool() signal. A course not yet in the map (still
   * loading) reads as UNCLAIMED_SEAT_POOL, i.e. open with a full capacity
   * of seats left -- never "Full" before a real row has even been read. */
  private _blockSeatsFor(courseId: string, pools: Map<string, SeatPoolInfo>): BlockSeatStatus {
    const pool = pools.get(courseId) ?? UNCLAIMED_SEAT_POOL;
    const { seatAvailable, seatsLeft } = seatStatusFrom(pool);
    return { status: seatAvailable ? 'open' : 'full', seatsLeft, capacity: pool.capacity };
  }

  formatTime(minutes: number): string {
    return formatClockTime(minutes);
  }

  /** Short label for the block itself. */
  seatsShortLabel(seats: BlockSeatStatus): string {
    return seats.status === 'open' ? `${seats.seatsLeft} left` : 'Full';
  }

  /** Sample meeting slot for the modal's course-info box -- reuses the
   * same per-course slot the grid blocks already computed (slotsByCourse)
   * instead of recomputing, falling back to a direct dummySlotFor() call
   * only for the edge case of a course that isn't in courses() at all
   * (shouldn't happen in practice: the modal only ever opens from a block
   * built out of that same list). */
  sampleSlotFor(course: Course): ScheduleSlot {
    const fromMap = course.id ? this.slotsByCourse().get(course.id) : undefined;
    return fromMap ?? dummySlotFor(course.id || course.name);
  }

  sampleDaysLabel(course: Course): string {
    return this.sampleSlotFor(course).days.map((d) => this.dayLabels[d]).join('/');
  }

  professorFor(course: Course): string {
    return dummyProfessorFor(course.id || course.name);
  }

  buildingFor(course: Course): string {
    return dummyBuildingFor(course.id || course.name);
  }

  modalityFor(course: Course): Modality {
    return dummyModalityFor(course.id || course.name);
  }

  /** Real registration status, computed from the SAME seatPool() signal
   * the "Real seat, held for you" box below reads -- deliberately not a
   * second network round-trip through CourseEnrollmentService.checkAvailability()
   * (which would just re-fetch this exact row): reusing the one already-
   * loaded value is what actually guarantees this line and that box can
   * never show contradicting numbers. The seatAvailable/seatsLeft math
   * itself is seatStatusFrom() -- the exact same shared function
   * _blockSeatsFor() above uses for the grid block's own dot/label, so
   * this modal and that block can never disagree about the same course
   * either (see BlockSeatStatus's doc comment for why that used to be
   * possible: the block used to read an entirely separate, client-side-
   * only dummySeatAvailabilityFor() hash instead of this real pool). */
  registrationStatusFor(pool: SeatPoolInfo): { seatAvailable: boolean; label: string } {
    const { seatAvailable, seatsLeft } = seatStatusFrom(pool);
    return {
      seatAvailable,
      label: seatAvailable
        ? `Open — ${seatsLeft} of ${pool.capacity} seats left`
        : `Full — ${pool.seatsTaken} of ${pool.capacity} taken`,
    };
  }

  /** Real unlocked-course codes/names for the modal's "This course also
   * unlocks" section, from the major's course graph (see courseGraph
   * above) -- Course.unlocks on the model itself is only ever a count. */
  unlocksFor(course: Course): { code: string; name: string | null }[] {
    if (!course.id) return [];
    const byCode = this.courseGraphByCode();
    const entry = byCode.get(course.id);
    if (!entry) return [];
    return entry.unlocks.map((code) => ({ code, name: byCode.get(code)?.name ?? null }));
  }

  openReviewsModal() {
    this.reviewsModalOpen.set(true);
  }

  closeReviewsModal() {
    this.reviewsModalOpen.set(false);
  }

  isScheduled(course: Course): boolean {
    return !!course.id && this.scheduledCourseIds().includes(course.id.toUpperCase());
  }

  /** Opening is expressed as a URL change, not a direct signal write: the
   * ?course= param is what makes a course modal linkable, and routing the
   * open through it keeps one code path for all three ways this modal can
   * appear (a click here, a pasted link, a forward button). The effect in
   * the constructor is what actually mounts it. */
  openCourse(course: Course, event?: Event) {
    if (!course.id) return; // only id-bearing courses are rendered as blocks
    // Remember the block that opened us so closing can hand focus back to
    // it (see _restoreOpenerFocus) -- falls back to whatever is focused
    // right now (the same block, for a keyboard activation).
    const target = event?.currentTarget;
    this._opener = target instanceof HTMLElement ? target : (document.activeElement as HTMLElement | null);
    this._openerCourseId = course.id;
    // Whether WE are the ones adding the history entry decides how
    // closeCourse has to undo it -- see the comment there.
    this._pushedHistoryEntry = !this._route.snapshot.queryParamMap.get('course');
    this.selectedCourseCode.set(course.id);
  }

  /** Returns focus to the grid block that opened the modal once it has
   * closed. Deferred one tick for the same reason ModalFocusTrapDirective
   * defers its own restore: the browser moves focus to <body> when the
   * focused modal node leaves the document, which happens AFTER this runs.
   * If the original node was re-rendered in the meantime (a plan refresh
   * rebuilding the grid), the block is looked up again by course id. */
  private _restoreOpenerFocus() {
    const opener = this._opener;
    const courseId = this._openerCourseId;
    this._opener = null;
    this._openerCourseId = null;
    if (!opener && !courseId) return;
    setTimeout(() => {
      const target: HTMLElement | null =
        opener?.isConnected
          ? opener
          : courseId
            ? (this.host.nativeElement as HTMLElement).querySelector(`[data-course-block="${CSS.escape(courseId)}"]`)
            : null;
      target?.focus();
    }, 0);
  }

  async closeCourse() {
    await this._animateOut();
    if (this._pushedHistoryEntry) {
      // We pushed an entry to open this, so the honest undo is to pop it.
      // Clearing the signal instead would push a SECOND entry, and Back
      // would then walk the student back INTO the modal they just closed.
      this._pushedHistoryEntry = false;
      this._location.back();
    } else {
      // Arrived here by pasted link or reload -- there is no entry of ours
      // to pop, so drop the param directly. Back still leaves the page,
      // which is right: the modal was the whole reason they were here.
      this.selectedCourseCode.set(null);
    }
  }

  /** Everything openCourse used to do inline. Driven only by the effect
   * above it, so a deep-linked open and a clicked one are byte-identical. */
  private _showCourse(course: Course) {
    // Supersede every loader still running for the previously open course
    // BEFORE resetting the fields below, so none of its late responses can
    // overwrite the fresh nulls with the wrong course's data.
    const token = ++this._loadToken;
    this.selectedCourse.set(course);
    this.myEnrollment.set(null);
    this.groupStatus.set(null);
    this.classmateLinkedins.set([]);
    this.joinCodeInput.set('');
    this.justCreatedInviteCode.set(null);
    this.courseRatingSummary.set(null);
    this.reviewsModalOpen.set(false);
    this.descriptionOpen.set(false);
    afterNextRender(() => this._animateIn(), { injector: this.injector });
    if (course.id) {
      this._loadRealCourseState(course.id, token);
      this._loadRatingSummary(course.id, token);
    }
  }

  onDescriptionToggle(event: Event) {
    this.descriptionOpen.set((event.target as HTMLDetailsElement).open);
  }

  readonly closeCourseFn = () => this.closeCourse();

  onToggleScheduled(course: Course) {
    if (course.id) this.toggleScheduled.emit(course.id);
  }

  /** True when the live pool for the open course says every seat is taken.
   * Drives the Apply button's "Full" label; the click itself still goes
   * through applyForSeat() so the student gets told, not silently ignored. */
  readonly openCourseIsFull = computed(() => {
    const pool = this.seatPool();
    return !!pool && !seatStatusFrom(pool).seatAvailable;
  });

  /** A full course cannot be registered -- the student is told so and
   * nothing is claimed. Checked against the live pool first (no doomed
   * round trip when the screen already shows Full), and then again by the
   * server: claim_course_seat refuses a full course atomically, so two
   * students racing for the last seat can't both get it, and the loser
   * gets the same message as someone who clicked a minute late.
   * The RPC itself is the only thing gating the busy state -- the pool
   * refresh that follows a claim is CourseEnrollmentService's own, in the
   * background, and Realtime delivers it to everyone else. */
  async applyForSeat(courseCode: string) {
    if (this.openCourseIsFull()) {
      this.toast.show(courseFullMessage(courseCode), 'error');
      return;
    }
    this.applyBusy.set(true);
    try {
      const result = await this.enrollment.apply(courseCode);
      this.myEnrollment.set(result);
      this.toast.show("You’re in — a seat is held for you.", 'success');
    } catch (e) {
      if (e instanceof CourseFullError) {
        this.toast.show(e.message, 'error');
      } else {
        this.toast.show(
          e instanceof Error ? e.message : 'Could not apply right now — check your connection and try again.',
          'error',
        );
      }
    } finally {
      this.applyBusy.set(false);
    }
  }

  async dropSeat(courseCode: string) {
    // Confirmed rather than immediate: the freed seat opens up to every
    // other student the instant this commits (release_freed_course_seat,
    // migration 0011, plus the live count), so there is nothing to undo
    // afterward -- if someone else takes it, re-applying is refused. It's
    // the one irreversible action in this modal, and it sat a single
    // stray click away.
    const proceed = window.confirm(
      `Give up your seat in ${courseCode}? It opens up to other students immediately, and you can’t take it back.`,
    );
    if (!proceed) return;
    this.applyBusy.set(true);
    try {
      await this.enrollment.drop(courseCode);
      this.myEnrollment.set(null);
      this.toast.show('Seat dropped.', 'success');
    } catch (e) {
      this.toast.show(
        e instanceof Error ? e.message : 'Could not drop right now — check your connection and try again.',
        'error',
      );
    } finally {
      this.applyBusy.set(false);
    }
  }

  async createGroup(courseCode: string) {
    this.groupBusy.set(true);
    try {
      const { groupId, inviteCode } = await this.groups.createGroup(courseCode);
      this.justCreatedInviteCode.set(inviteCode);
      this.groupStatus.set(await this.groups.getGroupStatus(groupId, inviteCode));
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : 'Could not create a group right now.', 'error');
    } finally {
      this.groupBusy.set(false);
    }
  }

  async joinGroup(courseCode: string) {
    const code = this.joinCodeInput().trim();
    if (!code) return;
    this.groupBusy.set(true);
    try {
      const { groupId, inviteCode } = await this.groups.joinGroup(code);
      this.joinCodeInput.set('');
      this.groupStatus.set(await this.groups.getGroupStatus(groupId, inviteCode));
      this.toast.show('Joined the group.', 'success');
    } catch {
      this.toast.show("That invite code didn’t work.", 'error');
    } finally {
      this.groupBusy.set(false);
    }
  }

  async leaveGroup(courseCode: string) {
    const group = this.groupStatus();
    if (!group) return;
    this.groupBusy.set(true);
    try {
      await this.groups.leaveGroup(group.groupId);
      this.groupStatus.set(null);
      this.justCreatedInviteCode.set(null);
    } catch (e) {
      this.toast.show(e instanceof Error ? e.message : 'Could not leave the group right now.', 'error');
    } finally {
      this.groupBusy.set(false);
    }
  }

  /** Best-effort, fire-and-forget from openCourse() -- a signed-out
   * visitor (the common case) or a network hiccup should never block the
   * modal from opening or degrade anything else in it; every piece here
   * fails silently into its own empty/null state instead of surfacing an
   * error for what is, for most visitors, an entirely optional add-on. */
  private async _loadRealCourseState(courseCode: string, token: number): Promise<void> {
    // `token` is the _loadToken value at the moment this course was opened;
    // after every await, a mismatch means a different course (or no course)
    // has been opened since, and this response belongs to the old one.
    const stale = () => token !== this._loadToken;
    // The pool needs no stale guard: it goes into the live store under
    // this course's own code, and seatPool() reads whichever course is
    // open -- a late answer for a closed course is simply never displayed.
    this.enrollment.getSeatPool(courseCode).catch(() => {
      // leave the store as-is -- the section just won't render until a read succeeds
    });
    if (stale() || !this.isSignedIn()) return;
    try {
      const mine = await this.enrollment.getMyEnrollment(courseCode);
      if (stale()) return;
      this.myEnrollment.set(mine);
    } catch {
      // leave myEnrollment null
    }
    if (stale()) return;
    try {
      const group = await this.groups.findMyGroup(courseCode);
      if (stale()) return;
      this.groupStatus.set(group);
    } catch {
      // leave groupStatus null
    }
    if (stale()) return;
    if (this.myEnrollment()?.status === 'enrolled') {
      try {
        const linkedins = await this.profiles.getClassmateLinkedins(courseCode);
        if (stale()) return;
        this.classmateLinkedins.set(linkedins);
      } catch {
        // leave classmateLinkedins empty
      }
    }
  }

  /** Real, anonymous course_rating_summary for the open course -- kept
   * separate from _loadRealCourseState above since ratings need no
   * account at all (see CourseRatingService), so unlike that whole flow
   * this always fires, signed in or not. Same batched getSummaries() call
   * flowchart.component.ts uses for its recommended-course cards, just
   * called with a single code here since the modal only ever shows one
   * course at a time. */
  private _loadRatingSummary(courseCode: string, token: number): void {
    this.ratings.getSummaries([courseCode]).then(
      (map) => {
        if (token !== this._loadToken) return; // a different course has been opened since
        this.courseRatingSummary.set(map.get(normalizeCourseCode(courseCode)) ?? null);
      },
      () => {}, // reviews are a nice-to-have here too -- fail silently into "no summary"
    );
  }

  private _animateIn() {
    const b = this.modalBackdrop();
    const p = this.modalPanel();
    if (b && p) animateModalIn(b.nativeElement, p.nativeElement);
  }

  private async _animateOut(): Promise<void> {
    const b = this.modalBackdrop();
    const p = this.modalPanel();
    if (b && p) await animateModalOut(b.nativeElement, p.nativeElement);
  }
}
