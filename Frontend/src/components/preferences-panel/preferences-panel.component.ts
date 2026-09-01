import { ChangeDetectionStrategy, Component, ElementRef, HostListener, inject, signal } from '@angular/core';
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

  toggleOpen() {
    this.open.update((v) => !v);
  }

  onToggleSummer() {
    const s = this.planner.state();
    this.planner.onPlanningChanged({
      startYear: s.startYear,
      gradYears: s.gradYears,
      allowSummer: !s.allowSummer,
    });
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent) {
    if (this.open() && !this.host.nativeElement.contains(event.target as Node)) {
      this.open.set(false);
    }
  }
}
