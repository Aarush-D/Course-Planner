import { Injectable, effect, signal } from '@angular/core';

const STORAGE_KEY = 'theme';

function initialDarkMode(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'dark') return true;
    if (stored === 'light') return false;
  } catch {
    // localStorage can throw in a locked-down/private context -- fall
    // through to the OS preference rather than crash the whole app over a
    // theme toggle.
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
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
