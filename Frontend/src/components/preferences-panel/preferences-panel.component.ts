import { ChangeDetectionStrategy, Component, ElementRef, HostListener, inject, signal, viewChild } from '@angular/core';
import { PlannerStateService } from '../../services/planner-state.service';

/** A small collapsible home for togglable preferences in the header chrome
 * -- starts with just "Allow Summer Courses" (moved out of the chat
 * panel's own header, which was getting crowded), but is deliberately its
 * own component so a future preference-like toggle has somewhere real to
 * go instead of accumulating as one-off header buttons. Injects
 * PlannerStateService directly rather than input/output plumbing, matching
 * ChatbotComponent's own reasoning for the same choice. */
@Component({
  selector: 'app-preferences-panel',
  standalone: true,
  templateUrl: './preferences-panel.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PreferencesPanelComponent {
  readonly planner = inject(PlannerStateService);
  private readonly host = inject(ElementRef<HTMLElement>);

  open = signal(false);

  private readonly toggleButton = viewChild<ElementRef<HTMLButtonElement>>('toggleButton');

  toggleOpen() {
    this.open.update((v) => !v);
  }

  @HostListener('document:keydown.escape')
  onEscape() {
    if (!this.open()) return;
    this.open.set(false);
    this.toggleButton()?.nativeElement.focus();
  }

  onToggleSummer() {
    const s = this.planner.state();
    this.planner.onPlanningChanged({
      startYear: s.startYear,
      gradYears: s.gradYears,
      allowSummer: !s.allowSummer,
      maxCreditsPerSemester: s.maxCreditsPerSemester,
    });
  }

  onMaxCreditsChange(value: string) {
    const s = this.planner.state();
    this.planner.onPlanningChanged({
      startYear: s.startYear,
      gradYears: s.gradYears,
      allowSummer: s.allowSummer,
      maxCreditsPerSemester: value ? Number(value) : undefined,
    });
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent) {
    if (this.open() && !this.host.nativeElement.contains(event.target as Node)) {
      this.open.set(false);
    }
  }
}
