// Production build (`ng build`, deployed as a static site). A relative
// /api/... path has nothing to proxy to once this is deployed away from
// the dev server, so every request needs the real backend's own origin
// prefixed on — see docs/HOSTING_PLAN.md for where that backend lives.
//
// Replace with the real Render service URL once it exists (Render ->
// your service -> the URL shown at the top of its dashboard), then
// rebuild. No trailing slash.
export const environment = {
  production: true,
  apiBaseUrl: 'https://REPLACE-WITH-YOUR-RENDER-URL.onrender.com',
};
