import { Routes } from '@angular/router';
import { HomePageComponent } from './pages/home-page/home-page.component';
import { FlowchartPageComponent } from './pages/flowchart-page/flowchart-page.component';
import { ProgressPageComponent } from './pages/progress-page/progress-page.component';
import { RecommendationsPageComponent } from './pages/recommendations-page/recommendations-page.component';
import { GenEdPageComponent } from './pages/gen-ed-page/gen-ed-page.component';
import { TransferredCoursesPageComponent } from './pages/transferred-courses-page/transferred-courses-page.component';

export const routes: Routes = [
  { path: '', component: HomePageComponent, title: 'Course Planner' },
  { path: 'flowchart', component: FlowchartPageComponent, title: 'Flowchart · Course Planner' },
  { path: 'progress', component: ProgressPageComponent, title: 'Progress · Course Planner' },
  { path: 'recommendations', component: RecommendationsPageComponent, title: 'Recommendations · Course Planner' },
  { path: 'general-education', component: GenEdPageComponent, title: 'General Education · Course Planner' },
  { path: 'transferred-courses', component: TransferredCoursesPageComponent, title: 'Transferred Courses · Course Planner' },
  { path: '**', redirectTo: '' },
];
