import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { FlowchartComponent } from '../../components/flowchart/flowchart.component';
import { PlannerStateService } from '../../services/planner-state.service';

@Component({
  selector: 'app-flowchart-page',
  standalone: true,
  templateUrl: './flowchart-page.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FlowchartComponent],
})
export class FlowchartPageComponent {
  readonly planner = inject(PlannerStateService);
}
