import { Injectable, effect, signal } from '@angular/core';

const STORAGE_KEY = 'theme';

function initialDarkMode(): boolean {
  // Defaults to light (white background) for a first-time visitor -- only
  // an explicit prior toggle switches this, regardless of OS preference.
  try {
    return localStorage.getItem(STORAGE_KEY) === 'dark';
  } catch {
    // localStorage can throw in a locked-down/private context -- fall
    // back to the light default rather than crash the whole app over a
    // theme toggle.
    return false;
  }
}

@Injectable({ providedIn: 'root' })
export class ThemeService {
  // Matches the inline script in index.html that applies the `dark` class
  // before Angular boots (avoids a flash of the light theme on load) --
  // this signal just takes over from there, so the two must agree on
  // both the storage key and the fallback-to-OS-preference behavior.
  readonly dark = signal(initialDarkMode());

  constructor() {
    effect(() => {
      const isDark = this.dark();
      document.documentElement.classList.toggle('dark', isDark);
      try {
        localStorage.setItem(STORAGE_KEY, isDark ? 'dark' : 'light');
      } catch {
        // Same as above -- a full/blocked localStorage shouldn't break
        // the toggle itself, just the "remember it next time" part.
      }
    });
  }

  toggle(): void {
    this.dark.update((v) => !v);
  }
}
