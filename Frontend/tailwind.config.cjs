/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{html,ts}'],
  // 'class' strategy (not the default 'media') so ThemeService's own
  // toggle controls dark mode directly, instead of only following the OS
  // preference -- mirrors the config the Play CDN script used to set
  // inline in index.html before this replaced it.
  darkMode: 'class',
  theme: {
    extend: {},
  },
  plugins: [],
};
