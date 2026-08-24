// Dev build (ng serve / npm run dev). Empty apiBaseUrl means every request
// stays a relative /api/... path, handled by proxy.conf.json forwarding to
// the local Flask dev server — unchanged from before this file existed.
export const environment = {
  production: false,
  apiBaseUrl: '',
};
