
import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideZonelessChangeDetection } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { AppComponent } from './src/app.component';
import { routes } from './src/app.routes';

bootstrapApplication(AppComponent, {
  providers: [
    provideHttpClient(),
    provideZonelessChangeDetection(),
    // withComponentInputBinding lets a routed component declare its route
    // param as a plain `input()` (e.g. AdvisorReviewPageComponent's `id`)
    // instead of injecting ActivatedRoute and subscribing to paramMap --
    // matches the signal-input idiom already used everywhere else in this
    // app (SharedPlanPageComponent's `token`, FlowchartComponent's inputs).
    provideRouter(routes, withComponentInputBinding()),
  ]
}).catch(err => console.error(err));

// AI Studio always uses an `index.tsx` file for all project types.
