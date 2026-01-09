import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { Course } from '../../models/course-plan.model';

@Component({
  selector: 'app-flowchart',
  standalone: true,
  templateUrl: './flowchart.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FlowchartComponent {
  courses = input<Course[] | undefined>();
  isLoading = input.required<boolean>();

  expandedCourseId = signal<string | null>(null);

  toggleCourse(id: string) {
    this.expandedCourseId.update(currentId => (currentId === id ? null : id));
  }
}
