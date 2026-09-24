// Dev build (ng serve / npm run dev). Empty apiBaseUrl means every request
// stays a relative /api/... path, handled by proxy.conf.json forwarding to
// the local Flask dev server — unchanged from before this file existed.
//
// supabaseUrl/supabaseAnonKey point at the same real Supabase project as
// prod (no separate dev project) -- the advisor-workspace subsystem talks
// to Supabase directly from the frontend, not through the Flask backend.
// The anon key is safe to embed here by design: access is enforced by
// Postgres Row Level Security, not by keeping this key secret (see
// supabase/migrations/0001_advisor_workspace.sql).
export const environment = {
  production: false,
  apiBaseUrl: '',
  supabaseUrl: 'https://rfyxmpzomwhftbahinqb.supabase.co',
  // Off until a Microsoft Entra app registration exists and Supabase's
  // Azure provider is configured with it (Dashboard -> Auth -> Providers).
  // The /secure-access page shows the button disabled until then.
  microsoftLoginEnabled: false,
  supabaseAnonKey: 'sb_publishable_vrFlKmKT6shEqWzMBpVN9A_Ta8w4ziu',
};
