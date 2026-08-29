// Production build (`ng build`, deployed as a static site). A relative
// /api/... path has nothing to proxy to once this is deployed away from
// the dev server, so every request needs the real backend's own origin
// prefixed on — see docs/HOSTING_PLAN.md for where that backend lives.
//
// Replace with the real Render service URL once it exists (Render ->
// your service -> the URL shown at the top of its dashboard), then
// rebuild. No trailing slash.
//
// supabaseUrl/supabaseAnonKey: the advisor-workspace subsystem (real
// accounts, review requests, comments, meeting proposals) talks to
// Supabase directly from the frontend, never through Flask -- see
// supabase/migrations/0001_advisor_workspace.sql for the schema/RLS this
// key is scoped by. The anon key is meant to be public; it's the Row
// Level Security policies in that migration that actually enforce access,
// not secrecy of this value.
export const environment = {
  production: true,
  apiBaseUrl: 'https://course-planner-pzdl.onrender.com',
  supabaseUrl: 'https://rfyxmpzomwhftbahinqb.supabase.co',
  supabaseAnonKey: 'sb_publishable_vrFlKmKT6shEqWzMBpVN9A_Ta8w4ziu',
};
