import { Routes } from '@angular/router';
import { advisorAuthGuard } from './guards/advisor-auth.guard';
import { HomePageComponent } from './pages/home-page/home-page.component';

export const routes: Routes = [
  // Home stays eager: it's the default landing route and every visitor
  // pays for it anyway, so there's nothing to gain by deferring it.
  { path: '', component: HomePageComponent, title: 'Course Planner' },
  {
    path: 'flowchart',
    // Lazy: FlowchartComponent statically imports mermaid, whose ~305KB/76KB
    // core otherwise rides in the initial bundle for every route. See
    // course-planner-scaling memory / bundle-size investigation.
    loadComponent: () =>
      import('./pages/flowchart-page/flowchart-page.component').then((m) => m.FlowchartPageComponent),
    title: 'Flowchart · Course Planner',
  },
  {
    path: 'progress',
    loadComponent: () => import('./pages/progress-page/progress-page.component').then((m) => m.ProgressPageComponent),
    title: 'Progress · Course Planner',
  },
  {
    path: 'recommendations',
    loadComponent: () =>
      import('./pages/recommendations-page/recommendations-page.component').then(
        (m) => m.RecommendationsPageComponent,
      ),
    title: 'Recommendations · Course Planner',
  },
  {
    path: 'your-plan',
    loadComponent: () => import('./pages/your-plan-page/your-plan-page.component').then((m) => m.YourPlanPageComponent),
    title: 'Your Plan · Course Planner',
  },
  {
    path: 'faq',
    loadComponent: () => import('./pages/faq-page/faq-page.component').then((m) => m.FaqPageComponent),
    title: 'FAQ · Course Planner',
  },
  {
    path: 'general-education',
    loadComponent: () => import('./pages/gen-ed-page/gen-ed-page.component').then((m) => m.GenEdPageComponent),
    title: 'General Education · Course Planner',
  },
  {
    path: 'transferred-courses',
    loadComponent: () =>
      import('./pages/transferred-courses-page/transferred-courses-page.component').then(
        (m) => m.TransferredCoursesPageComponent,
      ),
    title: 'Transferred Courses · Course Planner',
  },
  {
    path: 'demo-login',
    loadComponent: () => import('./pages/demo-login-page/demo-login-page.component').then((m) => m.DemoLoginPageComponent),
    title: 'Try a Demo Student · Course Planner',
  },
  // A real but entirely optional account, purely so a plan survives a
  // refresh -- no canActivate guard, unlike /advisor/*: every route must
  // keep working with no session at all.
  {
    path: 'login',
    loadComponent: () => import('./pages/student-login-page/student-login-page.component').then((m) => m.StudentLoginPageComponent),
    title: 'Sign In · Course Planner',
  },
  // Shared by both roles -- see SupabaseService.requestPasswordReset. No
  // guard: a fresh, unauthenticated browser landing on the emailed link is
  // exactly the expected case.
  {
    path: 'reset-password',
    loadComponent: () =>
      import('./pages/reset-password-page/reset-password-page.component').then((m) => m.ResetPasswordPageComponent),
    title: 'Reset Password · Course Planner',
  },
  {
    path: 'privacy',
    loadComponent: () => import('./pages/privacy-page/privacy-page.component').then((m) => m.PrivacyPageComponent),
    title: 'Privacy Policy · Course Planner',
  },
  {
    path: 'terms',
    loadComponent: () => import('./pages/terms-page/terms-page.component').then((m) => m.TermsPageComponent),
    title: 'Terms of Service · Course Planner',
  },
  // Real bookmarkable paths (unlike the query-param-based ?shared= link) --
  // an advisor returns to these repeatedly. Relies on the GH-Pages 404.html
  // SPA fallback (public/404.html) to survive a fresh/direct hit.
  {
    path: 'advisor/login',
    loadComponent: () => import('./pages/advisor-login-page/advisor-login-page.component').then((m) => m.AdvisorLoginPageComponent),
    title: 'Advisor Sign In · Course Planner',
  },
  {
    path: 'advisor/dashboard',
    loadComponent: () =>
      import('./pages/advisor-dashboard-page/advisor-dashboard-page.component').then(
        (m) => m.AdvisorDashboardPageComponent,
      ),
    canActivate: [advisorAuthGuard],
    title: 'Review Requests · Course Planner',
  },
  {
    path: 'advisor/review/:id',
    loadComponent: () =>
      import('./pages/advisor-review-page/advisor-review-page.component').then((m) => m.AdvisorReviewPageComponent),
    canActivate: [advisorAuthGuard],
    title: 'Review Request · Course Planner',
  },
  { path: '**', redirectTo: '' },
];
