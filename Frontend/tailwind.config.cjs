/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{html,ts}'],
  // 'class' strategy (not the default 'media') so ThemeService's own
  // toggle controls dark mode directly, instead of only following the OS
  // preference -- mirrors the config the Play CDN script used to set
  // inline in index.html before this replaced it.
  darkMode: 'class',
  theme: {
    extend: {
      // Overrides Tailwind's default `indigo` (not a new color name) so
      // every existing bg-indigo-600/text-indigo-600/ring-indigo-500/
      // dark:bg-indigo-950/etc. class across the app (the app's one
      // consistent accent color, ~500 occurrences) renders in real Penn
      // State brand blue instead, with zero risk of missing a class
      // somewhere. 300 and 600 are Penn State's own real, official brand
      // hex values (Pugh Blue and Beaver Blue -- brand.psu.edu); 900 is
      // Nittany Navy. The rest of the ramp is interpolated between those
      // three real anchors. Every shade actually used in this app for
      // text/UI against a real background here was verified against the
      // WCAG ratios this app's own prior accessibility pass established
      // (4.5:1 text, 3:1 non-text) -- see the git history for the
      // computed numbers: 600 (primary buttons/links, white text) is
      // 10.10:1; 700 (hover) is 12.54:1; 500 (focus rings, both themes)
      // ranges 3.24-4.52:1; 400/300 (dark-mode text/links) are
      // 5.07-10.38:1 against slate-800/900/950.
      colors: {
        indigo: {
          50: '#eef3fa',
          100: '#dde7f5',
          200: '#b9d0eb',
          300: '#96BEE6', // Pugh Blue -- Penn State's real secondary brand color
          400: '#6a9bd6',
          500: '#4278bc',
          600: '#1E407C', // Beaver Blue -- Penn State's real primary brand blue
          700: '#17335f',
          800: '#0f2549',
          900: '#001E44', // Nittany Navy -- Penn State's real primary brand color
          950: '#000d1f',
        },
      },
      // font-sans (already used on <body>, see index.html) now resolves to
      // Mulish -- a free, geometric-humanist Google Font close in spirit to
      // Penn State's own print brand typeface (Proxima Nova, not itself
      // freely licensable for a self-hosted web app). font-display is a
      // new, opt-in utility for a stronger collegiate heading voice (Zilla
      // Slab, echoing the brand book's Serifa) -- not applied anywhere by
      // default, available for a future pass that wants it on headings.
      fontFamily: {
        sans: ['Mulish', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
        display: ['"Zilla Slab"', 'ui-serif', 'Georgia', 'serif'],
      },
    },
  },
  plugins: [],
};
