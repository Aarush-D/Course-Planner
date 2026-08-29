import { Routes } from '@angular/router';
import { advisorAuthGuard } from './guards/advisor-auth.guard';
import { AdvisorDashboardPageComponent } from './pages/advisor-dashboard-page/advisor-dashboard-page.component';
import { AdvisorLoginPageComponent } from './pages/advisor-login-page/advisor-login-page.component';
import { AdvisorReviewPageComponent } from './pages/advisor-review-page/advisor-review-page.component';
import { HomePageComponent } from './pages/home-page/home-page.component';
import { FlowchartPageComponent } from './pages/flowchart-page/flowchart-page.component';
import { ProgressPageComponent } from './pages/progress-page/progress-page.component';
import { RecommendationsPageComponent } from './pages/recommendations-page/recommendations-page.component';
import { GenEdPageComponent } from './pages/gen-ed-page/gen-ed-page.component';
import { TransferredCoursesPageComponent } from './pages/transferred-courses-page/transferred-courses-page.component';
import { DemoLoginPageComponent } from './pages/demo-login-page/demo-login-page.component';
import { YourPlanPageComponent } from './pages/your-plan-page/your-plan-page.component';
import { PrivacyPageComponent } from './pages/privacy-page/privacy-page.component';
import { TermsPageComponent } from './pages/terms-page/terms-page.component';

export const routes: Routes = [
  { path: '', component: HomePageComponent, title: 'Course Planner' },
  { path: 'flowchart', component: FlowchartPageComponent, title: 'Flowchart · Course Planner' },
  { path: 'progress', component: ProgressPageComponent, title: 'Progress · Course Planner' },
  { path: 'recommendations', component: RecommendationsPageComponent, title: 'Recommendations · Course Planner' },
  { path: 'your-plan', component: YourPlanPageComponent, title: 'Your Plan · Course Planner' },
  { path: 'general-education', component: GenEdPageComponent, title: 'General Education · Course Planner' },
  { path: 'transferred-courses', component: TransferredCoursesPageComponent, title: 'Transferred Courses · Course Planner' },
  { path: 'demo-login', component: DemoLoginPageComponent, title: 'Try a Demo Student · Course Planner' },
  { path: 'privacy', component: PrivacyPageComponent, title: 'Privacy Policy · Course Planner' },
  { path: 'terms', component: TermsPageComponent, title: 'Terms of Service · Course Planner' },
  // Real bookmarkable paths (unlike the query-param-based ?shared= link) --
  // an advisor returns to these repeatedly. Relies on the GH-Pages 404.html
  // SPA fallback (public/404.html) to survive a fresh/direct hit.
  { path: 'advisor/login', component: AdvisorLoginPageComponent, title: 'Advisor Sign In · Course Planner' },
  {
    path: 'advisor/dashboard',
    component: AdvisorDashboardPageComponent,
    canActivate: [advisorAuthGuard],
    title: 'Review Requests · Course Planner',
  },
  {
    path: 'advisor/review/:id',
    component: AdvisorReviewPageComponent,
    canActivate: [advisorAuthGuard],
    title: 'Review Request · Course Planner',
  },
  { path: '**', redirectTo: '' },
];
